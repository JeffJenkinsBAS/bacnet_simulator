"""
Single-duct VAV box with hot-water reheat.

Point set and behavior per Phase 1 Addendum 3/4 (extended through VAV-17):

    damper_position_command  AO  WebCTRL -> sim   0-100 %
    hw_valve_command          AO  WebCTRL -> sim   0-100 %
    airflow_setpoint          AO  WebCTRL -> sim   cfm pressure-independent target
    airflow                   AV  sim -> WebCTRL   cfm
    heating_min_airflow       AV  sim -> WebCTRL   cfm read-only design value
    heating_max_airflow       AV  sim -> WebCTRL   cfm read-only design value
    cooling_min_airflow       AV  sim -> WebCTRL   cfm read-only design value
    cooling_max_airflow       AV  sim -> WebCTRL   cfm read-only design value
    damper_position_feedback  AV  sim -> WebCTRL   0-100 % effective position
    discharge_temp             AI  sim -> WebCTRL   deg F
    zone_temp                  AI  sim -> WebCTRL   deg F (virtual zones)
    zone_humidity              AI  sim -> WebCTRL   %RH (VAV-3 through VAV-15)

Zone Temp is intentionally NOT modeled here for VAV-1/VAV-2 — it comes from
real communicating ZS thermostats on the bench (Phase 1 Addendum 2, Q4). A
config flag lets VAV-3 through VAV-17 (which have no physical zone sensor) publish a
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
from math import sqrt

from app.equipment.base import EquipmentModel
from app.equipment.psychrometrics import humidity_ratio_from_rh
from app.equipment.zone import ZoneModel, ZoneParameters
from app.registry import PointRegistry


@dataclass
class VavParameters:
    """Instructor/commissioning-adjustable parameters for one VAV box."""

    max_airflow_cfm: float = 1200.0
    occupied_minimum_airflow_cfm: float = 300.0
    heating_maximum_airflow_cfm: float = 600.0
    closed_damper_leakage_cfm: float = 1.0
    min_airflow_floor_cfm: float = 25.0  # avoids divide-by-zero in the reheat model at near-zero flow
    design_static_pressure_inwc: float = 1.0  # static pressure at which max_airflow_cfm is achievable
    damper_time_constant_seconds: float = 8.0
    max_reheat_rise_f: float = 40.0  # rise at occupied minimum airflow and 100% valve
    thermal_time_constant_seconds: float = 30.0
    hot_water_supply_temp_f: float = 180.0  # standalone/unit-test fallback
    hot_water_valve_time_constant_seconds: float = 30.0
    hot_water_valve_rangeability: float = 20.0
    hot_water_design_delta_f: float = 20.0
    hot_water_design_dp_psi: float = 4.0
    hot_water_minimum_return_approach_f: float = 10.0
    maximum_discharge_temp_f: float = 95.0
    duct_pickup_f: float = 1.0
    zone_heating_setpoint_f: float = 70.0
    zone_cooling_setpoint_f: float = 72.0
    space_name: str = "Virtual zone"
    floor_area_sqft: float = 1000.0
    ceiling_height_ft: float = 10.0
    zone_thermal_capacitance_btuper_f: float = 10000.0
    zone_envelope_ua_btuh_per_f: float = 150.0
    zone_peak_solar_gain_btuh: float = 2000.0
    zone_solar_peak_hour: float = 14.0
    zone_internal_sensible_gain_btuh_per_sqft: float = 7.2
    zone_occupants_per_1000_sqft: float = 5.0
    zone_humidity_capacitance_multiplier: float = 12.0
    zone_initial_humidity_pct: float = 45.0
    zone_infiltration_ach_fan_on: float = 0.05
    zone_infiltration_ach_fan_off: float = 0.15
    zone_adjacent_mixing_cfm: float = 20.0
    airflow_animation_minimum_cfm: float = 50.0
    airflow_animation_fraction: float = 0.10
    conditioning_delta_f: float = 2.0
    conditioning_hysteresis_f: float = 1.0

    def __post_init__(self) -> None:
        positive_fields = (
            "max_airflow_cfm",
            "design_static_pressure_inwc",
            "damper_time_constant_seconds",
            "thermal_time_constant_seconds",
            "hot_water_valve_time_constant_seconds",
            "hot_water_valve_rangeability",
            "hot_water_design_delta_f",
            "hot_water_design_dp_psi",
            "floor_area_sqft",
            "ceiling_height_ft",
            "zone_thermal_capacitance_btuper_f",
            "zone_humidity_capacitance_multiplier",
        )
        for field_name in positive_fields:
            if float(getattr(self, field_name)) <= 0.0:
                raise ValueError(f"{field_name} must be greater than zero")

        if not (
            0.0
            <= self.occupied_minimum_airflow_cfm
            <= self.heating_maximum_airflow_cfm
            <= self.max_airflow_cfm
        ):
            raise ValueError(
                "airflow sizing must satisfy 0 <= occupied minimum <= "
                "heating maximum <= design maximum"
            )
        if not 0.0 <= self.zone_initial_humidity_pct <= 100.0:
            raise ValueError("zone_initial_humidity_pct must be between 0 and 100")
        if not 0.0 <= self.closed_damper_leakage_cfm <= 3.0:
            raise ValueError(
                "closed_damper_leakage_cfm must be between 0 and 3 CFM"
            )
        if self.conditioning_hysteresis_f < 0.0:
            raise ValueError("conditioning_hysteresis_f must not be negative")
        if self.conditioning_hysteresis_f > self.conditioning_delta_f:
            raise ValueError(
                "conditioning_hysteresis_f must not exceed conditioning_delta_f"
            )


class SingleDuctVavModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        parameters: VavParameters | None = None,
        has_physical_zone_sensor: bool = True,
        ahu_model=None,
        boiler_plant_model=None,
    ):
        super().__init__(equipment_id, registry)
        if parameters is not None:
            self.params = parameters
        elif hasattr(registry, "group_config"):
            self.params = VavParameters(**registry.group_config().model_parameters)
        else:
            self.params = VavParameters()
        self.has_physical_zone_sensor = has_physical_zone_sensor
        self.ahu_model = ahu_model  # AhuModel instance, Phase 3+; None falls back to the static values below
        self.boiler_plant_model = boiler_plant_model

        # Internal state not exposed on BACnet directly except through the
        # airflow/discharge_temp outputs.
        self._airflow_cfm = 0.0
        self._discharge_temp_f = 55.0
        self._air_mode = "off"
        self._hot_water_valve_fraction = 0.0
        self._hot_water_flow_gpm = 0.0
        self._hot_water_coil_load_btuh = 0.0
        self._hot_water_return_temp_f = self.params.hot_water_supply_temp_f
        point_aliases = set(self.registry.all_points())
        self._has_extended_airflow_points = {
            "heating_min_airflow",
            "heating_max_airflow",
            "cooling_min_airflow",
            "cooling_max_airflow",
            "damper_position_feedback",
        }.issubset(point_aliases)
        self._damper_position_feedback_pct = (
            float(self.registry.get("damper_position_feedback"))
            if "damper_position_feedback" in point_aliases
            else float(
                self.registry.get_commanded("damper_position_command") or 0.0
            )
        )
        # Every terminal needs a physical space state so its heat and moisture
        # loads can reach the common AHU return.  VAV-3..17 publish that state
        # as BACnet zone sensors.  VAV-1/2 are controlled by external physical
        # controllers, so their shadow zone remains internal until those
        # controllers expose a readable zone-temperature input.
        initial_zone_temp = (
            float(self.registry.get("zone_temp"))
            if "zone_temp" in point_aliases
            else self.params.zone_cooling_setpoint_f
        )
        initial_zone_rh = (
            float(self.registry.get("zone_humidity"))
            if "zone_humidity" in point_aliases
            else self.params.zone_initial_humidity_pct
        )
        self.zone_model = ZoneModel(
            ZoneParameters(
                space_name=self.params.space_name,
                floor_area_sqft=self.params.floor_area_sqft,
                ceiling_height_ft=self.params.ceiling_height_ft,
                thermal_capacitance_btuper_f=(
                    self.params.zone_thermal_capacitance_btuper_f
                ),
                envelope_ua_btuh_per_f=self.params.zone_envelope_ua_btuh_per_f,
                peak_solar_gain_btuh=self.params.zone_peak_solar_gain_btuh,
                solar_peak_hour=self.params.zone_solar_peak_hour,
                internal_sensible_gain_btuh_per_sqft=(
                    self.params.zone_internal_sensible_gain_btuh_per_sqft
                ),
                occupants_per_1000_sqft=self.params.zone_occupants_per_1000_sqft,
                humidity_capacitance_multiplier=(
                    self.params.zone_humidity_capacitance_multiplier
                ),
                initial_humidity_pct=initial_zone_rh,
                infiltration_ach_fan_on=self.params.zone_infiltration_ach_fan_on,
                infiltration_ach_fan_off=self.params.zone_infiltration_ach_fan_off,
                adjacent_mixing_cfm=self.params.zone_adjacent_mixing_cfm,
            ),
            initial_temp_f=initial_zone_temp,
            initial_humidity_pct=initial_zone_rh,
        )

        # Used only when ahu_model is None (Phase 2 standalone VAV testing,
        # or unit tests). Once wired to a real AhuModel these are ignored in
        # favor of the AHU's live computed values.
        self.ahu_supply_air_temp_f = 55.0
        self.available_static_pressure_inwc = 1.2

    @property
    def heating_capacity_fraction(self) -> float:
        if self.boiler_plant_model is None:
            return 1.0
        return float(self.boiler_plant_model.heating_capacity_fraction)

    @property
    def heating_available(self) -> bool:
        return self.heating_capacity_fraction >= 0.05

    @property
    def damper_position_feedback_pct(self) -> float:
        """Effective terminal-damper position used by the upstream duct model."""
        return max(0.0, min(100.0, self._damper_position_feedback_pct))

    @property
    def design_max_airflow_cfm(self) -> float:
        """Design weight used when aggregating terminal demand at AHU-1."""
        return self.params.max_airflow_cfm

    @property
    def airflow_cfm(self) -> float:
        """Actual primary airflow delivered by the parent AHU."""
        return max(0.0, self._airflow_cfm)

    @property
    def discharge_air_temp_f(self) -> float:
        return self._discharge_temp_f

    @property
    def return_air_temp_f(self) -> float:
        """Physical space temperature returned to the common return duct."""
        return self.zone_model.temperature_f

    @property
    def return_air_humidity_ratio(self) -> float:
        return self.zone_model.humidity_ratio

    @property
    def return_airflow_cfm(self) -> float:
        # Zone exhaust/transfer-air accounting can refine this later.  With
        # the current single-AHU topology, terminal primary flow is the best
        # conserved return-flow weight for each space.
        return self.airflow_cfm

    @property
    def hot_water_design_load_btuh(self) -> float:
        return (
            1.08
            * self.params.occupied_minimum_airflow_cfm
            * self.params.max_reheat_rise_f
        )

    @property
    def hot_water_design_flow_gpm(self) -> float:
        return self.hot_water_design_load_btuh / max(
            500.0 * self.params.hot_water_design_delta_f,
            1.0,
        )

    def hot_water_flow_at_pressure(self, differential_pressure_psi: float) -> float:
        """Two-way equal-percentage valve flow at the available coil DP."""
        if self._hot_water_valve_fraction <= 0.001:
            return 0.0
        rangeability = self.params.hot_water_valve_rangeability
        characteristic = (
            rangeability ** self._hot_water_valve_fraction - 1.0
        ) / (rangeability - 1.0)
        pressure_factor = sqrt(
            max(0.0, differential_pressure_psi)
            / self.params.hot_water_design_dp_psi
        )
        return self.hot_water_design_flow_gpm * characteristic * pressure_factor

    @property
    def hot_water_coil_load_btuh(self) -> float:
        return self._hot_water_coil_load_btuh

    def _zone_temperature_f(self) -> tuple[float, str]:
        source = (
            "simulated-zone-physical"
            if "zone_temp" in self.registry.all_points()
            else "simulated-zone-shadow"
        )
        return self.zone_model.temperature_f, source

    def _update_air_mode(self, active: bool, zone_temp_f: float, valve_pct: float) -> str:
        if not active:
            self._air_mode = "off"
            return self._air_mode

        delta_f = self._discharge_temp_f - zone_temp_f
        enter = self.params.conditioning_delta_f
        exit_threshold = max(0.0, enter - self.params.conditioning_hysteresis_f)
        cooling_source = (
            self.ahu_model is None
            or bool(getattr(self.ahu_model, "cooling_delivery_available", True))
        )

        if (
            self._air_mode == "cooling"
            and cooling_source
            and delta_f <= -exit_threshold
        ):
            return self._air_mode
        if (
            self._air_mode == "heating"
            and self.heating_available
            and valve_pct > 5.0
            and delta_f >= exit_threshold
        ):
            return self._air_mode
        if (
            self.heating_available
            and valve_pct > 5.0
            and delta_f >= enter
        ):
            self._air_mode = "heating"
        elif cooling_source and delta_f <= -enter:
            self._air_mode = "cooling"
        else:
            self._air_mode = "ventilation"
        return self._air_mode

    def operating_snapshot(self) -> dict:
        zone_temp_f, zone_source = self._zone_temperature_f()
        valve_pct = max(0.0, min(100.0, self.registry.get_commanded("hw_valve_command") or 0.0))
        damper_command_pct = max(
            0.0,
            min(100.0, self.registry.get("damper_position_command") or 0.0),
        )
        damper_feedback_pct = max(
            0.0,
            min(100.0, self._damper_position_feedback_pct),
        )
        airflow_setpoint = max(0.0, self.registry.get_commanded("airflow_setpoint") or 0.0)
        ahu_proven = (
            bool(getattr(self.ahu_model, "supply_air_available", False))
            if self.ahu_model is not None
            else self.available_static_pressure_inwc >= 0.05
        )
        animation_threshold = max(
            self.params.airflow_animation_minimum_cfm,
            self.params.airflow_animation_fraction * self.params.max_airflow_cfm,
        )
        active = ahu_proven and self._airflow_cfm >= animation_threshold
        mode = self._update_air_mode(active, zone_temp_f, valve_pct)
        conditioning_source = "neutral"
        if mode == "heating":
            conditioning_source = "hot-water-reheat"
        elif mode == "cooling":
            conditioning_source = (
                getattr(self.ahu_model, "conditioning_source", "mechanical-cooling")
                if self.ahu_model is not None
                else "mechanical-cooling"
            )
        sensible_btuh = 1.08 * self._airflow_cfm * (self._discharge_temp_f - zone_temp_f)
        snapshot = {
            "active": active,
            "mode": mode,
            "conditioning_source": conditioning_source,
            "airflow_cfm": round(self._airflow_cfm, 2),
            "airflow_setpoint_cfm": round(airflow_setpoint, 2),
            "airflow_fraction": round(
                max(0.0, min(1.0, self._airflow_cfm / max(self.params.max_airflow_cfm, 1.0))),
                3,
            ),
            "animation_threshold_cfm": round(animation_threshold, 2),
            "discharge_temp_f": round(self._discharge_temp_f, 2),
            "zone_temp_f": round(zone_temp_f, 2),
            "zone_temp_source": zone_source,
            "temperature_delta_f": round(self._discharge_temp_f - zone_temp_f, 2),
            "sensible_btuh": round(sensible_btuh, 1),
            # damper_pct is retained for existing UI/API clients and now
            # represents the effective simulated feedback position.
            "damper_pct": round(damper_feedback_pct, 2),
            "damper_command_pct": round(damper_command_pct, 2),
            "damper_position_feedback_pct": round(damper_feedback_pct, 2),
            "reheat_valve_pct": round(valve_pct, 2),
            "reheat_valve_effective_pct": round(100.0 * self._hot_water_valve_fraction, 2),
            "hot_water_flow_gpm": round(self._hot_water_flow_gpm, 3),
            "hot_water_coil_load_btuh": round(self._hot_water_coil_load_btuh, 1),
            "hot_water_return_temp_f": round(self._hot_water_return_temp_f, 2),
            "space_name": self.params.space_name,
            "zone_area_sqft": round(self.params.floor_area_sqft, 1),
            "design_max_airflow_cfm": round(self.params.max_airflow_cfm, 1),
            "occupied_minimum_airflow_cfm": round(
                self.params.occupied_minimum_airflow_cfm,
                1,
            ),
            "heating_minimum_airflow_cfm": round(
                self.params.occupied_minimum_airflow_cfm,
                1,
            ),
            "heating_maximum_airflow_cfm": round(
                self.params.heating_maximum_airflow_cfm,
                1,
            ),
            "cooling_minimum_airflow_cfm": round(
                self.params.occupied_minimum_airflow_cfm,
                1,
            ),
            "cooling_maximum_airflow_cfm": round(
                self.params.max_airflow_cfm,
                1,
            ),
            "closed_damper_leakage_cfm": round(
                self.params.closed_damper_leakage_cfm,
                2,
            ),
            "dependencies": {
                "ahu_proven": ahu_proven,
                "cooling_available": bool(
                    getattr(self.ahu_model, "cooling_delivery_available", False)
                ) if self.ahu_model is not None else True,
                "hot_water_available": self.heating_available,
            },
        }
        if "zone_temp" in self.registry.all_points():
            snapshot["zone_temp_indicated_f"] = round(
                float(self.registry.get("zone_temp")),
                2,
            )
        if "zone_humidity" in self.registry.all_points():
            snapshot["zone_humidity_pct"] = round(
                float(self.registry.get("zone_humidity")),
                2,
            )
        if self.zone_model is not None:
            snapshot["zone_loads"] = dict(self.zone_model.last_snapshot)
        return snapshot

    def tick(self, dt_seconds: float) -> None:
        damper_pct = self.registry.get_commanded("damper_position_command") or 0.0
        valve_pct = self.registry.get_commanded("hw_valve_command") or 0.0
        damper_pct = max(0.0, min(100.0, damper_pct))
        valve_pct = max(0.0, min(100.0, valve_pct))
        self._hot_water_valve_fraction = self.approach(
            self._hot_water_valve_fraction,
            valve_pct / 100.0,
            dt_seconds,
            self.params.hot_water_valve_time_constant_seconds,
        )
        # The BACnet AO is the controller request; blades and linkage require
        # time to stroke. The effective feedback is also what the parent duct
        # model sees, preserving the physical parent/child timing chain.
        self._damper_position_feedback_pct = self.approach(
            self._damper_position_feedback_pct,
            damper_pct,
            dt_seconds,
            self.params.damper_time_constant_seconds,
        )

        if self._has_extended_airflow_points:
            self.registry.set(
                "heating_min_airflow",
                self.params.occupied_minimum_airflow_cfm,
            )
            self.registry.set(
                "heating_max_airflow",
                self.params.heating_maximum_airflow_cfm,
            )
            self.registry.set(
                "cooling_min_airflow",
                self.params.occupied_minimum_airflow_cfm,
            )
            self.registry.set(
                "cooling_max_airflow",
                self.params.max_airflow_cfm,
            )
            self.registry.set(
                "damper_position_feedback",
                self._damper_position_feedback_pct,
            )

        available_static = (
            self.ahu_model.available_static_pressure_inwc if self.ahu_model else self.available_static_pressure_inwc
        )
        supply_air_temp = self.ahu_model.effective_sa_temp_f if self.ahu_model else self.ahu_supply_air_temp_f
        ahu_supply_proven = (
            bool(self.ahu_model.supply_air_available)
            if self.ahu_model is not None
            else available_static >= 0.05
        )

        # --- Airflow: pressure-independent setpoint, bounded by real damper capacity ---
        static_ratio = max(
            0.0,
            min(1.0, available_static / self.params.design_static_pressure_inwc),
        )
        effective_damper_pct = self.damper_position_feedback_pct
        damper_capacity = (
            (effective_damper_pct / 100.0)
            * self.params.max_airflow_cfm
            * sqrt(static_ratio)
        )
        airflow_setpoint = max(0.0, self.registry.get_commanded("airflow_setpoint") or 0.0)
        if not ahu_supply_proven:
            # A stopped/unproven AHU cannot deliver primary air. Force an
            # exact zero instead of leaving a decaying residual on BACnet.
            self._airflow_cfm = 0.0
        elif effective_damper_pct <= 0.05:
            # This point represents the effective damper position in the
            # training system. At 0% the blades are closed, so only a tiny,
            # configurable casing/blade leakage remains while the AHU is on.
            self._airflow_cfm = self.params.closed_damper_leakage_cfm
        else:
            target_airflow = (
                min(damper_capacity, airflow_setpoint)
                if airflow_setpoint > 1.0
                else damper_capacity
            )
            self._airflow_cfm = self.approach(
                self._airflow_cfm,
                target_airflow,
                dt_seconds,
                self.params.damper_time_constant_seconds,
            )

        # --- Discharge temp: actual AHU air plus plant-dependent terminal reheat ---
        zone_temp_f, _ = self._zone_temperature_f()
        if self.boiler_plant_model is not None:
            hot_water_temp = float(self.boiler_plant_model.supply_temp_f)
            hot_water_dp = float(
                getattr(
                    self.boiler_plant_model,
                    "differential_pressure_psi",
                    self.params.hot_water_design_dp_psi,
                )
            )
        else:
            hot_water_temp = self.params.hot_water_supply_temp_f
            hot_water_dp = self.params.hot_water_design_dp_psi
        self._hot_water_flow_gpm = (
            self.hot_water_flow_at_pressure(hot_water_dp)
            if self.heating_available
            else 0.0
        )
        self._hot_water_coil_load_btuh = 0.0
        self._hot_water_return_temp_f = hot_water_temp

        if self._airflow_cfm < self.params.min_airflow_floor_cfm:
            # With no meaningful air movement the sensor slowly equalizes with
            # the room/duct rather than reporting fictitious hot discharge.
            target_discharge_temp = zone_temp_f
        else:
            target_discharge_temp = supply_air_temp + self.params.duct_pickup_f
            if self._hot_water_flow_gpm > 0.0 and self.heating_available:
                effective_airflow = max(self._airflow_cfm, self.params.min_airflow_floor_cfm)
                entering_air_temp = target_discharge_temp
                water_side_capacity = (
                    500.0
                    * self._hot_water_flow_gpm
                    * max(
                        0.0,
                        hot_water_temp
                        - entering_air_temp
                        - self.params.hot_water_minimum_return_approach_f,
                    )
                )
                air_side_capacity = 1.08 * effective_airflow * max(
                    0.0,
                    min(hot_water_temp - 10.0, self.params.maximum_discharge_temp_f)
                    - entering_air_temp,
                )
                self._hot_water_coil_load_btuh = min(
                    self.hot_water_design_load_btuh
                    * self._hot_water_valve_fraction
                    * self.heating_capacity_fraction,
                    water_side_capacity,
                    air_side_capacity,
                )
                target_discharge_temp = min(
                    entering_air_temp
                    + self._hot_water_coil_load_btuh / max(1.08 * effective_airflow, 1.0),
                    hot_water_temp - 10.0,
                    self.params.maximum_discharge_temp_f,
                )
                self._hot_water_return_temp_f = hot_water_temp - (
                    self._hot_water_coil_load_btuh
                    / max(500.0 * self._hot_water_flow_gpm, 1.0)
                )
        self._discharge_temp_f = self.approach(
            self._discharge_temp_f, target_discharge_temp, dt_seconds, self.params.thermal_time_constant_seconds
        )

        self.registry.set("airflow", self._airflow_cfm)
        self.registry.set("discharge_temp", self._discharge_temp_f)

        # Virtual zones publish the result of a physical energy/moisture
        # balance. WebCTRL remains the controller; this model never writes
        # damper, valve, or airflow-setpoint commands.
        if self.zone_model is not None:
            current_zone = self.zone_model.temperature_f
            site_registry = getattr(self.ahu_model, "site_registry", None)
            oa_temp = (
                float(site_registry.get("oa_temp"))
                if site_registry is not None
                else self.params.zone_cooling_setpoint_f
            )
            oa_humidity = (
                float(site_registry.get("oa_humidity"))
                if site_registry is not None
                else 50.0
            )
            building_pressure = (
                float(site_registry.get("building_pressure"))
                if (
                    site_registry is not None
                    and "building_pressure" in site_registry.all_points()
                )
                else 0.0
            )
            supply_humidity_ratio = float(
                getattr(
                    self.ahu_model,
                    "supply_air_humidity_ratio",
                    humidity_ratio_from_rh(self._discharge_temp_f, 50.0),
                )
            )
            new_zone_temp, new_zone_rh = self.zone_model.tick(
                dt_seconds,
                zone_temp_f=current_zone,
                outdoor_temp_f=oa_temp,
                outdoor_humidity_pct=oa_humidity,
                supply_airflow_cfm=self._airflow_cfm,
                discharge_temp_f=self._discharge_temp_f,
                supply_humidity_ratio=supply_humidity_ratio,
                ahu_supply_proven=ahu_supply_proven,
                building_pressure_inwc=building_pressure,
            )
            if "zone_temp" in self.registry.all_points():
                self.registry.set("zone_temp", new_zone_temp)
            if "zone_humidity" in self.registry.all_points():
                self.registry.set("zone_humidity", new_zone_rh)

        self.runtime_seconds += dt_seconds
