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
from math import copysign

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class ExhaustFanParameters:
    proof_delay_seconds: float = 4.0
    pressure_time_constant_seconds: float = 8.0
    ahu_supply_pressure_inwc: float = 0.10
    max_exhaust_pressure_inwc: float = 0.11
    infiltration_pressure_inwc: float = 0.0
    fan_speed_time_constant_seconds: float = 6.0
    damper_time_constant_seconds: float = 15.0
    maximum_exhaust_airflow_cfm: float = 4000.0
    envelope_leakage_cfm_at_reference_pressure: float = 1800.0
    envelope_reference_pressure_inwc: float = 0.05


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
        self._proof_elapsed_seconds = 0.0
        self._speed_fraction = 0.0
        self._damper_fraction = 0.0
        self._exhaust_airflow_cfm = 0.0
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
        self._speed_fraction = self.approach(
            self._speed_fraction,
            vfd_pct / 100.0 if effective_cmd else 0.0,
            dt_seconds,
            self.params.fan_speed_time_constant_seconds,
        )
        self._damper_fraction = self.approach(
            self._damper_fraction,
            damper_pct / 100.0,
            dt_seconds,
            self.params.damper_time_constant_seconds,
        )
        if effective_cmd:
            self._proof_elapsed_seconds += max(0.0, dt_seconds)
        else:
            self._proof_elapsed_seconds = 0.0
        self._running = bool(
            effective_cmd
            and self._proof_elapsed_seconds >= self.params.proof_delay_seconds
            and self._speed_fraction >= 0.05
        )
        self._running_frac = 1.0 if self._running else 0.0
        self.registry.set("fan_status", 1.0 if self._running else 0.0)

        self._exhaust_airflow_cfm = (
            self.params.maximum_exhaust_airflow_cfm
            * self._speed_fraction
            * self._damper_fraction**0.5
            if self._running
            else 0.0
        )

        if self.site_registry is not None and "building_pressure" in self.site_registry.all_points():
            if (
                self.ahu_model is not None
                and hasattr(self.ahu_model, "outside_airflow_cfm")
            ):
                outdoor_airflow = (
                    float(self.ahu_model.outside_airflow_cfm)
                    if self.ahu_model.fan_running
                    else 0.0
                )
                imbalance_cfm = outdoor_airflow - self._exhaust_airflow_cfm
                leakage_cfm = max(
                    1.0,
                    self.params.envelope_leakage_cfm_at_reference_pressure,
                )
                target_pressure = copysign(
                    self.params.envelope_reference_pressure_inwc
                    * (abs(imbalance_cfm) / leakage_cfm) ** 2,
                    imbalance_cfm,
                )
                target_pressure += self.params.infiltration_pressure_inwc
            else:
                # Compatibility path for standalone and partial equipment
                # tests that expose fan proof but no airflow state.
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
                    self.params.infiltration_pressure_inwc
                    + supply_pressure
                    - exhaust_pressure
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

    @property
    def exhaust_airflow_cfm(self) -> float:
        return max(0.0, self._exhaust_airflow_cfm)
