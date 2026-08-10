"""Physical virtual-zone heat and moisture balance.

The virtual zones are loads, not controllers. They never write VAV commands
or move directly toward a comfort setpoint. Actual measured airflow and
discharge-air temperature determine sensible heating/cooling, while envelope,
infiltration, internal, solar, and adjacent-space loads continue even when the
box has no airflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, exp, pi

from app.equipment.psychrometrics import humidity_ratio_from_rh, rh_from_humidity_ratio


@dataclass
class ZoneParameters:
    space_name: str = "Virtual zone"
    floor_area_sqft: float = 1000.0
    ceiling_height_ft: float = 10.0
    thermal_capacitance_btuper_f: float = 10000.0
    envelope_ua_btuh_per_f: float = 150.0
    peak_solar_gain_btuh: float = 2000.0
    solar_peak_hour: float = 14.0
    internal_sensible_gain_btuh_per_sqft: float = 7.2
    occupants_per_1000_sqft: float = 5.0
    occupied_load_fraction: float = 0.72
    humidity_capacitance_multiplier: float = 12.0
    initial_humidity_pct: float = 45.0
    infiltration_ach_fan_on: float = 0.05
    infiltration_ach_fan_off: float = 0.15
    adjacent_mixing_cfm: float = 20.0
    adjacent_temp_f: float = 72.0
    adjacent_humidity_pct: float = 45.0
    initial_solar_hour: float = 14.0
    minimum_zone_temp_f: float = 45.0
    maximum_zone_temp_f: float = 105.0


class ZoneModel:
    """Analytical first-order zone energy and moisture balance."""

    STANDARD_AIR_SENSIBLE_FACTOR = 1.08  # Btu/h per CFM per deg F
    DRY_AIR_MASS_FLOW_FACTOR = 4.5  # lb dry air/h per CFM
    DRY_AIR_DENSITY_LB_PER_FT3 = 0.075
    OCCUPANT_MOISTURE_LB_PER_HOUR = 0.19

    def __init__(
        self,
        parameters: ZoneParameters,
        *,
        initial_temp_f: float,
        initial_humidity_pct: float | None = None,
    ):
        self.params = parameters
        seed_rh = (
            parameters.initial_humidity_pct
            if initial_humidity_pct is None
            else initial_humidity_pct
        )
        self._temperature_f = float(initial_temp_f)
        self._humidity_ratio = humidity_ratio_from_rh(initial_temp_f, seed_rh)
        self.runtime_seconds = 0.0
        self.last_snapshot: dict[str, float] = {}

    @property
    def volume_ft3(self) -> float:
        return max(1.0, self.params.floor_area_sqft * self.params.ceiling_height_ft)

    @property
    def temperature_f(self) -> float:
        """True physical zone temperature, independent of sensor faults."""
        return self._temperature_f

    @property
    def humidity_ratio(self) -> float:
        """True physical zone moisture content in lb water/lb dry air."""
        return self._humidity_ratio

    def _solar_gain_btuh(self) -> float:
        hour = (self.params.initial_solar_hour + self.runtime_seconds / 3600.0) % 24.0
        hour_delta = ((hour - self.params.solar_peak_hour + 12.0) % 24.0) - 12.0
        shape = max(0.0, cos(hour_delta * pi / 12.0)) ** 3
        return self.params.peak_solar_gain_btuh * shape

    @staticmethod
    def _analytical_state(
        current: float,
        conductance: float,
        source: float,
        capacity: float,
        dt_hours: float,
    ) -> float:
        if capacity <= 0.0 or dt_hours <= 0.0:
            return current
        if conductance <= 1e-9:
            return current + source * dt_hours / capacity
        equilibrium = source / conductance
        return equilibrium + (current - equilibrium) * exp(
            -conductance * dt_hours / capacity
        )

    def tick(
        self,
        dt_seconds: float,
        *,
        zone_temp_f: float,
        outdoor_temp_f: float,
        outdoor_humidity_pct: float,
        supply_airflow_cfm: float,
        discharge_temp_f: float,
        supply_humidity_ratio: float,
        ahu_supply_proven: bool,
    ) -> tuple[float, float]:
        dt_hours = max(0.0, float(dt_seconds)) / 3600.0
        supply_cfm = max(0.0, float(supply_airflow_cfm)) if ahu_supply_proven else 0.0
        infiltration_ach = (
            self.params.infiltration_ach_fan_on
            if ahu_supply_proven
            else self.params.infiltration_ach_fan_off
        )
        infiltration_cfm = max(0.0, infiltration_ach * self.volume_ft3 / 60.0)
        mixing_cfm = max(0.0, self.params.adjacent_mixing_cfm)

        supply_conductance = self.STANDARD_AIR_SENSIBLE_FACTOR * supply_cfm
        infiltration_conductance = (
            self.STANDARD_AIR_SENSIBLE_FACTOR * infiltration_cfm
        )
        mixing_conductance = self.STANDARD_AIR_SENSIBLE_FACTOR * mixing_cfm
        envelope_conductance = max(0.0, self.params.envelope_ua_btuh_per_f)
        total_conductance = (
            supply_conductance
            + infiltration_conductance
            + mixing_conductance
            + envelope_conductance
        )

        internal_gain = (
            self.params.floor_area_sqft
            * self.params.internal_sensible_gain_btuh_per_sqft
            * self.params.occupied_load_fraction
        )
        solar_gain = self._solar_gain_btuh()
        sensible_source = (
            internal_gain
            + solar_gain
            + supply_conductance * discharge_temp_f
            + infiltration_conductance * outdoor_temp_f
            + mixing_conductance * self.params.adjacent_temp_f
            + envelope_conductance * outdoor_temp_f
        )
        physical_zone_temp = self._temperature_f
        new_temp = self._analytical_state(
            physical_zone_temp,
            total_conductance,
            sensible_source,
            max(1.0, self.params.thermal_capacitance_btuper_f),
            dt_hours,
        )
        new_temp = max(
            self.params.minimum_zone_temp_f,
            min(self.params.maximum_zone_temp_f, new_temp),
        )
        self._temperature_f = new_temp

        outdoor_ratio = humidity_ratio_from_rh(
            outdoor_temp_f,
            outdoor_humidity_pct,
        )
        adjacent_ratio = humidity_ratio_from_rh(
            self.params.adjacent_temp_f,
            self.params.adjacent_humidity_pct,
        )
        supply_mass_flow = self.DRY_AIR_MASS_FLOW_FACTOR * supply_cfm
        infiltration_mass_flow = self.DRY_AIR_MASS_FLOW_FACTOR * infiltration_cfm
        mixing_mass_flow = self.DRY_AIR_MASS_FLOW_FACTOR * mixing_cfm
        total_moisture_flow = (
            supply_mass_flow + infiltration_mass_flow + mixing_mass_flow
        )
        occupants = (
            self.params.floor_area_sqft
            * self.params.occupants_per_1000_sqft
            / 1000.0
            * self.params.occupied_load_fraction
        )
        moisture_generation = occupants * self.OCCUPANT_MOISTURE_LB_PER_HOUR
        moisture_source = (
            moisture_generation
            + supply_mass_flow * max(0.0, supply_humidity_ratio)
            + infiltration_mass_flow * outdoor_ratio
            + mixing_mass_flow * adjacent_ratio
        )
        moisture_capacity = (
            self.DRY_AIR_DENSITY_LB_PER_FT3
            * self.volume_ft3
            * max(1.0, self.params.humidity_capacitance_multiplier)
        )
        self._humidity_ratio = self._analytical_state(
            self._humidity_ratio,
            total_moisture_flow,
            moisture_source,
            moisture_capacity,
            dt_hours,
        )
        self._humidity_ratio = max(0.001, min(0.03, self._humidity_ratio))
        new_rh = max(5.0, min(95.0, rh_from_humidity_ratio(new_temp, self._humidity_ratio)))

        hvac_btuh = supply_conductance * (discharge_temp_f - physical_zone_temp)
        envelope_btuh = envelope_conductance * (outdoor_temp_f - physical_zone_temp)
        self.last_snapshot = {
            "zone_area_sqft": round(self.params.floor_area_sqft, 1),
            "thermal_capacitance_btuper_f": round(
                self.params.thermal_capacitance_btuper_f,
                1,
            ),
            "hvac_sensible_btuh": round(hvac_btuh, 1),
            "envelope_btuh": round(envelope_btuh, 1),
            "internal_gain_btuh": round(internal_gain, 1),
            "solar_gain_btuh": round(solar_gain, 1),
            "infiltration_cfm": round(infiltration_cfm, 1),
        }
        self.runtime_seconds += max(0.0, float(dt_seconds))
        return new_temp, new_rh
