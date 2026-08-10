import asyncio

import pytest

from app.engine import SimulationEngine


class CountingEquipment:
    equipment_id = "TEST-EQUIPMENT"

    def __init__(self) -> None:
        self.ticks = 0

    def tick(self, _dt: float) -> None:
        self.ticks += 1


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
