import asyncio

import pytest

from app.engine import SimulationEngine


class CountingEquipment:
    equipment_id = "TEST-EQUIPMENT"

    def __init__(self) -> None:
        self.ticks = 0

    def tick(self, _dt: float) -> None:
        self.ticks += 1


class RecordingEquipment:
    equipment_id = "RECORDING-EQUIPMENT"

    def __init__(self) -> None:
        self.steps: list[float] = []

    def tick(self, dt: float) -> None:
        self.steps.append(dt)


class ClockRecordingScenario:
    def __init__(self) -> None:
        self.engine: SimulationEngine | None = None
        self.observed_times: list[float] = []

    def tick(self, _dt: float) -> None:
        assert self.engine is not None
        self.observed_times.append(self.engine.simulated_seconds_elapsed)


def test_accelerated_time_uses_bounded_physics_substeps() -> None:
    equipment = RecordingEquipment()
    engine = SimulationEngine([equipment])

    engine._advance_physics(60.0)

    assert len(equipment.steps) == 60
    assert sum(equipment.steps) == pytest.approx(60.0)
    assert max(equipment.steps) <= 1.0
    assert engine.simulated_seconds_elapsed == pytest.approx(60.0)


def test_fractional_time_rate_preserves_exact_elapsed_time() -> None:
    equipment = RecordingEquipment()
    engine = SimulationEngine([equipment])

    engine._advance_physics(2.5)

    assert equipment.steps == [1.0, 1.0, 0.5]
    assert engine.simulated_seconds_elapsed == pytest.approx(2.5)


def test_scenario_observes_each_substep_clock_boundary() -> None:
    scenario = ClockRecordingScenario()
    engine = SimulationEngine([], scenario_engine=scenario)
    scenario.engine = engine

    engine._advance_physics(4.0)

    assert scenario.observed_times == [0.0, 1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_rapid_stop_then_start_never_leaves_two_tick_loops() -> None:
    equipment = CountingEquipment()
    engine = SimulationEngine([equipment])

    await engine.start()
    first_task = engine._task
    assert first_task is not None

    stop_task = asyncio.create_task(engine.stop())
    start_task = asyncio.create_task(engine.start())
    await asyncio.gather(stop_task, start_task)

    assert engine.running is True
    assert engine._task is not None
    assert engine._task is not first_task
    assert first_task.done()

    await asyncio.sleep(0)
    assert equipment.ticks <= 2

    await engine.stop()
    assert engine.running is False
    assert engine._task is None
