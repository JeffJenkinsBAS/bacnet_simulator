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


class SimulationEngine:
    def __init__(self, equipment: list[EquipmentModel], fault_manager=None, scenario_engine=None):
        self.equipment = equipment
        self.fault_manager = fault_manager
        self.scenario_engine = scenario_engine
        self.running = False
        self.speed_multiplier = 1.0
        self.simulated_seconds_elapsed = 0.0
        self._task: asyncio.Task | None = None
        self.tick_count = 0
        self.last_tick_wall_time: float | None = None

    async def _run_loop(self) -> None:
        logger.info("Simulation engine started")
        while self.running:
            start = time.monotonic()
            dt = TICK_INTERVAL_SECONDS * self.speed_multiplier

            if self.fault_manager is not None:
                self.fault_manager.tick(dt)
            if self.scenario_engine is not None:
                self.scenario_engine.tick(dt)

            for eq in self.equipment:
                try:
                    eq.tick(dt)
                except Exception:  # noqa: BLE001 - one bad equipment model must not kill the loop
                    logger.exception("Error ticking equipment '%s'", eq.equipment_id)
            self.simulated_seconds_elapsed += dt
            self.tick_count += 1
            self.last_tick_wall_time = time.time()
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, TICK_INTERVAL_SECONDS - elapsed))
        logger.info("Simulation engine stopped")

    def start(self) -> None:
        if self.running:
            return
        # Verify a loop exists BEFORE flipping the running flag: called from
        # a worker thread (a sync FastAPI endpoint), create_task raises and
        # the flag would claim a loop task that doesn't exist -- this exact
        # corruption happened live on the bench dev machine.
        asyncio.get_running_loop()
        self.running = True
        self._task = asyncio.create_task(self._run_loop())

    def stop(self) -> None:
        self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "speed_multiplier": self.speed_multiplier,
            "simulated_seconds_elapsed": round(self.simulated_seconds_elapsed, 1),
            "tick_count": self.tick_count,
            "equipment_count": len(self.equipment),
        }
