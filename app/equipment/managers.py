"""
Plant manager aggregator models (audit finding 2.1).

The ACI-SIM-CHW-PLANT and ACI-SIM-BOILER-MGR groups publish manager-level
points (chillerN_ok / boilerN_ok proof mirrors, common CHW header sensors)
that Jeff's Chiller Manager / Boiler Manager EIKON programs bind to, but
until this module existed no equipment model ever serviced them -- they sat
frozen at their initial values forever.

These aggregators hold direct in-process references to the unit models
(same pattern as SingleDuctVavModel's ahu_model reference) and must tick
AFTER them in the engine's equipment list so they mirror this tick's proof
state, not last tick's.

The manager-level COMMAND points are consumed elsewhere, closer to the
equipment they affect: remote_shutdown is read by every ChillerModel from
the plant GroupView, and enable_boilerN is read by each BoilerModel through
its manager_registry reference.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.equipment.boiler import BoilerModel
from app.equipment.chiller import ChillerModel
from app.registry import PointRegistry


@dataclass
class ChwPlantParameters:
    design_flow_per_chiller_gpm: float = 300.0
    header_time_constant_seconds: float = 8.0  # sensor/header mixing lag
    flow_time_constant_seconds: float = 3.0
    idle_header_temp_f: float = 70.0  # initial water temperature, not a fixed target
    header_return_rise_f: float = 10.0
    minimum_usable_flow_gpm: float = 50.0
    maximum_header_return_rise_f: float = 30.0
    # Roughly 8-10 gallons/ton for the active 125-ton circuit, including
    # mains, coil, evaporator, and buffer volume. One unit can therefore pull
    # an ambient loop down in about ten minutes; additional units shorten it.
    loop_volume_gallons: float = 1200.0
    water_heat_capacity_btuper_gallon_f: float = 8.33
    pump_heat_btuh_per_running_pump: float = 12_000.0
    loop_ambient_ua_btuh_per_f: float = 450.0
    outdoor_ambient_fraction: float = 0.15
    mechanical_room_temp_offset_f: float = 2.0
    minimum_loop_temp_f: float = 34.0
    maximum_loop_temp_f: float = 100.0


class ChwPlantManagerModel(EquipmentModel):
    """Publishes the Chiller Manager's plant-level status points."""

    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        chillers: list[ChillerModel],
        parameters: ChwPlantParameters | None = None,
        site_registry: PointRegistry | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.chillers = chillers
        self.params = parameters or ChwPlantParameters()
        self.site_registry = site_registry
        self._chws_common = self.params.idle_header_temp_f
        self._chwr_common = self.params.idle_header_temp_f
        self._loop_mean_temp_f = self.params.idle_header_temp_f
        self._flow_common = 0.0
        self._cooling_coils: list = []
        self._coil_heat_btuh = 0.0
        self._pump_heat_btuh = 0.0
        self._ambient_heat_btuh = 0.0
        self._refrigeration_btuh = 0.0

    def set_cooling_coils(self, cooling_coils: list) -> None:
        """Attach downstream coil-load providers after graph construction."""
        self._cooling_coils = list(cooling_coils)

    @property
    def proven_unit_count(self) -> int:
        return sum(1 for chiller in self.chillers if chiller.proven)

    @property
    def supply_temp_f(self) -> float:
        return self._chws_common

    @property
    def return_temp_f(self) -> float:
        return self._chwr_common

    @property
    def flow_gpm(self) -> float:
        return self._flow_common

    @property
    def cooling_capacity_fraction(self) -> float:
        """Fraction of design coil water flow physically available.

        Water temperature is handled by the AHU coil effectiveness model.
        Treating 55 F water as zero capacity incorrectly prevented a warm,
        circulating loop from absorbing any building heat.
        """
        if self._flow_common < self.params.minimum_usable_flow_gpm:
            return 0.0
        # One design-flow circuit is sufficient to serve the only AHU coil.
        # Additional pumps/chillers add plant capacity and redundancy rather
        # than making a single downstream coil exceed 100% availability.
        design_flow = self.params.design_flow_per_chiller_gpm
        flow_factor = max(
            0.0,
            min(1.0, self._flow_common / max(design_flow, 1.0)),
        )
        return flow_factor

    @property
    def cooling_load_btuh(self) -> float:
        """Positive air-side heat transferred into chilled water."""
        return sum(
            max(0.0, float(getattr(coil, "cooling_coil_load_btuh", 0.0)))
            for coil in self._cooling_coils
        )

    @property
    def loop_ambient_temp_f(self) -> float:
        building_temperatures = [
            float(getattr(coil, "return_air_temp_f"))
            for coil in self._cooling_coils
            if hasattr(coil, "return_air_temp_f")
        ]
        building_temp = (
            sum(building_temperatures) / len(building_temperatures)
            if building_temperatures
            else 72.0
        )
        outdoor_temp = building_temp
        if self.site_registry is not None:
            try:
                outdoor_temp = float(self.site_registry.get("oa_temp"))
            except KeyError:
                pass
        outdoor_fraction = max(
            0.0,
            min(1.0, self.params.outdoor_ambient_fraction),
        )
        return (
            (1.0 - outdoor_fraction) * building_temp
            + outdoor_fraction * outdoor_temp
            + self.params.mechanical_room_temp_offset_f
        )

    @property
    def nominal_capacity_btuh(self) -> float:
        per_unit = (
            500.0
            * self.params.design_flow_per_chiller_gpm
            * self.params.header_return_rise_f
        )
        return per_unit * self.proven_unit_count

    @property
    def cooling_available(self) -> bool:
        return self.cooling_capacity_fraction >= 0.05

    def operating_snapshot(self) -> dict:
        return {
            "available": self.cooling_available,
            "capacity_fraction": round(self.cooling_capacity_fraction, 3),
            "proven_units": self.proven_unit_count,
            "supply_temp_f": round(self.supply_temp_f, 2),
            "return_temp_f": round(self.return_temp_f, 2),
            "flow_gpm": round(self.flow_gpm, 2),
            "cooling_load_btuh": round(self.cooling_load_btuh, 1),
            "nominal_capacity_btuh": round(self.nominal_capacity_btuh, 1),
            "loop_mean_temp_f": round(self._loop_mean_temp_f, 2),
            "loop_ambient_temp_f": round(self.loop_ambient_temp_f, 2),
            "coil_heat_btuh": round(self._coil_heat_btuh, 1),
            "pump_heat_btuh": round(self._pump_heat_btuh, 1),
            "ambient_heat_btuh": round(self._ambient_heat_btuh, 1),
            "refrigeration_btuh": round(self._refrigeration_btuh, 1),
        }

    def tick(self, dt_seconds: float) -> None:
        for n, chiller in enumerate(self.chillers, start=1):
            self.registry.set(f"chiller{n}_ok", 1.0 if chiller.proven else 0.0)

        distribution_units = [
            c
            for c in self.chillers
            if c.chw_pump_running and c.chw_isolation_open
        ]
        pumping = len(distribution_units)
        target_flow = pumping * self.params.design_flow_per_chiller_gpm
        self._flow_common = self.approach(
            self._flow_common,
            target_flow,
            dt_seconds,
            self.params.flow_time_constant_seconds,
        )

        # Whole-loop first-law balance.  AHU heat, pump work, and ambient
        # piping/mechanical-room gains add energy; proven compressors remove
        # it.  The finite water inventory makes temperatures coast and pull
        # down over realistic minutes/hours instead of snapping to 54-55 F.
        self._coil_heat_btuh = self.cooling_load_btuh if self._flow_common > 0.01 else 0.0
        self._pump_heat_btuh = (
            pumping * self.params.pump_heat_btuh_per_running_pump
        )
        self._ambient_heat_btuh = self.params.loop_ambient_ua_btuh_per_f * (
            self.loop_ambient_temp_f - self._loop_mean_temp_f
        )
        self._refrigeration_btuh = sum(
            max(0.0, chiller.evaporator_heat_removed_btuh)
            for chiller in distribution_units
        )
        net_heat_btuh = (
            self._coil_heat_btuh
            + self._pump_heat_btuh
            + self._ambient_heat_btuh
            - self._refrigeration_btuh
        )
        loop_heat_capacity = max(
            1.0,
            self.params.loop_volume_gallons
            * self.params.water_heat_capacity_btuper_gallon_f,
        )
        self._loop_mean_temp_f += (
            net_heat_btuh * max(0.0, dt_seconds) / 3600.0 / loop_heat_capacity
        )
        self._loop_mean_temp_f = max(
            self.params.minimum_loop_temp_f,
            min(self.params.maximum_loop_temp_f, self._loop_mean_temp_f),
        )

        if self._flow_common > 0.01:
            # Heat picked up between the common supply and return sensors.
            # Pump work changes the mean temperature; the pump is treated as
            # plant-side of the common supply sensor, so it is not double
            # counted as AHU/distribution delta-T.
            load_side_heat_btuh = (
                self._coil_heat_btuh + self._ambient_heat_btuh
            )
            return_rise = load_side_heat_btuh / max(500.0 * self._flow_common, 1.0)
            return_rise = max(
                -self.params.maximum_header_return_rise_f,
                min(self.params.maximum_header_return_rise_f, return_rise),
            )
            target_chws = self._loop_mean_temp_f - 0.5 * return_rise
            target_chwr = self._loop_mean_temp_f + 0.5 * return_rise
        else:
            # Stagnant headers share the loop mean.  They may match each other,
            # but they continue drifting toward actual ambient rather than a
            # fabricated chilled-water temperature.
            target_chws = self._loop_mean_temp_f
            target_chwr = self._loop_mean_temp_f

        tc = self.params.header_time_constant_seconds
        self._chws_common = self.approach(self._chws_common, target_chws, dt_seconds, tc)
        self._chwr_common = self.approach(self._chwr_common, target_chwr, dt_seconds, tc)
        distribution_ids = {id(chiller) for chiller in distribution_units}
        branch_flow = self._flow_common / max(pumping, 1) if pumping else 0.0
        for chiller in self.chillers:
            chiller.set_evaporator_conditions(
                return_temp_f=self._chwr_common,
                flow_gpm=(
                    branch_flow * (1.0 - chiller.chw_bypass_fraction)
                    if id(chiller) in distribution_ids
                    else 0.0
                ),
            )

        self.registry.set("chws_temp_common", self._chws_common)
        self.registry.set("chwr_temp_common", self._chwr_common)
        self.registry.set("chws_flow_common", self._flow_common)

        self.runtime_seconds += dt_seconds


