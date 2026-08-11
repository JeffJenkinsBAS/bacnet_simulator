"""
Simulation Engine.

Owns simulated time and drives every equipment model's tick() on a regular
interval. Phase 4: also ticks the FaultManager (advances drift-fault
accumulators) and the ScenarioEngine (fires timed events) once per loop,
both before the equipment models tick so a fault or scenario event
scheduled for "this tick" is visible to every equipment model immediately
rather than one tick late.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.equipment.base import EquipmentModel

logger = logging.getLogger("aci_sim.engine")

TICK_INTERVAL_SECONDS = 1.0
MAX_PHYSICS_STEP_SECONDS = 1.0


class SimulationEngine:
    def __init__(
        self,
        equipment: list[EquipmentModel],
        fault_manager=None,
        scenario_engine=None,
        diagnostics=None,
        training_manager=None,
    ):
        self.equipment = equipment
        self.fault_manager = fault_manager
        self.scenario_engine = scenario_engine
        self.diagnostics = diagnostics
        self.training_manager = training_manager
        self.running = False
        self.speed_multiplier = 1.0
        self.simulated_seconds_elapsed = 0.0
        self._task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self.tick_count = 0
        self.last_tick_wall_time: float | None = None

    def _advance_physics(self, dt_seconds: float) -> None:
        """Advance the coupled equipment graph with bounded causal steps.

        The UI time multiplier changes simulated time per wall-clock update,
        not the numerical integration interval. Keeping substeps at one
        simulated second prevents a 60x run from introducing a 60-second
        parent/child lag or skipping equipment proof and interlock timing.
        """
        remaining = max(0.0, float(dt_seconds))
        while remaining > 1e-9:
            step = min(MAX_PHYSICS_STEP_SECONDS, remaining)
            if self.fault_manager is not None:
                self.fault_manager.tick(step)
            if self.scenario_engine is not None:
                self.scenario_engine.tick(step)

            for eq in self.equipment:
                try:
                    eq.tick(step)
                except Exception:  # noqa: BLE001 - isolate one equipment failure
                    logger.exception("Error ticking equipment '%s'", eq.equipment_id)
            self.simulated_seconds_elapsed += step
            if self.training_manager is not None:
                try:
                    self.training_manager.tick(self.simulated_seconds_elapsed)
                except Exception:  # noqa: BLE001 - evidence capture must not stop physics
                    logger.exception("Error recording training-session evidence")
            remaining -= step

    async def _run_loop(self) -> None:
        logger.info("Simulation engine started")
        try:
            while self.running:
                start = time.monotonic()
                dt = TICK_INTERVAL_SECONDS * self.speed_multiplier

                self._advance_physics(dt)
                if self.diagnostics is not None:
                    try:
                        self.diagnostics.tick()
                    except Exception:  # noqa: BLE001 - diagnostics must not stop the simulation
                        logger.exception("Error refreshing command-center diagnostics")
                self.tick_count += 1
                self.last_tick_wall_time = time.time()
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.0, TICK_INTERVAL_SECONDS - elapsed))
        except asyncio.CancelledError:
            raise
        finally:
            logger.info("Simulation engine stopped")

    async def start(self) -> None:
        """Start exactly one tick loop, waiting for any stop in progress."""
        async with self._lifecycle_lock:
            if self._task is not None and not self._task.done():
                return
            self.running = True
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Cancel and await the exact loop task before reporting stopped."""
        async with self._lifecycle_lock:
            self.running = False
            task = self._task
            self._task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def reset_clock(self) -> None:
        """Reset simulated-time counters after the tick loop has stopped."""
        if self.running or (self._task is not None and not self._task.done()):
            raise RuntimeError("simulation clock can only be reset while stopped")
        self.simulated_seconds_elapsed = 0.0
        self.tick_count = 0
        self.last_tick_wall_time = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "speed_multiplier": self.speed_multiplier,
            "simulated_seconds_elapsed": round(self.simulated_seconds_elapsed, 1),
            "tick_count": self.tick_count,
            "equipment_count": len(self.equipment),
        }
