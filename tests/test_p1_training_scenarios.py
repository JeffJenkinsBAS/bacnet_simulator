from __future__ import annotations

import pytest

from app.main import build_application


P1_SCENARIOS = (
    "economizer_dual_enthalpy_transition",
    "economizer_damper_stuck",
    "chw_excessive_bypass_low_flow",
    "building_negative_pressure_recovery",
    "bacnet_priority3_conflict",
    "chw_pump_proof_loss_comfort_complaint",
)


@pytest.mark.asyncio
async def test_p1_scenarios_reach_all_calibrated_training_outcomes() -> None:
    api, transport, engine, _faults, scenarios = build_application()
    transport.registry.build_objects()
    training = api.state.training_manager
    failures: dict[str, list[str]] = {}

    for scenario_id in P1_SCENARIOS:
        scenarios.reset()
        await scenarios.drain_priority_writes()
        engine.equipment[:] = api.state.equipment_factory()
        engine.reset_clock()

        # A production session reaches this state through named-baseline
        # restore. This focused calibration test bypasses the extra settle
        # interval so it measures only each scenario's authored timeline.
        training.current_baseline_id = "p1-calibration"
        training.current_baseline_version = "1"
        training.baseline_settled = True
        training.reconciled_priority_mode = "none-needed"
        training.reconciled_priority_fingerprint = training._priority_fingerprint(
            training.priority_report()
        )

        training.start_session(scenario_id, "automated-calibration", 1)
        scenarios.start(scenario_id)
        duration = int(scenarios.scenarios[scenario_id].duration_seconds) + 2
        for _ in range(duration):
            engine._advance_physics(1.0)
            await scenarios.drain_priority_writes()

        summary = training.finish_session("completed")
        failed = [
            assertion["assertion_id"]
            for assertion in summary["assertions"]
            if assertion["status"] != "passed"
        ]
        if summary["score"] != 100.0 or failed:
            failures[scenario_id] = failed

    assert failures == {}