@dataclass
class BoilerPlantParameters:
    design_flow_per_boiler_gpm: float = 60.0
    header_time_constant_seconds: float = 10.0
    hydraulic_time_constant_seconds: float = 3.0
    idle_header_temp_f: float = 100.0
    minimum_usable_supply_temp_f: float = 90.0
    full_capacity_supply_temp_f: float = 160.0
    minimum_usable_flow_gpm: float = 1.0
    pump_design_dp_psi: float = 8.0
    pump_shutoff_dp_psi: float = 12.0
    minimum_bypass_flow_per_running_pump_gpm: float = 3.0
    loop_volume_gallons: float = 800.0
    water_heat_capacity_btuper_gallon_f: float = 8.33
    distribution_pump_heat_btuh: float = 6_000.0
    circulator_pump_heat_btuh: float = 3_000.0
    # Insulated distribution piping. The former 900 BTUH/F value implied
    # roughly 95 kBTUH of jacket loss at 180 F, overwhelming the actual
    # terminal load and fabricating extreme common-header delta-T.
    loop_ambient_ua_btuh_per_f: float = 60.0
    outdoor_ambient_fraction: float = 0.10
    mechanical_room_temp_offset_f: float = 3.0
    minimum_loop_temp_f: float = 40.0
    maximum_loop_temp_f: float = 210.0
    maximum_header_drop_f: float = 40.0
    supply_sensor_overshoot_tolerance_f: float = 0.25


