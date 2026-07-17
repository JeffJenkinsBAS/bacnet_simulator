"""
Single-duct VAV box with hot-water reheat.

Point set and behavior per Phase 1 Addendum 3/4 (finalized for VAV-1/2/3/4/5):

    damper_position_command  AO  WebCTRL -> sim   0-100 %
    hw_valve_command          AO  WebCTRL -> sim   0-100 %
    airflow_setpoint          AO  WebCTRL -> sim   cfm (context only, not a hard override)
    airflow                   AV  sim -> WebCTRL   cfm
    discharge_temp             AI  sim -> WebCTRL   deg F

Zone Temp is intentionally NOT modeled here for VAV-1/VAV-2 — it comes from
real communicating ZS thermostats on the bench (Phase 1 Addendum 2, Q4). A
config flag lets VAV-3/4/5 (which have no physical zone sensor) publish a
simulated Zone Temp point instead; see `has_physical_zone_sensor` below.

Mechanical behavior implemented, matching the governing brief's VAV
expectations:
  - Airflow increases with damper position, but is capped by available duct
    static pressure relative to box design static (a fully open damper
    during low AHU static does not produce full-scale airflow).
  - Discharge air temperature responds to reheat valve position, the AHU's
    supply air temperature, and current airflow (lower airflow -> more
    temperature rise for the same valve position, since there's less air to
    absorb the reheat coil's heat output).
  - Both airflow and discharge temp move toward their target with a
    first-order lag rather than jumping instantly, so trends look like real
    equipment responding to a command, not a step function.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class VavParameters:
    """Instructor/commissioning-adjustable parameters for one VAV box."""

    max_airflow_cfm: float = 1200.0
    min_airflow_floor_cfm: float = 50.0  # avoids divide-by-zero in the reheat model at near-zero flow
    design_static_pressure_inwc: float = 1.0  # static pressure at which max_airflow_cfm is achievable
    damper_time_constant_seconds: float = 8.0
    max_reheat_rise_f: float = 40.0  # temperature rise at 100% valve, design airflow
    thermal_time_constant_seconds: float = 20.0
    hot_water_supply_temp_f: float = 140.0  # physical ceiling: discharge air can never exceed the HW loop temp


class SingleDuctVavModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        parameters: VavParameters | None = None,
        has_physical_zone_sensor: bool = True,
        ahu_model=None,
    ):
        super().__init__(equipment_id, registry)
        self.params = parameters or VavParameters()
        self.has_physical_zone_sensor = has_physical_zone_sensor
        self.ahu_model = ahu_model  # AhuModel instance, Phase 3+; None falls back to the static values below

        # Internal state not exposed on BACnet directly except through the
        # airflow/discharge_temp outputs.
        self._airflow_cfm = 0.0
        self._discharge_temp_f = 55.0

        # Used only when ahu_model is None (Phase 2 standalone VAV testing,
        # or unit tests). Once wired to a real AhuModel these are ignored in
        # favor of the AHU's live computed values.
        self.ahu_supply_air_temp_f = 55.0
        self.available_static_pressure_inwc = 1.2

    def tick(self, dt_seconds: float) -> None:
        damper_pct = self.registry.get_commanded("damper_position_command") or 0.0
        valve_pct = self.registry.get_commanded("hw_valve_command") or 0.0
        damper_pct = max(0.0, min(100.0, damper_pct))
        valve_pct = max(0.0, min(100.0, valve_pct))

        available_static = (
            self.ahu_model.available_static_pressure_inwc if self.ahu_model else self.available_static_pressure_inwc
        )
        supply_air_temp = self.ahu_model.effective_sa_temp_f if self.ahu_model else self.ahu_supply_air_temp_f

        # --- Airflow: damper position, capped by available static pressure ---
        static_ratio = min(1.0, available_static / self.params.design_static_pressure_inwc)
        target_airflow = (damper_pct / 100.0) * self.params.max_airflow_cfm * static_ratio
        self._airflow_cfm = self.approach(
            self._airflow_cfm, target_airflow, dt_seconds, self.params.damper_time_constant_seconds
        )

        # --- Discharge temp: reheat valve + AHU SA temp + airflow dilution ---
        effective_airflow = max(self._airflow_cfm, self.params.min_airflow_floor_cfm)
        dilution_factor = self.params.max_airflow_cfm / effective_airflow
        target_rise = (valve_pct / 100.0) * self.params.max_reheat_rise_f * dilution_factor
        # Clamp at the hot-water loop temperature: at minimum airflow (normal
        # heating mode) the unclamped dilution math targeted ~1,000 deg F,
        # which no reheat coil can do -- discharge asymptotes to HW temp.
        target_discharge_temp = min(supply_air_temp + target_rise, self.params.hot_water_supply_temp_f)
        self._discharge_temp_f = self.approach(
            self._discharge_temp_f, target_discharge_temp, dt_seconds, self.params.thermal_time_constant_seconds
        )

        self.registry.set("airflow", self._airflow_cfm)
        self.registry.set("discharge_temp", self._discharge_temp_f)

        # Virtual zones (no physical ZS thermostat) publish a simulated Zone Temp.
        if "zone_temp" in self.registry.all_points():
            # Simple space model: zone temp drifts toward a comfort setpoint,
            # pulled by discharge air relative to the zone based on airflow.
            zone_setpoint = 72.0
            pull = min(1.0, self._airflow_cfm / max(self.params.max_airflow_cfm, 1.0))
            target_zone = zone_setpoint + pull * (self._discharge_temp_f - zone_setpoint) * 0.15
            current_zone = self.registry.get("zone_temp")
            self.registry.set("zone_temp", self.approach(current_zone, target_zone, dt_seconds, 300.0))

        self.runtime_seconds += dt_seconds
