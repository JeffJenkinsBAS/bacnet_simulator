"""
Exhaust fan equipment model.

    exh_air_damper   AO  WebCTRL -> sim
    exh_fan_ss        BO  WebCTRL -> sim
    vfd_speed_command AO  WebCTRL -> sim, 0-100 %
    fan_status         BI  sim -> WebCTRL, after a proof delay

The fan is the building exhaust/relief trim. AHU supply creates positive
pressure; increasing exhaust-fan speed removes more air and reduces that
pressure. The occupied schedule remains a WebCTRL responsibility through
``exh_fan_ss``.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class ExhaustFanParameters:
    proof_delay_seconds: float = 4.0
    pressure_time_constant_seconds: float = 8.0
    ahu_supply_pressure_inwc: float = 0.10
    max_exhaust_pressure_inwc: float = 0.11
    infiltration_pressure_inwc: float = 0.0


class ExhaustFanModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        parameters: ExhaustFanParameters | None = None,
        *,
        site_registry: PointRegistry | None = None,
        ahu_model=None,
    ):
        super().__init__(equipment_id, registry)
        self.params = parameters or ExhaustFanParameters()
        self.site_registry = site_registry
        self.ahu_model = ahu_model
        self._running = False
        self._running_frac = 0.0
        self._building_pressure_inwc = 0.0

    def tick(self, dt_seconds: float) -> None:
        cmd = self.registry.get_commanded("exh_fan_ss") == 1.0
        try:
            damper_pct = self.registry.get_commanded("exh_air_damper")
        except KeyError:
            damper_pct = 100.0
        damper_pct = max(0.0, min(100.0, damper_pct if damper_pct is not None else 0.0))
        try:
            vfd_pct = self.registry.get_commanded("vfd_speed_command")
        except KeyError:
            vfd_pct = 100.0  # backwards compatibility for partial/unit-test configs
        vfd_pct = max(0.0, min(100.0, vfd_pct if vfd_pct is not None else 0.0))
        effective_cmd = cmd and vfd_pct >= 5.0
        self._running_frac = self.approach(
            self._running_frac,
            1.0 if effective_cmd else 0.0,
            dt_seconds,
            self.params.proof_delay_seconds,
        )
        self._running = self._running_frac > 0.5
        self.registry.set("fan_status", 1.0 if self._running else 0.0)

        if self.site_registry is not None and "building_pressure" in self.site_registry.all_points():
            supply_pressure = (
                self.params.ahu_supply_pressure_inwc
                if self.ahu_model is not None and self.ahu_model.fan_running
                else 0.0
            )
            exhaust_pressure = (
                self.params.max_exhaust_pressure_inwc
                * (vfd_pct / 100.0)
                * (damper_pct / 100.0)
                * self._running_frac
            )
            target_pressure = (
                self.params.infiltration_pressure_inwc + supply_pressure - exhaust_pressure
            )
            target_pressure = max(-0.25, min(0.25, target_pressure))
            self._building_pressure_inwc = self.approach(
                self._building_pressure_inwc,
                target_pressure,
                dt_seconds,
                self.params.pressure_time_constant_seconds,
            )
            self.site_registry.set("building_pressure", self._building_pressure_inwc)
        self.runtime_seconds += dt_seconds