class BoilerManagerModel(EquipmentModel):
    """Publishes the Boiler Manager's boilerN_ok proof mirrors."""

    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        boilers: list[BoilerModel],
        parameters: BoilerPlantParameters | None = None,
        site_registry: PointRegistry | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.boilers = boilers
        self.params = parameters or BoilerPlantParameters()
        self.site_registry = site_registry
        self._hws_common = self.params.idle_header_temp_f
        self._hwr_common = self.params.idle_header_temp_f
        self._loop_mean_temp_f = self.params.idle_header_temp_f
        self._flow_common = 0.0
        self._differential_pressure_psi = 0.0
        self._pump_speed_pct = 0.0
        self._heating_coils: list = []
        self._coil_heat_btuh = 0.0
        self._pump_heat_btuh = 0.0
        self._ambient_loss_btuh = 0.0
        self._boiler_heat_btuh = 0.0
        self._primary_to_secondary_heat_btuh = 0.0
        self._primary_return_temp_f = self.params.idle_header_temp_f

    def set_heating_coils(self, heating_coils: list) -> None:
        """Attach AHU and terminal coils after graph construction."""
        self._heating_coils = list(heating_coils)

    @property
    def distribution_units(self) -> list[BoilerModel]:
        # Distribution pumps circulate residual heat even with burners off.
        return [boiler for boiler in self.boilers if boiler.hw_pump_running]

    @property
    def proven_unit_count(self) -> int:
        return sum(1 for boiler in self.boilers if boiler.proven)

    @property
    def supply_temp_f(self) -> float:
        return self._hws_common

    @property
    def return_temp_f(self) -> float:
        return self._hwr_common

    @property
    def flow_gpm(self) -> float:
        return self._flow_common

    @property
    def differential_pressure_psi(self) -> float:
        return self._differential_pressure_psi

    @property
    def heating_load_btuh(self) -> float:
        return sum(
            max(0.0, float(getattr(coil, "hot_water_coil_load_btuh", 0.0)))
            for coil in self._heating_coils
        )

    @property
    def loop_ambient_temp_f(self) -> float:
        zone_temperatures = [
            float(getattr(coil, "return_air_temp_f"))
            for coil in self._heating_coils
            if hasattr(coil, "return_air_temp_f")
        ]
        building_temp = sum(zone_temperatures) / len(zone_temperatures) if zone_temperatures else 72.0
        outdoor_temp = building_temp
        if self.site_registry is not None:
            try:
                outdoor_temp = float(self.site_registry.get("oa_temp"))
            except KeyError:
                pass
        fraction = max(0.0, min(1.0, self.params.outdoor_ambient_fraction))
        return (
            (1.0 - fraction) * building_temp
            + fraction * outdoor_temp
            + self.params.mechanical_room_temp_offset_f
        )

    @property
    def heating_capacity_fraction(self) -> float:
        if self._flow_common < self.params.minimum_usable_flow_gpm:
            return 0.0
        temperature_span = (
            self.params.full_capacity_supply_temp_f
            - self.params.minimum_usable_supply_temp_f
        )
        temperature_factor = max(
            0.0,
            min(
                1.0,
                (self._hws_common - self.params.minimum_usable_supply_temp_f)
                / max(temperature_span, 0.1),
            ),
        )
        # A two-way system at low load legitimately carries only a few GPM;
        # low total flow does not mean the open coil lacks capacity. Available
        # differential pressure is the relevant delivery constraint.
        pressure_factor = max(
            0.0,
            min(
                1.0,
                self._differential_pressure_psi
                / max(self.params.pump_design_dp_psi, 0.1),
            ),
        )
        return temperature_factor * pressure_factor

    @property
    def heating_available(self) -> bool:
        return self.heating_capacity_fraction >= 0.05

    def operating_snapshot(self) -> dict:
        return {
            "available": self.heating_available,
            "capacity_fraction": round(self.heating_capacity_fraction, 3),
            "proven_units": self.proven_unit_count,
            "distribution_units": len(self.distribution_units),
            "supply_temp_f": round(self.supply_temp_f, 2),
            "return_temp_f": round(self.return_temp_f, 2),
            "flow_gpm": round(self.flow_gpm, 2),
            "differential_pressure_psi": round(self.differential_pressure_psi, 2),
            "pump_speed_pct": round(self._pump_speed_pct, 1),
            "heating_load_btuh": round(self.heating_load_btuh, 1),
            "boiler_heat_btuh": round(self._boiler_heat_btuh, 1),
            "primary_to_secondary_heat_btuh": round(
                self._primary_to_secondary_heat_btuh, 1
            ),
            "primary_return_temp_f": round(self._primary_return_temp_f, 2),
            "pump_heat_btuh": round(self._pump_heat_btuh, 1),
            "ambient_loss_btuh": round(self._ambient_loss_btuh, 1),
            "loop_mean_temp_f": round(self._loop_mean_temp_f, 2),
            "loop_ambient_temp_f": round(self.loop_ambient_temp_f, 2),
        }

    def _coil_flow_demand(self, differential_pressure_psi: float) -> float:
        return sum(
            max(0.0, float(coil.hot_water_flow_at_pressure(differential_pressure_psi)))
            for coil in self._heating_coils
            if hasattr(coil, "hot_water_flow_at_pressure")
        )

    def _solve_hydraulics(self, pumping: int) -> tuple[float, float]:
        if pumping <= 0:
            return 0.0, 0.0
        # Preserve nominal fixed-speed behavior for a manager used by itself.
        if not self._heating_coils:
            return (
                pumping * self.params.design_flow_per_boiler_gpm,
                self.params.pump_design_dp_psi,
            )

        bypass = pumping * self.params.minimum_bypass_flow_per_running_pump_gpm
        design_dp = self.params.pump_design_dp_psi
        shutoff_dp = max(design_dp + 0.1, self.params.pump_shutoff_dp_psi)

        def pump_capacity(dp: float) -> float:
            head_fraction = max(0.0, (shutoff_dp - dp) / (shutoff_dp - design_dp))
            return pumping * self.params.design_flow_per_boiler_gpm * (head_fraction ** 0.5)

        def demand(dp: float) -> float:
            return bypass + self._coil_flow_demand(dp)

        low, high = 0.0, shutoff_dp
        for _ in range(40):
            mid = 0.5 * (low + high)
            if pump_capacity(mid) > demand(mid):
                low = mid
            else:
                high = mid
        dp = 0.5 * (low + high)
        return min(demand(dp), pump_capacity(dp)), dp

    def tick(self, dt_seconds: float) -> None:
        for n, boiler in enumerate(self.boilers, start=1):
            self.registry.set(f"boiler{n}_ok", 1.0 if boiler.proven else 0.0)

        distribution_units = self.distribution_units
        target_flow, target_dp = self._solve_hydraulics(len(distribution_units))
        circulator_units = [boiler for boiler in self.boilers if boiler.circ_pump_running]

        hydraulic_tc = self.params.hydraulic_time_constant_seconds
        self._flow_common = self.approach(
            self._flow_common, target_flow, dt_seconds, hydraulic_tc
        )
        self._differential_pressure_psi = self.approach(
            self._differential_pressure_psi, target_dp, dt_seconds, hydraulic_tc
        )

        self._coil_heat_btuh = self.heating_load_btuh if self._flow_common > 0.01 else 0.0
        self._pump_heat_btuh = (
            len(distribution_units) * self.params.distribution_pump_heat_btuh
            + len(circulator_units) * self.params.circulator_pump_heat_btuh
        )
        self._ambient_loss_btuh = self.params.loop_ambient_ua_btuh_per_f * (
            self._loop_mean_temp_f - self.loop_ambient_temp_f
        )
        self._boiler_heat_btuh = sum(
            max(0.0, boiler.heat_output_btuh) for boiler in self.boilers
        )

        # Primary-secondary hydraulic separator balance. Boiler circulators
        # establish constant primary flow independently of secondary demand.
        # The common distribution supply is therefore actual primary leaving
        # water (or a blend of that water with excess secondary return when
        # secondary flow is greater), never an independently synthesized
        # temperature hotter than every active boiler outlet.
        primary_flow = (
            len(circulator_units) * self.params.design_flow_per_boiler_gpm
        )
        primary_leaving_temperatures = [
            float(getattr(boiler, "hws_temp_f", self._hws_common))
            for boiler in circulator_units
        ]
        primary_supply = (
            sum(primary_leaving_temperatures) / len(primary_leaving_temperatures)
            if primary_leaving_temperatures
            else self._hws_common
        )
        loop_heat_capacity = max(
            1.0,
            self.params.loop_volume_gallons * self.params.water_heat_capacity_btuper_gallon_f,
        )

        if self._flow_common > 0.01:
            load_side_heat = self._coil_heat_btuh + self._ambient_loss_btuh
            header_drop = load_side_heat / max(500.0 * self._flow_common, 1.0)
            header_drop = max(
                -self.params.maximum_header_drop_f,
                min(self.params.maximum_header_drop_f, header_drop),
            )
            if primary_flow > 0.01:
                primary_fraction = min(1.0, primary_flow / self._flow_common)
                target_hws = (
                    primary_fraction * primary_supply
                    + (1.0 - primary_fraction) * self._hwr_common
                )
                tc = self.params.header_time_constant_seconds
                self._hws_common = self.approach(
                    self._hws_common, target_hws, dt_seconds, tc
                )
                if primary_leaving_temperatures:
                    hottest_primary_supply = max(primary_leaving_temperatures)
                    self._hws_common = min(
                        self._hws_common,
                        hottest_primary_supply
                        + self.params.supply_sensor_overshoot_tolerance_f,
                    )
            else:
                # Distribution-pump coast with no primary source uses the
                # finite stored loop energy.
                net_heat_btuh = (
                    self._pump_heat_btuh
                    - self._coil_heat_btuh
                    - self._ambient_loss_btuh
                )
                self._loop_mean_temp_f += (
                    net_heat_btuh
                    * max(0.0, dt_seconds)
                    / 3600.0
                    / loop_heat_capacity
                )
                target_hws = self._loop_mean_temp_f + 0.5 * header_drop
                tc = self.params.header_time_constant_seconds
                self._hws_common = self.approach(
                    self._hws_common, target_hws, dt_seconds, tc
                )

            # Common return is the flow-weighted result of all open coils,
            # the minimum-flow bypass, and modeled distribution loss. It is
            # not an unrelated bulk-loop temperature. This identity makes
            # 500*GPM*(HWS-HWR) equal the actual secondary heat removed.
            self._hwr_common = self._hws_common - header_drop
            self._primary_to_secondary_heat_btuh = (
                500.0
                * self._flow_common
                * (self._hws_common - self._hwr_common)
                if primary_flow > 0.01
                else 0.0
            )
            # Keep one finite inventory for pump-off coast, synchronized to
            # the physically measured flowing headers. Do not integrate the
            # same source/load energy a second time into an independent mean.
            self._loop_mean_temp_f = 0.5 * (
                self._hws_common + self._hwr_common
            )
        else:
            target_hws = (
                primary_supply if primary_flow > 0.01 else self._loop_mean_temp_f
            )
            target_hwr = self._loop_mean_temp_f
            # With no secondary flow, only ambient exchange changes the
            # distribution inventory; circulator heat is added to the
            # primary return below.
            self._loop_mean_temp_f += (
                -self._ambient_loss_btuh
                * max(0.0, dt_seconds)
                / 3600.0
                / loop_heat_capacity
            )
            tc = self.params.header_time_constant_seconds
            self._hws_common = self.approach(
                self._hws_common, target_hws, dt_seconds, tc
            )
            self._hwr_common = self.approach(
                self._hwr_common, target_hwr, dt_seconds, tc
            )
            if primary_leaving_temperatures:
                self._hws_common = min(
                    self._hws_common,
                    max(primary_leaving_temperatures)
                    + self.params.supply_sensor_overshoot_tolerance_f,
                )
            self._primary_to_secondary_heat_btuh = 0.0

        self._loop_mean_temp_f = max(
            self.params.minimum_loop_temp_f,
            min(self.params.maximum_loop_temp_f, self._loop_mean_temp_f),
        )
        self._pump_speed_pct = self.approach(
            self._pump_speed_pct,
            100.0 if distribution_units else 0.0,
            dt_seconds,
            tc,
        )

        branch_units = circulator_units
        # Boiler circulators are primary-loop pumps. Their boiler flow does
        # not collapse just because two-way secondary valves close or the
        # distribution pump stops; the hydraulic separator decouples the two
        # circuits. This preserves the boiler's minimum-flow interlock and
        # lets it warm its primary loop before secondary demand arrives.
        branch_flow = self.params.design_flow_per_boiler_gpm if branch_units else 0.0
        if primary_flow > 0.01:
            if primary_flow >= self._flow_common:
                recirculated_primary_flow = primary_flow - self._flow_common
                self._primary_return_temp_f = (
                    self._flow_common * self._hwr_common
                    + recirculated_primary_flow * primary_supply
                ) / primary_flow
            else:
                # All primary flow returns from the secondary circuit; excess
                # secondary flow recirculates through the separator.
                self._primary_return_temp_f = self._hwr_common
            # Pump work is added after the common return sensor and before
            # the boiler entering-water sensor, so it reduces burner demand
            # without corrupting the measured secondary load delta-T.
            self._primary_return_temp_f += self._pump_heat_btuh / max(
                500.0 * primary_flow,
                1.0,
            )
        else:
            self._primary_return_temp_f = self._hwr_common

        branch_ids = {id(boiler) for boiler in branch_units}
        for boiler in self.boilers:
            boiler.set_hydronic_conditions(
                self._primary_return_temp_f,
                branch_flow if id(boiler) in branch_ids else 0.0,
            )

        aliases = set(self.registry.all_points())
        telemetry = {
            "hwr_temp_common": self._hwr_common,
            "hws_flow_common": self._flow_common,
            "hws_temp_common": self._hws_common,
            "hw_diff_pressure": self._differential_pressure_psi,
            "hw_loop_load": self._coil_heat_btuh,
            "boiler_heat_output": self._boiler_heat_btuh,
            "hw_pump_heat": self._pump_heat_btuh,
            "hw_pump_speed_common": self._pump_speed_pct,
        }
        for alias, value in telemetry.items():
            if alias in aliases:
                self.registry.set(alias, value)
        self.runtime_seconds += dt_seconds
