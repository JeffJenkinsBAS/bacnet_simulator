"""
AHU-1 equipment model.

Point set and behavior per the Phase 1 architecture (original doc's Mechanical
Behavior Expectations, plus the hard-interlock design in Phase 1 Addendum 2/3):

    cooling_valve, heating_valve, preheat_valve, economizer   AO  WebCTRL -> sim, 0-100%
    sa_temp_setpoint                                           AV  WebCTRL -> sim, 45-95 F
    ra_fan_ss, sa_fan_ss                                       BO  WebCTRL -> sim
    high_static_pressure_trip, freezestat_trip                  BV  WebCTRL -> sim, INTERLOCK
    ahu_ma_temp, ahu_ra_temp, ahu_ra_humidity, ahu_sa_temp       AI  sim -> WebCTRL
    ra_smoke_detector, sa_smoke_detector                          BI  sim -> WebCTRL (fault-library driven)
    ra_fan_status, sa_fan_status                                  BI  sim -> WebCTRL run proof

Interlocks are checked first, every tick, ahead of normal command
processing -- see Phase 1 Addendum 2 §5. While either trip is active, the
AHU forces itself into a fixed safe state regardless of what WebCTRL is
currently commanding, the way a real hardwired safety circuit would.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sin
from typing import Iterable

from app.equipment.base import EquipmentModel
from app.equipment.psychrometrics import (
    dew_point_f,
    dry_bulb_from_enthalpy_and_humidity_ratio,
    humidity_ratio_from_rh,
    moist_air_enthalpy_btu_per_lb,
    moist_air_enthalpy_from_humidity_ratio,
    rh_from_humidity_ratio,
)
from app.faults import FaultType
from app.registry import PointRegistry


@dataclass
class AhuParameters:
    fan_start_time_constant_seconds: float = 3.0
    economizer_time_constant_seconds: float = 15.0
    plenum_idle_time_constant_seconds: float = 300.0  # MA drift rate with fans off (no forced airflow)
    coil_time_constant_seconds: float = 45.0
    cooling_valve_time_constant_seconds: float = 60.0
    return_air_time_constant_seconds: float = 20.0
    cooling_coil_design_flow_gpm: float = 300.0
    cooling_coil_max_water_delta_f: float = 14.0
    heating_valve_time_constant_seconds: float = 45.0
    valve_overlap_threshold_pct: float = 10.0
    valve_changeover_grace_seconds: float = 60.0
    valve_change_rate_threshold_pct: float = 0.25
    space_time_constant_seconds: float = 120.0
    minimum_outdoor_air_fraction: float = 0.15
    economizer_enthalpy_enable_delta_btu_lb: float = -1.0
    economizer_enthalpy_disable_delta_btu_lb: float = 1.0
    economizer_oa_above_ra_enable_limit_f: float = 9.0
    economizer_oa_above_ra_disable_limit_f: float = 10.0
    economizer_fixed_high_limit_f: float = 75.0
    economizer_single_enthalpy_high_limit_btu_lb: float = 28.0
    economizer_dry_bulb_fallback_limit_f: float = 65.0
    economizer_dew_point_enable_limit_f: float = 55.0
    economizer_dew_point_disable_limit_f: float = 57.0
    economizer_mixed_air_low_limit_f: float = 45.0
    economizer_mixed_air_low_limit_release_f: float = 47.0
    economizer_cooling_benefit_deadband_f: float = 1.0
    economizer_integrated_proof_seconds: float = 180.0
    preheat_leaving_temp_f: float = 55.0
    cooling_coil_approach_f: float = 10.0
    # A characterized/equal-percentage hydronic valve offsets the convex
    # coil curve so installed heat output is approximately linear with the
    # actuator command. 20.5 F of design rise makes a 50% valve command
    # settle near 85 F SAT at normal 70-72 F mixed/return conditions.
    heating_coil_design_rise_f: float = 20.5
    fan_heat_f: float = 2.0
    minimum_sa_temp_setpoint_f: float = 45.0
    maximum_sa_temp_setpoint_f: float = 95.0
    sa_setpoint_mode_deadband_f: float = 1.0
    maximum_heating_supply_temp_f: float = 95.0
    design_duct_static_pressure_inwc: float = 1.2
    duct_static_default_setpoint_inwc: float = 1.0
    duct_static_minimum_setpoint_inwc: float = 0.25
    duct_static_maximum_setpoint_inwc: float = 2.0
    # The PID produces a conventional 0-100% speed signal. The physical VFD
    # is configured for 0-60 Hz with a 20 Hz run minimum, so a running drive
    # cannot operate below 33.33% physical speed even when the PID asks for
    # less. Keeping signal and physical limits separate makes saturation
    # visible in the training telemetry.
    fan_speed_signal_minimum_pct: float = 0.0
    vfd_minimum_frequency_hz: float = 20.0
    vfd_maximum_frequency_hz: float = 60.0
    fan_minimum_speed_pct: float = 100.0 / 3.0
    fan_maximum_speed_pct: float = 100.0
    fan_proof_minimum_speed_pct: float = 20.0
    fan_speed_time_constant_seconds: float = 8.0
    fan_full_speed_shutoff_pressure_inwc: float = 6.25
    terminal_relief_coefficient: float = 1.7
    terminal_leakage_conductance_fraction: float = 0.05
    duct_pressure_time_constant_seconds: float = 4.0
    duct_sensor_time_constant_seconds: float = 2.5
    duct_sensor_ripple_inwc: float = 0.003
    duct_model_maximum_pressure_inwc: float = 10.0
    high_static_trip_pressure_inwc: float = 4.0
    high_static_trip_delay_seconds: float = 1.0
    training_duct_failure_pressure_inwc: float = 5.0
    duct_failure_delay_seconds: float = 1.0
    duct_static_pid_kp: float = 30.0
    duct_static_pid_ki: float = 0.25
    duct_static_pid_kd: float = 0.0
    duct_static_pid_interval_seconds: float = 1.0
    duct_static_pid_bias_pct: float = 55.0
    duct_static_pid_deadband_inwc: float = 0.01
    duct_static_pid_derivative_filter_seconds: float = 5.0
    duct_static_pid_output_slew_pct_per_second: float = 3.0
    ra_setpoint_f: float = 72.0
    ra_humidity_setpoint_pct: float = 50.0
    freezestat_trip_temp_f: float = 35.0
    freezestat_trip_delay_seconds: float = 10.0
    cooling_coil_freeze_temp_f: float = 32.0
    cooling_coil_no_flow_failure_seconds: float = 20.0 * 60.0
    cooling_coil_proven_flow_failure_seconds: float = 60.0 * 60.0
    cooling_coil_flow_valve_threshold_pct: float = 5.0


class AhuModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        site_registry: PointRegistry,
        parameters: AhuParameters | None = None,
        *,
        chw_plant_model=None,
        boiler_plant_model=None,
    ):
        super().__init__(equipment_id, registry)
        self.site_registry = site_registry
        self.params = parameters or AhuParameters()
        self.chw_plant_model = chw_plant_model
        self.boiler_plant_model = boiler_plant_model

        self.fan_running = False
        self._fan_running_frac = 0.0
        self._ma_temp = 60.0
        self._cooling_coil_entering_air_temp = 60.0
        self._ra_temp = self.params.ra_setpoint_f
        self._ra_humidity = self.params.ra_humidity_setpoint_pct
        self._ra_humidity_ratio = humidity_ratio_from_rh(
            self._ra_temp,
            self._ra_humidity,
        )
        self._sa_temp = 55.0
        self._mixed_air_humidity_ratio = humidity_ratio_from_rh(
            self._ma_temp,
            self._ra_humidity,
        )
        self._supply_air_humidity_ratio = humidity_ratio_from_rh(
            self._sa_temp,
            self._ra_humidity,
        )
        self._conditioning_source = "off"
        self._mechanical_cooling_active = False
        self._economizer_cooling_active = False
        self._heating_active = False
        self._sa_temp_setpoint_f = 55.0
        self._requested_conditioning = "cooling"
        self._outside_air_fraction = 0.0
        self._economizer_requested_pct = 0.0
        self._economizer_effective_pct = 0.0
        self._economizer_state = "off"
        self._economizer_suitability_method = "dual-enthalpy"
        self._economizer_free_cooling_available = False
        self._economizer_cooling_beneficial = False
        self._economizer_oa_enthalpy_btu_lb = 0.0
        self._economizer_ra_enthalpy_btu_lb = 0.0
        self._economizer_enthalpy_delta_btu_lb = 0.0
        self._economizer_oa_dew_point_f = 0.0
        self._economizer_mixed_air_low_limit_active = False
        self._economizer_sensor_fallback_reason = ""
        self._economizer_limiting_reason = "fan-off"
        self._economizer_full_open_seconds = 0.0
        self._economizer_integrated_cooling_allowed = False
        self._economizer_fdd_flags: list[str] = []
        self._cooling_valve_command_pct = 0.0
        self._heating_valve_command_pct = 0.0
        self._cooling_valve_fraction = 0.0
        self._heating_valve_fraction = 0.0
        self._previous_cooling_valve_command_pct = 0.0
        self._previous_heating_valve_command_pct = 0.0
        self._cooling_valve_closing_remaining_seconds = 0.0
        self._heating_valve_closing_remaining_seconds = 0.0
        self._valve_changeover_remaining_seconds = 0.0
        self._valve_changeover_active = False
        self._simultaneous_heating_cooling = False
        self._valve_overlap_pct = 0.0
        self._vav_models: list = []
        self._sa_fan_commanded = False
        self._duct_static_pressure_setpoint_inwc = (
            self.params.duct_static_default_setpoint_inwc
        )
        self._duct_static_pressure_physical_inwc = 0.0
        self._duct_static_pressure_sensed_inwc = 0.0
        self._sa_fan_speed_feedback_pct = 0.0
        self._sa_fan_vfd_frequency_hz = 0.0
        self._aggregate_vav_conductance = (
            self.params.terminal_leakage_conductance_fraction
        )
        self._aggregate_vav_damper_pct = 0.0
        self._pid_kp = self.params.duct_static_pid_kp
        self._pid_ki = self.params.duct_static_pid_ki
        self._pid_kd = self.params.duct_static_pid_kd
        self._pid_interval_seconds = self.params.duct_static_pid_interval_seconds
        self._pid_integral = 0.0
        self._pid_previous_measurement: float | None = None
        self._pid_derivative_filtered = 0.0
        self._pid_elapsed_seconds = 0.0
        self._pid_output_pct = 0.0
        self._pid_active = False
        self._duct_static_history: deque[dict] = deque(maxlen=900)
        self._history_elapsed_seconds = 0.0
        self._high_static_exposure_seconds = 0.0
        self._duct_failure_exposure_seconds = 0.0
        self._automatic_high_static_trip = False
        self._duct_structural_failure = False
        self._freezestat_exposure_seconds = 0.0
        self._automatic_freezestat_trip = False
        self._cooling_coil_freeze_condition = False
        self._cooling_coil_freeze_hazard_dose = 0.0
        self._cooling_coil_rupture_flood = False
        self._total_supply_airflow_cfm = 0.0
        self._cooling_coil_load_btuh = 0.0
        self._cooling_coil_chw_flow_gpm = 0.0
        self._cooling_coil_chwr_temp_f = 55.0
        point_aliases = set(self.registry.all_points())
        self._has_duct_static_points = {
            "duct_static_pressure_setpoint",
            "duct_static_pressure",
            "sa_fan_speed_feedback",
        }.issubset(point_aliases)
        self._available_point_aliases = point_aliases

        # Exposed to other equipment models (VAV boxes) via direct in-process
        # reference, not through BACnet -- see SingleDuctVavModel(ahu_model=...).
        self.available_static_pressure_inwc = 0.0

    def set_vav_models(self, vav_models: Iterable) -> None:
        """Attach downstream terminals after the equipment graph is constructed."""
        self._vav_models = list(vav_models)

    def configure_duct_static_pid(
        self,
        *,
        kp: float,
        ki: float,
        kd: float,
        interval_seconds: float,
    ) -> None:
        limits = {
            "kp": (kp, 0.0, 100.0),
            "ki": (ki, 0.0, 1.0),
            "kd": (kd, 0.0, 20.0),
            "interval_seconds": (interval_seconds, 0.5, 10.0),
        }
        for name, (value, minimum, maximum) in limits.items():
            if not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
        self._pid_kp = float(kp)
        self._pid_ki = float(ki)
        self._pid_kd = float(kd)
        self._pid_interval_seconds = float(interval_seconds)
        self.reset_duct_static_pid(clear_history=False)

    def restore_duct_static_pid_defaults(self) -> None:
        self._pid_kp = self.params.duct_static_pid_kp
        self._pid_ki = self.params.duct_static_pid_ki
        self._pid_kd = self.params.duct_static_pid_kd
        self._pid_interval_seconds = self.params.duct_static_pid_interval_seconds
        self.reset_duct_static_pid(clear_history=False)

    def reset_duct_static_pid(self, *, clear_history: bool = False) -> None:
        """Clear controller memory while preserving the selected tuning."""
        self._pid_integral = 0.0
        self._pid_previous_measurement = None
        self._pid_derivative_filtered = 0.0
        self._pid_elapsed_seconds = 0.0
        self._pid_output_pct = (
            max(
                self.params.fan_minimum_speed_pct,
                self._sa_fan_speed_feedback_pct,
            )
            if self._pid_active
            else 0.0
        )
        if clear_history:
            self._duct_static_history.clear()
            self._history_elapsed_seconds = 0.0

    @property
    def effective_sa_temp_f(self) -> float:
        return self._sa_temp

    @property
    def supply_air_temp_setpoint_f(self) -> float:
        return self._sa_temp_setpoint_f

    @property
    def supply_air_available(self) -> bool:
        return self.fan_running and self.available_static_pressure_inwc >= 0.05

    @property
    def supply_air_humidity_ratio(self) -> float:
        """Live supply moisture content, unaffected by sensible reheat."""
        return self._supply_air_humidity_ratio

    @property
    def total_supply_airflow_cfm(self) -> float:
        return self._total_supply_airflow_cfm

    @property
    def cooling_coil_load_btuh(self) -> float:
        """Total sensible plus latent heat transferred into chilled water."""
        return self._cooling_coil_load_btuh

    @property
    def cooling_coil_chw_flow_gpm(self) -> float:
        return self._cooling_coil_chw_flow_gpm

    @property
    def cooling_coil_chwr_temp_f(self) -> float:
        return self._cooling_coil_chwr_temp_f

    @property
    def cooling_capacity_fraction(self) -> float:
        if self.chw_plant_model is None:
            return 1.0
        return float(self.chw_plant_model.cooling_capacity_fraction)

    @property
    def heating_capacity_fraction(self) -> float:
        if self.boiler_plant_model is None:
            return 1.0
        return float(self.boiler_plant_model.heating_capacity_fraction)

    @property
    def mechanical_cooling_available(self) -> bool:
        return self.cooling_capacity_fraction >= 0.05

    @property
    def hot_water_available(self) -> bool:
        return self.heating_capacity_fraction >= 0.05

    @property
    def cooling_delivery_available(self) -> bool:
        return self._mechanical_cooling_active or self._economizer_cooling_active

    @property
    def conditioning_source(self) -> str:
        return self._conditioning_source

    def _safety_bypassed(self, alias: str) -> bool:
        return self.registry.has_point_fault(alias, FaultType.safety_bypass)

    @staticmethod
    def _point_sensor_reliable(registry, alias: str) -> bool:
        try:
            registry.get(alias)
        except KeyError:
            return False
        return not registry.has_point_fault(alias, FaultType.reliability_fail)

    def _update_economizer(
        self,
        dt_seconds: float,
        *,
        requested_pct: float,
        oa_temp_f: float,
        oa_humidity_pct: float,
        cooling_command_pct: float,
        safety_shutdown: bool,
    ) -> float:
        """Resolve weather suitability and the physically effective OA stroke.

        AO23 remains the WebCTRL request. The simulated unit controller
        publishes that request separately and limits the physical damper to
        minimum ventilation when weather is unsuitable, or fully closed for
        fan-off/safety/mixed-air low-limit states.
        """
        self._economizer_requested_pct = max(
            0.0,
            min(100.0, float(requested_pct)),
        )
        oa_temp_reliable = self._point_sensor_reliable(
            self.site_registry,
            "oa_temp",
        )
        oa_humidity_reliable = self._point_sensor_reliable(
            self.site_registry,
            "oa_humidity",
        )
        ra_temp_reliable = self._point_sensor_reliable(
            self.registry,
            "ahu_ra_temp",
        )
        ra_humidity_reliable = self._point_sensor_reliable(
            self.registry,
            "ahu_ra_humidity",
        )

        self._economizer_oa_enthalpy_btu_lb = (
            moist_air_enthalpy_btu_per_lb(oa_temp_f, oa_humidity_pct)
        )
        self._economizer_ra_enthalpy_btu_lb = (
            moist_air_enthalpy_btu_per_lb(
                self._ra_temp,
                self._ra_humidity,
            )
        )
        self._economizer_enthalpy_delta_btu_lb = (
            self._economizer_oa_enthalpy_btu_lb
            - self._economizer_ra_enthalpy_btu_lb
        )
        self._economizer_oa_dew_point_f = dew_point_f(
            oa_temp_f,
            oa_humidity_pct,
        )

        available = self._economizer_free_cooling_available
        method = "unavailable"
        fallback_reason = ""
        if (
            oa_temp_reliable
            and oa_humidity_reliable
            and ra_temp_reliable
            and ra_humidity_reliable
        ):
            method = "dual-enthalpy"
            if available:
                available = not (
                    self._economizer_enthalpy_delta_btu_lb
                    >= self.params.economizer_enthalpy_disable_delta_btu_lb
                    or oa_temp_f
                    >= self._ra_temp
                    + self.params.economizer_oa_above_ra_disable_limit_f
                    or oa_temp_f > self.params.economizer_fixed_high_limit_f
                    or self._economizer_oa_dew_point_f
                    >= self.params.economizer_dew_point_disable_limit_f
                )
            else:
                available = (
                    self._economizer_enthalpy_delta_btu_lb
                    <= self.params.economizer_enthalpy_enable_delta_btu_lb
                    and oa_temp_f
                    <= self._ra_temp
                    + self.params.economizer_oa_above_ra_enable_limit_f
                    and oa_temp_f
                    <= self.params.economizer_fixed_high_limit_f
                    and self._economizer_oa_dew_point_f
                    <= self.params.economizer_dew_point_enable_limit_f
                )
        elif oa_temp_reliable and oa_humidity_reliable:
            method = "single-enthalpy"
            fallback_reason = "return-air enthalpy sensor unavailable"
            enable_limit = (
                self.params.economizer_single_enthalpy_high_limit_btu_lb - 1.0
            )
            disable_limit = (
                self.params.economizer_single_enthalpy_high_limit_btu_lb + 1.0
            )
            if available:
                available = not (
                    self._economizer_oa_enthalpy_btu_lb >= disable_limit
                    or oa_temp_f > self.params.economizer_fixed_high_limit_f
                    or self._economizer_oa_dew_point_f
                    >= self.params.economizer_dew_point_disable_limit_f
                )
            else:
                available = (
                    self._economizer_oa_enthalpy_btu_lb <= enable_limit
                    and oa_temp_f
                    <= self.params.economizer_fixed_high_limit_f
                    and self._economizer_oa_dew_point_f
                    <= self.params.economizer_dew_point_enable_limit_f
                )
        elif oa_temp_reliable:
            method = (
                "differential-dry-bulb"
                if ra_temp_reliable
                else "fixed-dry-bulb"
            )
            fallback_reason = "outdoor-air humidity sensor unavailable"
            if ra_temp_reliable:
                if available:
                    available = not (
                        oa_temp_f >= self._ra_temp + 1.0
                        or oa_temp_f
                        > self.params.economizer_dry_bulb_fallback_limit_f
                        + 2.0
                    )
                else:
                    available = (
                        oa_temp_f <= self._ra_temp - 1.0
                        and oa_temp_f
                        <= self.params.economizer_dry_bulb_fallback_limit_f
                    )
            elif available:
                available = (
                    oa_temp_f
                    < self.params.economizer_dry_bulb_fallback_limit_f + 2.0
                )
            else:
                available = (
                    oa_temp_f
                    <= self.params.economizer_dry_bulb_fallback_limit_f
                )
        else:
            available = False
            fallback_reason = "outdoor-air temperature sensor unavailable"

        self._economizer_suitability_method = method
        self._economizer_sensor_fallback_reason = fallback_reason
        self._economizer_free_cooling_available = available
        self._economizer_cooling_beneficial = bool(
            self.fan_running
            and (
                cooling_command_pct > 5.0
                or self._sa_temp
                > self._sa_temp_setpoint_f
                + self.params.economizer_cooling_benefit_deadband_f
            )
            and self._requested_conditioning != "heating"
        )

        if self._economizer_mixed_air_low_limit_active:
            if self._ma_temp >= (
                self.params.economizer_mixed_air_low_limit_release_f
            ):
                self._economizer_mixed_air_low_limit_active = False
        elif (
            self.fan_running
            and self._ma_temp < self.params.economizer_mixed_air_low_limit_f
        ):
            self._economizer_mixed_air_low_limit_active = True

        effective = self._economizer_requested_pct
        if not self.fan_running:
            effective = 0.0
            state = "off"
            limiting_reason = "fan-off"
        elif safety_shutdown:
            effective = 0.0
            state = "safety-shutdown"
            limiting_reason = "hard safety active"
        elif self._economizer_mixed_air_low_limit_active:
            effective = 0.0
            state = "mixed-air-low-limit"
            limiting_reason = "mixed air below 45 F"
        elif not available:
            effective = 0.0
            state = (
                "unavailable-sensor"
                if method == "unavailable"
                else "unavailable-weather"
            )
            limiting_reason = fallback_reason or "outdoor air not suitable"
        elif self._economizer_cooling_beneficial:
            state = "economizing"
            limiting_reason = ""
        else:
            state = "available-idle"
            limiting_reason = "no cooling benefit"

        # A stuck feedback fault represents a physical OA-damper actuator
        # failure, not merely a bad display value.  It therefore overrides
        # the controller's weather, mixed-air low-limit, and shutdown
        # commands.  The fan interlock still determines whether air actually
        # moves through the failed-open damper.
        stuck_damper = self.registry.point_fault_parameters(
            "economizer_damper_feedback",
            FaultType.stuck_value,
        )
        if stuck_damper is not None:
            effective = max(
                0.0,
                min(100.0, float(stuck_damper.get("value", effective))),
            )
            state = "actuator-stuck"
            limiting_reason = (
                f"OA damper physically stuck at {effective:.0f}%"
            )

        if (
            self.fan_running
            and effective >= 95.0
            and self._economizer_cooling_beneficial
            and self._sa_temp
            > self._sa_temp_setpoint_f
            + self.params.economizer_cooling_benefit_deadband_f
        ):
            self._economizer_full_open_seconds += max(
                0.0,
                float(dt_seconds),
            )
        else:
            self._economizer_full_open_seconds = 0.0
        self._economizer_integrated_cooling_allowed = (
            self._economizer_full_open_seconds
            >= self.params.economizer_integrated_proof_seconds
        )
        if (
            self._economizer_integrated_cooling_allowed
            and cooling_command_pct > 5.0
        ):
            state = "integrated-economizing"

        flags: list[str] = []
        if self._economizer_requested_pct > 5.0 and not available:
            flags.append("economizer-commanded-while-unavailable")
        if (
            available
            and self._economizer_cooling_beneficial
            and self._economizer_requested_pct <= 5.0
        ):
            flags.append("economizer-not-commanded-when-available")
        if (
            available
            and not self._economizer_cooling_beneficial
            and self._economizer_requested_pct > 5.0
        ):
            flags.append("excess-outdoor-air-without-cooling-need")
        if stuck_damper is not None:
            flags.append("economizer-damper-actuator-stuck")

        self._economizer_effective_pct = effective
        self._economizer_state = state
        self._economizer_limiting_reason = limiting_reason
        self._economizer_fdd_flags = flags
        return effective

    @property
    def chilled_water_flow_proven(self) -> bool:
        if (
            self._cooling_valve_fraction * 100.0
            < self.params.cooling_coil_flow_valve_threshold_pct
        ):
            return False
        if self.chw_plant_model is None:
            return self.cooling_capacity_fraction >= 0.05
        minimum_flow = float(
            getattr(
                getattr(self.chw_plant_model, "params", None),
                "minimum_usable_flow_gpm",
                0.0,
            )
        )
        return (
            float(getattr(self.chw_plant_model, "flow_gpm", 0.0))
            >= minimum_flow
        )

    def _update_freeze_safety(
        self,
        dt_seconds: float,
        *,
        automatic_bypassed: bool,
    ) -> None:
        """Advance low-temperature safeties on bounded simulated-time steps."""
        remaining = max(0.0, float(dt_seconds))
        while remaining > 1.0e-9:
            step = min(1.0, remaining)
            remaining -= step
            entering_temp = self._cooling_coil_entering_air_temp

            if (
                not self._automatic_freezestat_trip
                and not automatic_bypassed
                and entering_temp <= self.params.freezestat_trip_temp_f
            ):
                self._freezestat_exposure_seconds += step
                if (
                    self._freezestat_exposure_seconds
                    >= self.params.freezestat_trip_delay_seconds
                ):
                    self._automatic_freezestat_trip = True
            elif not self._automatic_freezestat_trip:
                self._freezestat_exposure_seconds = 0.0

            hazardous = (
                automatic_bypassed
                and entering_temp < self.params.cooling_coil_freeze_temp_f
                and not self._cooling_coil_rupture_flood
            )
            self._cooling_coil_freeze_condition = hazardous
            if hazardous:
                failure_seconds = (
                    self.params.cooling_coil_proven_flow_failure_seconds
                    if self.chilled_water_flow_proven
                    else self.params.cooling_coil_no_flow_failure_seconds
                )
                self._cooling_coil_freeze_hazard_dose += (
                    step / max(1.0, failure_seconds)
                )
                if self._cooling_coil_freeze_hazard_dose >= 1.0:
                    self._cooling_coil_freeze_hazard_dose = 1.0
                    self._cooling_coil_rupture_flood = True
                    self._cooling_coil_freeze_condition = True
            elif (
                not self._cooling_coil_rupture_flood
                and entering_temp >= self.params.cooling_coil_freeze_temp_f
            ):
                self._cooling_coil_freeze_hazard_dose = 0.0

        if self._cooling_coil_rupture_flood:
            self._cooling_coil_freeze_condition = True

    def _publish_if_present(self, alias: str, value: float) -> None:
        if alias in self._available_point_aliases:
            self.registry.set(alias, value)

    def _publish_safety_points(self) -> None:
        mixed_air_rh = rh_from_humidity_ratio(
            self._ma_temp,
            self._mixed_air_humidity_ratio,
        )
        supply_air_rh = rh_from_humidity_ratio(
            self._sa_temp,
            self._supply_air_humidity_ratio,
        )
        values = {
            "ahu_ma_humidity": mixed_air_rh,
            "ahu_sa_humidity": supply_air_rh,
            "cooling_coil_entering_air_temp": (
                self._cooling_coil_entering_air_temp
            ),
            "automatic_high_static_trip": (
                1.0 if self._automatic_high_static_trip else 0.0
            ),
            "duct_structural_failure": (
                1.0 if self._duct_structural_failure else 0.0
            ),
            "automatic_freezestat_trip": (
                1.0 if self._automatic_freezestat_trip else 0.0
            ),
            "cooling_coil_freeze_condition": (
                1.0 if self._cooling_coil_freeze_condition else 0.0
            ),
            "cooling_coil_rupture_flood": (
                1.0 if self._cooling_coil_rupture_flood else 0.0
            ),
        }
        for alias, value in values.items():
            self._publish_if_present(alias, value)

    def _aggregate_terminal_conductance(self) -> tuple[float, float]:
        weighted_position = 0.0
        total_design_cfm = 0.0
        for vav in self._vav_models:
            design_cfm = max(
                1.0,
                float(getattr(vav, "design_max_airflow_cfm", 1.0)),
            )
            damper_fraction = max(
                0.0,
                min(
                    1.0,
                    float(getattr(vav, "damper_position_feedback_pct", 0.0))
                    / 100.0,
                ),
            )
            weighted_position += design_cfm * damper_fraction
            total_design_cfm += design_cfm
        if total_design_cfm <= 0.0:
            average_damper_fraction = 0.5
        else:
            average_damper_fraction = weighted_position / total_design_cfm
        leakage = max(
            0.0,
            min(0.25, self.params.terminal_leakage_conductance_fraction),
        )
        conductance = leakage + ((1.0 - leakage) * average_damper_fraction)
        return conductance, average_damper_fraction * 100.0

    def _aggregate_return_air_state(self) -> tuple[float, float, float]:
        """Mass-weight downstream space states into the common return duct."""
        states: list[tuple[float, float, float, float]] = []
        for vav in self._vav_models:
            try:
                temp_f = float(vav.return_air_temp_f)
                humidity_ratio = float(vav.return_air_humidity_ratio)
            except (AttributeError, TypeError, ValueError):
                continue
            airflow_cfm = max(
                0.0,
                float(getattr(vav, "return_airflow_cfm", 0.0)),
            )
            fallback_weight = max(
                1.0,
                float(
                    getattr(
                        getattr(vav, "params", None),
                        "floor_area_sqft",
                        getattr(vav, "design_max_airflow_cfm", 1.0),
                    )
                ),
            )
            states.append((temp_f, humidity_ratio, airflow_cfm, fallback_weight))

        if not states:
            # Standalone AHU/unit-test mode has no downstream spaces to
            # aggregate. Preserve the legacy neutral-building surrogate;
            # fully wired systems never take this branch.
            surrogate_temp = (
                0.9 * self.params.ra_setpoint_f + 0.1 * self._sa_temp
                if self.fan_running
                else self.params.ra_setpoint_f
            )
            return (
                surrogate_temp,
                humidity_ratio_from_rh(
                    surrogate_temp,
                    self.params.ra_humidity_setpoint_pct,
                ),
                0.0,
            )

        total_airflow = sum(state[2] for state in states)
        use_actual_flow = total_airflow > 1.0
        weights = [
            state[2] if use_actual_flow else state[3]
            for state in states
        ]
        total_weight = max(1.0, sum(weights))
        return_humidity_ratio = sum(
            weight * state[1]
            for weight, state in zip(weights, states)
        ) / total_weight
        return_enthalpy = sum(
            weight
            * moist_air_enthalpy_from_humidity_ratio(state[0], state[1])
            for weight, state in zip(weights, states)
        ) / total_weight
        return_temp = dry_bulb_from_enthalpy_and_humidity_ratio(
            return_enthalpy,
            return_humidity_ratio,
        )
        return return_temp, return_humidity_ratio, total_airflow

    def _calculate_pid_output(self, measurement_inwc: float) -> float:
        interval = self._pid_interval_seconds
        error = self._duct_static_pressure_setpoint_inwc - measurement_inwc
        if abs(error) <= self.params.duct_static_pid_deadband_inwc:
            error = 0.0

        derivative = 0.0
        if self._pid_previous_measurement is not None:
            measurement_rate = (
                measurement_inwc - self._pid_previous_measurement
            ) / interval
            derivative = -measurement_rate
        self._pid_previous_measurement = measurement_inwc

        filter_seconds = max(
            interval,
            self.params.duct_static_pid_derivative_filter_seconds,
        )
        filter_alpha = interval / (filter_seconds + interval)
        self._pid_derivative_filtered += filter_alpha * (
            derivative - self._pid_derivative_filtered
        )

        integral_candidate = self._pid_integral + (
            self._pid_ki * error * interval
        )
        proportional = self._pid_kp * error
        derivative_term = self._pid_kd * self._pid_derivative_filtered
        raw_output = (
            self.params.duct_static_pid_bias_pct
            + proportional
            + integral_candidate
            + derivative_term
        )
        minimum = self.params.fan_speed_signal_minimum_pct
        maximum = self.params.fan_maximum_speed_pct
        saturated_high = raw_output > maximum and error > 0.0
        saturated_low = raw_output < minimum and error < 0.0
        if not saturated_high and not saturated_low:
            self._pid_integral = integral_candidate
        raw_output = (
            self.params.duct_static_pid_bias_pct
            + proportional
            + self._pid_integral
            + derivative_term
        )
        target = max(minimum, min(maximum, raw_output))
        max_change = (
            self.params.duct_static_pid_output_slew_pct_per_second * interval
        )
        return max(
            self._pid_output_pct - max_change,
            min(self._pid_output_pct + max_change, target),
        )

    def _vfd_frequency_for_signal(
        self,
        speed_signal_pct: float,
        *,
        run_command: bool,
    ) -> float:
        """Translate the PID signal into the drive's physical frequency."""
        if not run_command:
            return 0.0
        maximum_hz = max(0.0, self.params.vfd_maximum_frequency_hz)
        requested_hz = (
            max(0.0, min(100.0, speed_signal_pct))
            / 100.0
            * maximum_hz
        )
        return max(
            min(self.params.vfd_minimum_frequency_hz, maximum_hz),
            min(maximum_hz, requested_hz),
        )

    def _record_duct_static_history(
        self,
        dt_seconds: float,
        *,
        active: bool,
        sample_sim_seconds: float,
    ) -> None:
        self._history_elapsed_seconds += dt_seconds
        if self._history_elapsed_seconds < 1.0:
            return
        self._history_elapsed_seconds %= 1.0
        self._duct_static_history.append(
            {
                "sim_seconds": round(sample_sim_seconds, 1),
                "setpoint_inwc": round(
                    self._duct_static_pressure_setpoint_inwc if active else 0.0,
                    3,
                ),
                "actual_inwc": round(
                    self._duct_static_pressure_sensed_inwc if active else 0.0,
                    3,
                ),
                "fan_speed_pct": round(
                    self._sa_fan_speed_feedback_pct if active else 0.0,
                    2,
                ),
                "vfd_frequency_hz": round(
                    self._sa_fan_vfd_frequency_hz if active else 0.0,
                    2,
                ),
                "pid_output_pct": round(
                    self._pid_output_pct if active else 0.0,
                    2,
                ),
            }
        )

    def _update_duct_static(
        self,
        dt_seconds: float,
        sa_fan_cmd: bool,
        *,
        automatic_safety_bypassed: bool,
    ) -> None:
        if not self._has_duct_static_points:
            self._fan_running_frac = self.approach(
                self._fan_running_frac,
                1.0 if sa_fan_cmd else 0.0,
                dt_seconds,
                self.params.fan_start_time_constant_seconds,
            )
            self.fan_running = self._fan_running_frac > 0.5
            self._sa_fan_speed_feedback_pct = self._fan_running_frac * 100.0
            self._sa_fan_vfd_frequency_hz = (
                self._sa_fan_speed_feedback_pct
                / 100.0
                * self.params.vfd_maximum_frequency_hz
            )
            self.available_static_pressure_inwc = (
                self.params.design_duct_static_pressure_inwc
                * self._fan_running_frac
            )
            self._duct_static_pressure_physical_inwc = (
                self.available_static_pressure_inwc
            )
            self._duct_static_pressure_sensed_inwc = (
                self.available_static_pressure_inwc
            )
            return

        self._aggregate_vav_conductance, self._aggregate_vav_damper_pct = (
            self._aggregate_terminal_conductance()
        )

        if not sa_fan_cmd:
            self._fan_running_frac = 0.0
            self.fan_running = False
            self._pid_active = False
            self._sa_fan_speed_feedback_pct = 0.0
            self._sa_fan_vfd_frequency_hz = 0.0
            self._duct_static_pressure_physical_inwc = 0.0
            self._duct_static_pressure_sensed_inwc = 0.0
            self.available_static_pressure_inwc = 0.0
            self.reset_duct_static_pid(clear_history=False)
            self.registry.set("duct_static_pressure", 0.0)
            self.registry.set("sa_fan_speed_feedback", 0.0)
            self._record_duct_static_history(
                dt_seconds,
                active=False,
                sample_sim_seconds=self.runtime_seconds + dt_seconds,
            )
            return

        # The engine can advance by as much as 60 simulated seconds per tick.
        # Integrate the fan, controller, duct, and sensor together on a bounded
        # internal step so accelerated time cannot run dozens of PID
        # calculations against one stale pressure measurement.
        remaining = max(0.0, dt_seconds)
        elapsed = 0.0
        internal_step_limit = min(1.0, self._pid_interval_seconds)
        while remaining > 1.0e-9:
            step = min(internal_step_limit, remaining)
            elapsed += step
            remaining -= step

            self._fan_running_frac = self.approach(
                self._fan_running_frac,
                1.0,
                step,
                self.params.fan_start_time_constant_seconds,
            )
            if not self.fan_running:
                startup_target = max(
                    self.params.fan_minimum_speed_pct,
                    self.params.duct_static_pid_bias_pct,
                )
                self._sa_fan_speed_feedback_pct = self.approach(
                    self._sa_fan_speed_feedback_pct,
                    startup_target,
                    step,
                    self.params.fan_speed_time_constant_seconds,
                )
                self._sa_fan_speed_feedback_pct = max(
                    self.params.fan_minimum_speed_pct,
                    self._sa_fan_speed_feedback_pct,
                )
                self._sa_fan_vfd_frequency_hz = (
                    self._sa_fan_speed_feedback_pct
                    / 100.0
                    * self.params.vfd_maximum_frequency_hz
                )
                self.fan_running = (
                    self._fan_running_frac > 0.5
                    and self._sa_fan_speed_feedback_pct
                    >= self.params.fan_proof_minimum_speed_pct
                )

            self._pid_active = bool(self.fan_running)
            if not self._pid_active:
                self._duct_static_pressure_physical_inwc = 0.0
                self._duct_static_pressure_sensed_inwc = 0.0
                self.available_static_pressure_inwc = 0.0
                self.registry.set("duct_static_pressure", 0.0)
                self.registry.set(
                    "sa_fan_speed_feedback",
                    self._sa_fan_speed_feedback_pct,
                )
                self._record_duct_static_history(
                    step,
                    active=False,
                    sample_sim_seconds=self.runtime_seconds + elapsed,
                )
                continue

            measured_pressure = max(
                0.0,
                min(
                    self.params.duct_model_maximum_pressure_inwc,
                    float(self.registry.get("duct_static_pressure")),
                ),
            )
            self._pid_elapsed_seconds += step
            if self._pid_elapsed_seconds >= self._pid_interval_seconds:
                if self._pid_output_pct <= 0.0:
                    self._pid_output_pct = max(
                        self.params.fan_minimum_speed_pct,
                        self._sa_fan_speed_feedback_pct,
                    )
                self._pid_output_pct = self._calculate_pid_output(
                    measured_pressure
                )
                self._pid_elapsed_seconds %= self._pid_interval_seconds

            requested_frequency_hz = self._vfd_frequency_for_signal(
                self._pid_output_pct,
                run_command=True,
            )
            requested_physical_speed_pct = (
                requested_frequency_hz
                / max(1.0e-9, self.params.vfd_maximum_frequency_hz)
                * 100.0
            )
            self._sa_fan_speed_feedback_pct = self.approach(
                self._sa_fan_speed_feedback_pct,
                requested_physical_speed_pct,
                step,
                self.params.fan_speed_time_constant_seconds,
            )
            self._sa_fan_speed_feedback_pct = max(
                self.params.fan_minimum_speed_pct,
                self._sa_fan_speed_feedback_pct,
            )
            self._sa_fan_vfd_frequency_hz = (
                self._sa_fan_speed_feedback_pct
                / 100.0
                * self.params.vfd_maximum_frequency_hz
            )
            speed_fraction = max(
                0.0,
                min(1.0, self._sa_fan_speed_feedback_pct / 100.0),
            )
            pressure_equilibrium = (
                self.params.fan_full_speed_shutoff_pressure_inwc
                * speed_fraction**2
                / (
                    1.0
                    + self.params.terminal_relief_coefficient
                    * self._aggregate_vav_conductance**2
                )
            )
            self._duct_static_pressure_physical_inwc = self.approach(
                self._duct_static_pressure_physical_inwc,
                max(
                    0.0,
                    min(
                        self.params.duct_model_maximum_pressure_inwc,
                        pressure_equilibrium,
                    ),
                ),
                step,
                self.params.duct_pressure_time_constant_seconds,
            )

            if not self._duct_structural_failure:
                if (
                    not automatic_safety_bypassed
                    and self._duct_static_pressure_physical_inwc
                    >= self.params.high_static_trip_pressure_inwc
                ):
                    self._high_static_exposure_seconds += step
                    if (
                        self._high_static_exposure_seconds
                        >= self.params.high_static_trip_delay_seconds
                    ):
                        self._automatic_high_static_trip = True
                elif not self._automatic_high_static_trip:
                    self._high_static_exposure_seconds = 0.0

                if (
                    automatic_safety_bypassed
                    and self._duct_static_pressure_physical_inwc
                    > self.params.training_duct_failure_pressure_inwc
                ):
                    self._duct_failure_exposure_seconds += step
                    if (
                        self._duct_failure_exposure_seconds
                        >= self.params.duct_failure_delay_seconds
                    ):
                        self._duct_structural_failure = True
                else:
                    self._duct_failure_exposure_seconds = 0.0

            if self._automatic_high_static_trip:
                self._fan_running_frac = 0.0
                self.fan_running = False
                self._pid_active = False
                self._pid_output_pct = 0.0
                self._sa_fan_speed_feedback_pct = 0.0
                self._sa_fan_vfd_frequency_hz = 0.0
                self._duct_static_pressure_physical_inwc = 0.0
                self._duct_static_pressure_sensed_inwc = 0.0
                self.available_static_pressure_inwc = 0.0
                self.registry.set("duct_static_pressure", 0.0)
                self.registry.set("sa_fan_speed_feedback", 0.0)
                self._record_duct_static_history(
                    step,
                    active=False,
                    sample_sim_seconds=self.runtime_seconds + elapsed,
                )
                return

            if self._duct_structural_failure:
                # A gross rupture vents the trunk before the remote pickup.
                # The fan may continue to run against a lost-pressure signal
                # when the protective switch has deliberately been bypassed.
                self._duct_static_pressure_physical_inwc = 0.0
                self._duct_static_pressure_sensed_inwc = 0.0
                self.available_static_pressure_inwc = 0.0
                self.registry.set("duct_static_pressure", 0.0)
                self.registry.set(
                    "sa_fan_speed_feedback",
                    self._sa_fan_speed_feedback_pct,
                )
                self._record_duct_static_history(
                    step,
                    active=True,
                    sample_sim_seconds=self.runtime_seconds + elapsed,
                )
                continue

            sensed_target = self._duct_static_pressure_physical_inwc + (
                self.params.duct_sensor_ripple_inwc
                * sin((self.runtime_seconds + elapsed) * 0.7)
            )
            self._duct_static_pressure_sensed_inwc = self.approach(
                self._duct_static_pressure_sensed_inwc,
                max(
                    0.0,
                    min(
                        self.params.duct_model_maximum_pressure_inwc,
                        sensed_target,
                    ),
                ),
                step,
                self.params.duct_sensor_time_constant_seconds,
            )
            self.available_static_pressure_inwc = (
                self._duct_static_pressure_physical_inwc
            )
            self.registry.set(
                "duct_static_pressure",
                self._duct_static_pressure_sensed_inwc,
            )
            self.registry.set(
                "sa_fan_speed_feedback",
                self._sa_fan_speed_feedback_pct,
            )
            self._record_duct_static_history(
                step,
                active=True,
                sample_sim_seconds=self.runtime_seconds + elapsed,
            )

    def duct_static_snapshot(self) -> dict:
        actual = (
            float(self.registry.get("duct_static_pressure"))
            if self._has_duct_static_points
            else self._duct_static_pressure_sensed_inwc
        )
        default_tuning = {
            "kp": self.params.duct_static_pid_kp,
            "ki": self.params.duct_static_pid_ki,
            "kd": self.params.duct_static_pid_kd,
            "interval_seconds": self.params.duct_static_pid_interval_seconds,
        }
        current_tuning = {
            "kp": self._pid_kp,
            "ki": self._pid_ki,
            "kd": self._pid_kd,
            "interval_seconds": self._pid_interval_seconds,
        }
        error = (
            self._duct_static_pressure_setpoint_inwc - actual
            if self._pid_active
            else 0.0
        )
        return {
            "available": self._has_duct_static_points,
            "sensor_location": (
                "Conceptual two-thirds common-trunk training station on a "
                "straight section before the summarized VAV terminal bank"
            ),
            "fan_command": self._sa_fan_commanded,
            "fan_status": self.fan_running,
            "pid_active": self._pid_active,
            "setpoint_inwc": round(
                self._duct_static_pressure_setpoint_inwc,
                3,
            ),
            "actual_inwc": round(actual, 3),
            "physical_inwc": round(
                self._duct_static_pressure_physical_inwc,
                3,
            ),
            "error_inwc": round(error, 3),
            "fan_speed_pct": round(self._sa_fan_speed_feedback_pct, 2),
            "pid_output_pct": round(self._pid_output_pct, 2),
            "vfd_frequency_hz": round(
                self._sa_fan_vfd_frequency_hz,
                2,
            ),
            "vfd_requested_frequency_hz": round(
                (
                    max(0.0, min(100.0, self._pid_output_pct))
                    / 100.0
                    * self.params.vfd_maximum_frequency_hz
                )
                if self._sa_fan_commanded
                else 0.0,
                2,
            ),
            "vfd_minimum_frequency_hz": round(
                self.params.vfd_minimum_frequency_hz,
                2,
            ),
            "vfd_maximum_frequency_hz": round(
                self.params.vfd_maximum_frequency_hz,
                2,
            ),
            "vfd_minimum_speed_pct": round(
                self.params.fan_minimum_speed_pct,
                2,
            ),
            "aggregate_vav_damper_pct": round(
                self._aggregate_vav_damper_pct,
                2,
            ),
            "aggregate_conductance": round(
                self._aggregate_vav_conductance,
                4,
            ),
            "tuning": {
                **current_tuning,
                "defaults": default_tuning,
                "is_default": all(
                    abs(current_tuning[key] - default_tuning[key]) < 1.0e-9
                    for key in current_tuning
                ),
                "units": {
                    "kp": "% output / in. w.c.",
                    "ki": "% output / (in. w.c. x s)",
                    "kd": "% output x s / in. w.c.",
                    "interval": "simulated seconds",
                },
                "limits": {
                    "kp": [0.0, 100.0],
                    "ki": [0.0, 1.0],
                    "kd": [0.0, 20.0],
                    "interval_seconds": [0.5, 10.0],
                },
            },
            "history": list(self._duct_static_history),
            "safety": self.safety_snapshot(),
        }

    def safety_snapshot(self) -> dict:
        manual_high_static_trip = (
            self.registry.get_commanded("high_static_pressure_trip") == 1.0
        )
        manual_freezestat_trip = (
            self.registry.get_commanded("freezestat_trip") == 1.0
        )
        high_static_bypassed = self._safety_bypassed(
            "automatic_high_static_trip"
        )
        freezestat_bypassed = self._safety_bypassed(
            "automatic_freezestat_trip"
        )
        flow_proven = self.chilled_water_flow_proven
        freeze_limit = (
            self.params.cooling_coil_proven_flow_failure_seconds
            if flow_proven
            else self.params.cooling_coil_no_flow_failure_seconds
        )
        return {
            "manual_high_static_trip": manual_high_static_trip,
            "automatic_high_static_trip": self._automatic_high_static_trip,
            "high_static_safety_bypassed": high_static_bypassed,
            "high_static_trip_active": (
                manual_high_static_trip or self._automatic_high_static_trip
            ),
            "high_static_trip_threshold_inwc": (
                self.params.high_static_trip_pressure_inwc
            ),
            "high_static_trip_delay_seconds": (
                self.params.high_static_trip_delay_seconds
            ),
            "high_static_exposure_seconds": round(
                self._high_static_exposure_seconds,
                1,
            ),
            "duct_failure_limit_inwc": (
                self.params.training_duct_failure_pressure_inwc
            ),
            "duct_structural_failure": self._duct_structural_failure,
            "manual_freezestat_trip": manual_freezestat_trip,
            "automatic_freezestat_trip": self._automatic_freezestat_trip,
            "freezestat_safety_bypassed": freezestat_bypassed,
            "freezestat_trip_active": (
                manual_freezestat_trip or self._automatic_freezestat_trip
            ),
            "freezestat_trip_temp_f": self.params.freezestat_trip_temp_f,
            "freezestat_trip_delay_seconds": (
                self.params.freezestat_trip_delay_seconds
            ),
            "freezestat_exposure_seconds": round(
                self._freezestat_exposure_seconds,
                1,
            ),
            "cooling_coil_entering_air_temp_f": round(
                self._cooling_coil_entering_air_temp,
                2,
            ),
            "return_air_temp_f": round(self._ra_temp, 2),
            "return_air_humidity_pct": round(self._ra_humidity, 2),
            "total_supply_airflow_cfm": round(
                self._total_supply_airflow_cfm,
                1,
            ),
            "cooling_coil_load_btuh": round(
                self._cooling_coil_load_btuh,
                1,
            ),
            "cooling_coil_chw_flow_gpm": round(
                self._cooling_coil_chw_flow_gpm,
                2,
            ),
            "cooling_coil_chwr_temp_f": round(
                self._cooling_coil_chwr_temp_f,
                2,
            ),
            "cooling_coil_freeze_temp_f": (
                self.params.cooling_coil_freeze_temp_f
            ),
            "cooling_coil_freeze_condition": (
                self._cooling_coil_freeze_condition
            ),
            "cooling_coil_rupture_flood": (
                self._cooling_coil_rupture_flood
            ),
            "freeze_hazard_progress_pct": round(
                self._cooling_coil_freeze_hazard_dose * 100.0,
                2,
            ),
            "freeze_failure_limit_seconds": freeze_limit,
            "chilled_water_flow_proven": flow_proven,
            "flood_severity": (
                "continuing-chw-flow"
                if self._cooling_coil_rupture_flood and flow_proven
                else (
                    "localized-coil-water"
                    if self._cooling_coil_rupture_flood
                    else "none"
                )
            ),
            "latched_states_require_restart": True,
            "duct_failure_limit_basis": (
                "Configurable training pressure-class limit; actual duct "
                "capacity depends on construction and reinforcement"
            ),
        }

    def ahu_command_center_snapshot(self, history_limit: int = 180) -> dict:
        """Complete AHU payload while retaining the legacy PID contract."""
        payload = self.duct_static_snapshot()
        limit = max(1, min(900, int(history_limit)))
        payload["history"] = payload["history"][-limit:]
        return {
            **payload,
            "operation": self.operating_snapshot(),
            "sensors": {
                "outside_air_temp_f": round(
                    float(self.site_registry.get("oa_temp")),
                    2,
                ),
                "mixed_air_temp_f": round(self._ma_temp, 2),
                "mixed_air_humidity_pct": round(
                    rh_from_humidity_ratio(
                        self._ma_temp,
                        self._mixed_air_humidity_ratio,
                    ),
                    2,
                ),
                "return_air_temp_f": round(self._ra_temp, 2),
                "return_air_humidity_pct": round(self._ra_humidity, 2),
                "cooling_coil_entering_air_temp_f": round(
                    self._cooling_coil_entering_air_temp,
                    2,
                ),
                "supply_air_temp_f": round(self._sa_temp, 2),
                "supply_air_humidity_pct": round(
                    rh_from_humidity_ratio(
                        self._sa_temp,
                        self._supply_air_humidity_ratio,
                    ),
                    2,
                ),
            },
            "actuators": {
                "economizer_pct": round(
                    self._economizer_requested_pct,
                    2,
                ),
                "economizer_effective_pct": round(
                    self._economizer_effective_pct,
                    2,
                ),
                "preheat_valve_pct": round(
                    float(self.registry.get_commanded("preheat_valve") or 0.0),
                    2,
                ),
                "cooling_valve_pct": round(
                    self._cooling_valve_command_pct,
                    2,
                ),
                "reheat_valve_pct": round(
                    self._heating_valve_command_pct,
                    2,
                ),
            },
            "economizer": self.economizer_snapshot(),
        }

    def economizer_snapshot(self) -> dict:
        return {
            "requested_pct": round(self._economizer_requested_pct, 2),
            "effective_pct": round(self._economizer_effective_pct, 2),
            "outside_air_fraction": round(self._outside_air_fraction, 3),
            "state": self._economizer_state,
            "suitability_method": self._economizer_suitability_method,
            "free_cooling_available": (
                self._economizer_free_cooling_available
            ),
            "cooling_beneficial": self._economizer_cooling_beneficial,
            "oa_enthalpy_btu_lb": round(
                self._economizer_oa_enthalpy_btu_lb,
                2,
            ),
            "ra_enthalpy_btu_lb": round(
                self._economizer_ra_enthalpy_btu_lb,
                2,
            ),
            "enthalpy_delta_btu_lb": round(
                self._economizer_enthalpy_delta_btu_lb,
                2,
            ),
            "oa_dew_point_f": round(
                self._economizer_oa_dew_point_f,
                2,
            ),
            "mixed_air_low_limit_active": (
                self._economizer_mixed_air_low_limit_active
            ),
            "mixed_air_low_limit_f": (
                self.params.economizer_mixed_air_low_limit_f
            ),
            "mixed_air_low_limit_release_f": (
                self.params.economizer_mixed_air_low_limit_release_f
            ),
            "sensor_fallback_reason": (
                self._economizer_sensor_fallback_reason
            ),
            "limiting_reason": self._economizer_limiting_reason,
            "full_open_seconds": round(
                self._economizer_full_open_seconds,
                1,
            ),
            "integrated_cooling_allowed": (
                self._economizer_integrated_cooling_allowed
            ),
            "fdd_flags": list(self._economizer_fdd_flags),
        }

    def operating_snapshot(self) -> dict:
        return {
            "fan_proven": self.fan_running,
            "supply_air_available": self.supply_air_available,
            "return_air_temp_f": round(self._ra_temp, 2),
            "return_air_humidity_pct": round(self._ra_humidity, 2),
            "total_supply_airflow_cfm": round(
                self._total_supply_airflow_cfm,
                1,
            ),
            "cooling_coil_load_btuh": round(
                self._cooling_coil_load_btuh,
                1,
            ),
            "cooling_coil_chw_flow_gpm": round(
                self._cooling_coil_chw_flow_gpm,
                2,
            ),
            "cooling_coil_chwr_temp_f": round(
                self._cooling_coil_chwr_temp_f,
                2,
            ),
            "supply_air_temp_f": round(self._sa_temp, 2),
            "supply_air_humidity_pct": round(
                rh_from_humidity_ratio(
                    self._sa_temp,
                    self._supply_air_humidity_ratio,
                ),
                2,
            ),
            "supply_air_temp_setpoint_f": round(self._sa_temp_setpoint_f, 2),
            "supply_air_temp_error_f": round(
                self._sa_temp_setpoint_f - self._sa_temp,
                2,
            ),
            "requested_conditioning": self._requested_conditioning,
            "mixed_air_temp_f": round(self._ma_temp, 2),
            "mixed_air_humidity_pct": round(
                rh_from_humidity_ratio(
                    self._ma_temp,
                    self._mixed_air_humidity_ratio,
                ),
                2,
            ),
            "cooling_coil_entering_air_temp_f": round(
                self._cooling_coil_entering_air_temp,
                2,
            ),
            "outside_air_fraction": round(self._outside_air_fraction, 3),
            "economizer_requested_pct": round(
                self._economizer_requested_pct,
                2,
            ),
            "economizer_effective_pct": round(
                self._economizer_effective_pct,
                2,
            ),
            "economizer_state": self._economizer_state,
            "economizer_suitability_method": (
                self._economizer_suitability_method
            ),
            "free_cooling_available": (
                self._economizer_free_cooling_available
            ),
            "economizer_cooling_beneficial": (
                self._economizer_cooling_beneficial
            ),
            "economizer_oa_enthalpy_btu_lb": round(
                self._economizer_oa_enthalpy_btu_lb,
                2,
            ),
            "economizer_ra_enthalpy_btu_lb": round(
                self._economizer_ra_enthalpy_btu_lb,
                2,
            ),
            "economizer_oa_dew_point_f": round(
                self._economizer_oa_dew_point_f,
                2,
            ),
            "economizer_limiting_reason": (
                self._economizer_limiting_reason
            ),
            "economizer_fdd_flags": list(self._economizer_fdd_flags),
            "duct_static_pressure_inwc": round(self.available_static_pressure_inwc, 3),
            "duct_static_pressure_setpoint_inwc": round(
                self._duct_static_pressure_setpoint_inwc,
                3,
            ),
            "sa_fan_speed_feedback_pct": round(
                self._sa_fan_speed_feedback_pct,
                2,
            ),
            "sa_fan_vfd_frequency_hz": round(
                self._sa_fan_vfd_frequency_hz,
                2,
            ),
            "aggregate_vav_damper_pct": round(
                self._aggregate_vav_damper_pct,
                2,
            ),
            "duct_static_pid_active": self._pid_active,
            "mechanical_cooling_available": self.mechanical_cooling_available,
            "hot_water_available": self.hot_water_available,
            "mechanical_cooling_active": self._mechanical_cooling_active,
            "economizer_cooling_active": self._economizer_cooling_active,
            "heating_active": self._heating_active,
            "cooling_valve_command_pct": round(self._cooling_valve_command_pct, 2),
            "cooling_valve_effective_pct": round(
                self._cooling_valve_fraction * 100.0,
                2,
            ),
            "heating_valve_command_pct": round(self._heating_valve_command_pct, 2),
            "heating_valve_effective_pct": round(
                self._heating_valve_fraction * 100.0,
                2,
            ),
            "valve_overlap_pct": round(self._valve_overlap_pct, 2),
            "valve_changeover_active": self._valve_changeover_active,
            "valve_changeover_remaining_seconds": round(
                self._valve_changeover_remaining_seconds,
                1,
            ),
            "simultaneous_heating_cooling": self._simultaneous_heating_cooling,
            "conditioning_source": self._conditioning_source,
        }

    def tick(self, dt_seconds: float) -> None:
        # --- Interlocks first, ahead of any normal command processing ---
        manual_high_static_trip = (
            self.registry.get_commanded("high_static_pressure_trip") == 1.0
        )
        manual_freezestat_trip = (
            self.registry.get_commanded("freezestat_trip") == 1.0
        )
        automatic_high_static_bypassed = self._safety_bypassed(
            "automatic_high_static_trip"
        )
        automatic_freezestat_bypassed = self._safety_bypassed(
            "automatic_freezestat_trip"
        )
        self._update_freeze_safety(
            dt_seconds,
            automatic_bypassed=automatic_freezestat_bypassed,
        )
        high_static_trip = (
            manual_high_static_trip or self._automatic_high_static_trip
        )
        freezestat_trip = (
            manual_freezestat_trip or self._automatic_freezestat_trip
        )

        raw_sa_fan_cmd = self.registry.get_commanded("sa_fan_ss") == 1.0
        self._sa_fan_commanded = raw_sa_fan_cmd
        sa_fan_cmd = raw_sa_fan_cmd
        ra_fan_cmd = self.registry.get_commanded("ra_fan_ss") == 1.0
        cooling_pct = max(0.0, min(100.0, self.registry.get_commanded("cooling_valve") or 0.0))
        heating_pct = max(0.0, min(100.0, self.registry.get_commanded("heating_valve") or 0.0))
        preheat_pct = max(0.0, min(100.0, self.registry.get_commanded("preheat_valve") or 0.0))
        economizer_requested_pct = max(
            0.0,
            min(
                100.0,
                self.registry.get_commanded("economizer") or 0.0,
            ),
        )
        econ_pct = economizer_requested_pct
        try:
            sa_temp_setpoint = self.registry.get_commanded("sa_temp_setpoint")
        except KeyError:
            # Partial legacy test configurations may omit the new point.
            sa_temp_setpoint = 55.0
        if sa_temp_setpoint is None:
            sa_temp_setpoint = 55.0
        self._sa_temp_setpoint_f = max(
            self.params.minimum_sa_temp_setpoint_f,
            min(self.params.maximum_sa_temp_setpoint_f, float(sa_temp_setpoint)),
        )
        if self._has_duct_static_points:
            duct_static_setpoint = self.registry.get_commanded(
                "duct_static_pressure_setpoint"
            )
            if duct_static_setpoint is None:
                duct_static_setpoint = (
                    self.params.duct_static_default_setpoint_inwc
                )
            self._duct_static_pressure_setpoint_inwc = max(
                self.params.duct_static_minimum_setpoint_inwc,
                min(
                    self.params.duct_static_maximum_setpoint_inwc,
                    float(duct_static_setpoint),
                ),
            )

        if high_static_trip:
            # Manual and automatic high-static trips are hard shutdowns.
            sa_fan_cmd = False
            ra_fan_cmd = False
            cooling_pct = 0.0
            heating_pct = 0.0
            preheat_pct = 0.0
            econ_pct = 0.0
        if freezestat_trip:
            # Water-coil freeze response: stop all fans, close outdoor air,
            # fully circulate CHW, and drive the upstream preheat coil open.
            # This protective override supersedes a concurrent high-static
            # trip and is intentionally not treated as simultaneous heating
            # and cooling during normal temperature control.
            sa_fan_cmd = False
            ra_fan_cmd = False
            econ_pct = 0.0
            cooling_pct = 100.0
            preheat_pct = 100.0
            heating_pct = 0.0
        if not sa_fan_cmd:
            # OA dampers spring-return closed whenever the supply fan is off.
            econ_pct = 0.0

        cooling_delta = cooling_pct - self._previous_cooling_valve_command_pct
        heating_delta = heating_pct - self._previous_heating_valve_command_pct
        if cooling_delta < -self.params.valve_change_rate_threshold_pct:
            self._cooling_valve_closing_remaining_seconds = (
                self.params.valve_changeover_grace_seconds
            )
        else:
            self._cooling_valve_closing_remaining_seconds = max(
                0.0,
                self._cooling_valve_closing_remaining_seconds - dt_seconds,
            )
        if heating_delta < -self.params.valve_change_rate_threshold_pct:
            self._heating_valve_closing_remaining_seconds = (
                self.params.valve_changeover_grace_seconds
            )
        else:
            self._heating_valve_closing_remaining_seconds = max(
                0.0,
                self._heating_valve_closing_remaining_seconds - dt_seconds,
            )
        crossover = (
            cooling_delta < -self.params.valve_change_rate_threshold_pct
            and heating_pct > self.params.valve_overlap_threshold_pct
        ) or (
            heating_delta < -self.params.valve_change_rate_threshold_pct
            and cooling_pct > self.params.valve_overlap_threshold_pct
        ) or (
            heating_delta > self.params.valve_change_rate_threshold_pct
            and self._cooling_valve_closing_remaining_seconds > 0.0
        ) or (
            cooling_delta > self.params.valve_change_rate_threshold_pct
            and self._heating_valve_closing_remaining_seconds > 0.0
        )
        if crossover:
            # Allow one normal actuator-stroke interval for the outgoing valve
            # to close while the incoming valve begins opening. If both
            # commands remain materially open after this window, the condition
            # is no longer a handoff and is flagged as simultaneous conditioning.
            self._valve_changeover_remaining_seconds = (
                self.params.valve_changeover_grace_seconds
            )
        else:
            self._valve_changeover_remaining_seconds = max(
                0.0,
                self._valve_changeover_remaining_seconds - dt_seconds,
            )

        self._cooling_valve_command_pct = cooling_pct
        self._heating_valve_command_pct = heating_pct
        self._cooling_valve_fraction = self.approach(
            self._cooling_valve_fraction,
            cooling_pct / 100.0,
            dt_seconds,
            self.params.cooling_valve_time_constant_seconds,
        )
        self._heating_valve_fraction = self.approach(
            self._heating_valve_fraction,
            heating_pct / 100.0,
            dt_seconds,
            self.params.heating_valve_time_constant_seconds,
        )
        self._previous_cooling_valve_command_pct = cooling_pct
        self._previous_heating_valve_command_pct = heating_pct

        commanded_overlap = min(cooling_pct, heating_pct)
        effective_overlap = min(
            self._cooling_valve_fraction * 100.0,
            self._heating_valve_fraction * 100.0,
        )
        commands_materially_overlap = (
            commanded_overlap > self.params.valve_overlap_threshold_pct
        )
        self._valve_overlap_pct = commanded_overlap
        self._simultaneous_heating_cooling = (
            commands_materially_overlap
            and self._valve_changeover_remaining_seconds <= 0.0
        )
        self._valve_changeover_active = (
            not self._simultaneous_heating_cooling
            and effective_overlap > self.params.valve_overlap_threshold_pct
            and (
                self._valve_changeover_remaining_seconds > 0.0
                or not commands_materially_overlap
            )
        )

        self._update_duct_static(
            dt_seconds,
            sa_fan_cmd,
            automatic_safety_bypassed=automatic_high_static_bypassed,
        )
        if self._automatic_high_static_trip:
            ra_fan_cmd = False
            cooling_pct = 0.0
            heating_pct = 0.0
            preheat_pct = 0.0
            econ_pct = 0.0

        oa_temp = self.site_registry.get("oa_temp")
        try:
            oa_humidity = self.site_registry.get("oa_humidity")
        except KeyError:
            # Partial unit-test/legacy site configurations may publish only
            # outdoor temperature. Fifty percent RH is a neutral fallback.
            oa_humidity = 50.0

        # The downstream spaces are the source of common return conditions.
        # VAVs tick later in the equipment loop, so this intentionally uses
        # their completed prior-tick states (a one-second transport lag).
        (
            return_temp_target,
            return_humidity_ratio_target,
            self._total_supply_airflow_cfm,
        ) = self._aggregate_return_air_state()
        return_tc = (
            self.params.return_air_time_constant_seconds
            if self.fan_running
            else self.params.space_time_constant_seconds
        )
        self._ra_temp = self.approach(
            self._ra_temp,
            return_temp_target,
            dt_seconds,
            return_tc,
        )
        self._ra_humidity_ratio = self.approach(
            self._ra_humidity_ratio,
            return_humidity_ratio_target,
            dt_seconds,
            return_tc,
        )
        self._ra_humidity = rh_from_humidity_ratio(
            self._ra_temp,
            self._ra_humidity_ratio,
        )

        econ_pct = self._update_economizer(
            dt_seconds,
            requested_pct=economizer_requested_pct,
            oa_temp_f=float(oa_temp),
            oa_humidity_pct=float(oa_humidity),
            cooling_command_pct=cooling_pct,
            safety_shutdown=bool(
                manual_high_static_trip
                or self._automatic_high_static_trip
                or manual_freezestat_trip
                or self._automatic_freezestat_trip
            ),
        )

        # --- Mixed air temp: minimum ventilation + economizer OA/RA blend ---
        # A 0% economizer command means minimum occupied ventilation, not
        # literally zero outside air. The commanded economizer position resets
        # the remaining stroke between that minimum and 100% outdoor air.
        physical_oa_damper_stuck = (
            self.registry.point_fault_parameters(
                "economizer_damper_feedback",
                FaultType.stuck_value,
            )
            is not None
        )
        if (
            self.fan_running
            and self._economizer_mixed_air_low_limit_active
            and not physical_oa_damper_stuck
        ):
            # The mixed-air low limit is allowed to close below the normal
            # ventilation minimum to protect the coils; the freezestat remains
            # the final hard shutdown.
            self._outside_air_fraction = 0.0
        elif self.fan_running:
            minimum_oa = max(
                0.0,
                min(1.0, self.params.minimum_outdoor_air_fraction),
            )
            self._outside_air_fraction = minimum_oa + (
                (1.0 - minimum_oa) * (econ_pct / 100.0)
            )
        else:
            self._outside_air_fraction = 0.0
        target_ma = (
            self._outside_air_fraction * oa_temp
            + (1.0 - self._outside_air_fraction) * self._ra_temp
        )
        # No airflow -> no forced blend; the plenum slowly equalizes with the
        # building instead of responding at full economizer speed.
        ma_tc = (
            self.params.economizer_time_constant_seconds
            if self.fan_running
            else self.params.plenum_idle_time_constant_seconds
        )
        self._ma_temp = self.approach(self._ma_temp, target_ma, dt_seconds, ma_tc)
        target_mixed_air_ratio = (
            self._outside_air_fraction
            * humidity_ratio_from_rh(oa_temp, oa_humidity)
            + (1.0 - self._outside_air_fraction)
            * self._ra_humidity_ratio
        )
        self._mixed_air_humidity_ratio = self.approach(
            self._mixed_air_humidity_ratio,
            target_mixed_air_ratio,
            dt_seconds,
            ma_tc,
        )
        preheat_active = (
            preheat_pct > 0.0
            and self.heating_capacity_fraction > 0.0
            and self._ma_temp < self.params.preheat_leaving_temp_f
        )
        preheat_target = self._ma_temp
        if preheat_active:
            preheat_target = self._ma_temp + (
                (preheat_pct / 100.0)
                * self.heating_capacity_fraction
                * (self.params.preheat_leaving_temp_f - self._ma_temp)
            )
        self._cooling_coil_entering_air_temp = self.approach(
            self._cooling_coil_entering_air_temp,
            preheat_target,
            dt_seconds,
            self.params.coil_time_constant_seconds,
        )

        neutral_sa_target = (
            self._cooling_coil_entering_air_temp + self.params.fan_heat_f
        )
        if (
            self._sa_temp_setpoint_f
            < neutral_sa_target - self.params.sa_setpoint_mode_deadband_f
        ):
            self._requested_conditioning = "cooling"
        elif (
            self._sa_temp_setpoint_f
            > neutral_sa_target + self.params.sa_setpoint_mode_deadband_f
        ):
            self._requested_conditioning = "heating"
        else:
            self._requested_conditioning = "neutral"

        # --- Supply air temp: plant-dependent coils in their physical order ---
        self._mechanical_cooling_active = False
        self._economizer_cooling_active = False
        self._heating_active = False
        self._cooling_coil_load_btuh = 0.0
        self._cooling_coil_chw_flow_gpm = 0.0
        target_supply_humidity_ratio = self._mixed_air_humidity_ratio
        chw_temp = (
            float(self.chw_plant_model.supply_temp_f)
            if self.chw_plant_model is not None
            else 44.0
        )
        self._cooling_coil_chwr_temp_f = chw_temp
        if self.fan_running:
            target_sa = self._cooling_coil_entering_air_temp
            heating_capacity = self.heating_capacity_fraction
            cooling_capacity = self.cooling_capacity_fraction

            if preheat_active:
                self._heating_active = True

            if self.chw_plant_model is None:
                available_chw_flow = (
                    self.params.cooling_coil_design_flow_gpm
                    if cooling_capacity > 0.0
                    else 0.0
                )
            else:
                available_chw_flow = float(
                    getattr(
                        self.chw_plant_model,
                        "flow_gpm",
                        self.params.cooling_coil_design_flow_gpm
                        if cooling_capacity > 0.0
                        else 0.0,
                    )
                )
            self._cooling_coil_chw_flow_gpm = (
                min(
                    self.params.cooling_coil_design_flow_gpm,
                    max(0.0, available_chw_flow),
                )
                * self._cooling_valve_fraction
            )

            cooling_output = self._cooling_valve_fraction * cooling_capacity
            if cooling_output > 0.0:
                coil_floor = chw_temp + self.params.cooling_coil_approach_f
                if target_sa > coil_floor:
                    coil_enter_temp = target_sa
                    coil_enter_ratio = target_supply_humidity_ratio
                    ideal_coil_leaving_temp = target_sa - (
                        cooling_output * (target_sa - coil_floor)
                    )
                    ideal_coil_leaving_ratio = coil_enter_ratio
                    if ideal_coil_leaving_temp < coil_enter_temp - 1.0:
                        wet_coil_temp = max(
                            42.0,
                            min(55.0, ideal_coil_leaving_temp),
                        )
                        ideal_coil_leaving_ratio = min(
                            ideal_coil_leaving_ratio,
                            humidity_ratio_from_rh(wet_coil_temp, 92.0),
                        )

                    entering_enthalpy = moist_air_enthalpy_from_humidity_ratio(
                        coil_enter_temp,
                        coil_enter_ratio,
                    )
                    ideal_leaving_enthalpy = (
                        moist_air_enthalpy_from_humidity_ratio(
                            ideal_coil_leaving_temp,
                            ideal_coil_leaving_ratio,
                        )
                    )
                    requested_load_btuh = max(
                        0.0,
                        4.5
                        * self._total_supply_airflow_cfm
                        * (entering_enthalpy - ideal_leaving_enthalpy),
                    )
                    water_capacity_btuh = (
                        500.0
                        * self._cooling_coil_chw_flow_gpm
                        * self.params.cooling_coil_max_water_delta_f
                    )
                    capacity_scale = (
                        min(1.0, water_capacity_btuh / requested_load_btuh)
                        if requested_load_btuh > 0.0
                        else 1.0
                    )
                    target_sa = coil_enter_temp + capacity_scale * (
                        ideal_coil_leaving_temp - coil_enter_temp
                    )
                    target_supply_humidity_ratio = coil_enter_ratio + (
                        capacity_scale
                        * (ideal_coil_leaving_ratio - coil_enter_ratio)
                    )
                    leaving_enthalpy = moist_air_enthalpy_from_humidity_ratio(
                        target_sa,
                        target_supply_humidity_ratio,
                    )
                    self._cooling_coil_load_btuh = max(
                        0.0,
                        4.5
                        * self._total_supply_airflow_cfm
                        * (entering_enthalpy - leaving_enthalpy),
                    )
                    if self._cooling_coil_chw_flow_gpm > 0.0:
                        self._cooling_coil_chwr_temp_f = chw_temp + (
                            self._cooling_coil_load_btuh
                            / (500.0 * self._cooling_coil_chw_flow_gpm)
                        )
                    self._mechanical_cooling_active = (
                        target_sa
                        < self._cooling_coil_entering_air_temp - 1.0
                    )

            heating_output = self._heating_valve_fraction * heating_capacity
            if heating_output > 0.0:
                hot_water_temp = (
                    self.boiler_plant_model.supply_temp_f
                    if self.boiler_plant_model is not None
                    else 180.0
                )
                heating_limit = min(
                    self.params.maximum_heating_supply_temp_f,
                    hot_water_temp - 10.0,
                )
                target_sa = min(
                    target_sa
                    + heating_output * self.params.heating_coil_design_rise_f,
                    heating_limit,
                )
                self._heating_active = (
                    heating_output * self.params.heating_coil_design_rise_f
                    >= 1.0
                )

            # Fan heat is added after the coils.
            target_sa += self.params.fan_heat_f
            if self._heating_active:
                target_sa = min(
                    target_sa,
                    self.params.maximum_heating_supply_temp_f,
                )
            self._economizer_cooling_active = (
                self._economizer_free_cooling_available
                and econ_pct > 5.0
                and self._economizer_cooling_beneficial
                and self._ma_temp < self._ra_temp - 1.0
            )
            self._sa_temp = self.approach(self._sa_temp, target_sa, dt_seconds, self.params.coil_time_constant_seconds)
            self._supply_air_humidity_ratio = self.approach(
                self._supply_air_humidity_ratio,
                target_supply_humidity_ratio,
                dt_seconds,
                self.params.coil_time_constant_seconds,
            )
        else:
            # A commanded valve can circulate stagnant/protective water with
            # the fan off, but there is no air-side coil load to warm it.
            if self.chw_plant_model is not None:
                self._cooling_coil_chw_flow_gpm = (
                    min(
                        self.params.cooling_coil_design_flow_gpm,
                        max(
                            0.0,
                            float(getattr(self.chw_plant_model, "flow_gpm", 0.0)),
                        ),
                    )
                    * self._cooling_valve_fraction
                )
            # Dead air slowly equalizes with the mixed-air plenum after fan
            # shutdown rather than freezing at the last operating SAT.
            self._sa_temp = self.approach(
                self._sa_temp,
                self._ma_temp,
                dt_seconds,
                self.params.plenum_idle_time_constant_seconds,
            )
            self._supply_air_humidity_ratio = self.approach(
                self._supply_air_humidity_ratio,
                self._mixed_air_humidity_ratio,
                dt_seconds,
                self.params.plenum_idle_time_constant_seconds,
            )

        if not self.fan_running:
            self._conditioning_source = "off"
        elif self._heating_active:
            self._conditioning_source = "hot-water-heating"
        elif (
            self._mechanical_cooling_active
            and self._economizer_cooling_active
        ):
            self._conditioning_source = "integrated-economizer-cooling"
        elif self._mechanical_cooling_active:
            self._conditioning_source = "mechanical-cooling"
        elif self._economizer_cooling_active:
            self._conditioning_source = "economizer"
        else:
            self._conditioning_source = "neutral"

        self.registry.set("ahu_ma_temp", self._ma_temp)
        self.registry.set("ahu_ra_temp", self._ra_temp)
        self.registry.set("ahu_ra_humidity", self._ra_humidity)
        self.registry.set("ahu_sa_temp", self._sa_temp)
        if "sa_fan_status" in self.registry.all_points():
            self.registry.set("sa_fan_status", 1.0 if self.fan_running else 0.0)
        if "ra_fan_status" in self.registry.all_points():
            self.registry.set("ra_fan_status", 1.0 if (ra_fan_cmd and self.fan_running) else 0.0)
        self._publish_safety_points()

        self.runtime_seconds += dt_seconds
