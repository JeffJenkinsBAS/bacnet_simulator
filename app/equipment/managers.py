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

        # Whole-loop first-law balance.  AHU heat, pump work, and ambient
        # piping/mechanical-room gains add energy; proven compressors remove
        # it.  The finite water inventory makes temperatures coast and pull
        # down over realistic minutes/hours instead of snapping to 54-55 F.
        self._coil_heat_btuh = self.cooling_load_btuh if target_flow > 0.0 else 0.0
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

        if target_flow > 0.0:
            # Heat picked up between the common supply and return sensors.
            # Pump work changes the mean temperature; the pump is treated as
            # plant-side of the common supply sensor, so it is not double
            # counted as AHU/distribution delta-T.
            load_side_heat_btuh = (
                self._coil_heat_btuh + self._ambient_heat_btuh
            )
            return_rise = load_side_heat_btuh / max(500.0 * target_flow, 1.0)
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
        self._flow_common = self.approach(self._flow_common, target_flow, dt_seconds, tc)

        distribution_ids = {id(chiller) for chiller in distribution_units}
        for chiller in self.chillers:
            chiller.set_evaporator_conditions(
                return_temp_f=self._chwr_common,
                flow_gpm=(
                    self.params.design_flow_per_chiller_gpm
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
    header_time_constant_seconds: float = 30.0
    idle_header_temp_f: float = 100.0
    minimum_usable_supply_temp_f: float = 100.0
    full_capacity_supply_temp_f: float = 160.0


class BoilerManagerModel(EquipmentModel):
    """Publishes the Boiler Manager's boilerN_ok proof mirrors."""

    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        boilers: list[BoilerModel],
        parameters: BoilerPlantParameters | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.boilers = boilers
        self.params = parameters or BoilerPlantParameters()
        self._hws_common = self.params.idle_header_temp_f
        self._flow_common = 0.0

    @property
    def distribution_units(self) -> list[BoilerModel]:
        return [
            boiler
            for boiler in self.boilers
            if boiler.proven and boiler.hw_pump_running
        ]

    @property
    def proven_unit_count(self) -> int:
        return sum(1 for boiler in self.boilers if boiler.proven)

    @property
    def supply_temp_f(self) -> float:
        return self._hws_common

    @property
    def flow_gpm(self) -> float:
        return self._flow_common

    @property
    def heating_capacity_fraction(self) -> float:
        if not self.distribution_units:
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
        design_flow = self.params.design_flow_per_boiler_gpm * len(self.distribution_units)
        flow_factor = max(0.0, min(1.0, self._flow_common / max(design_flow, 1.0)))
        return temperature_factor * flow_factor

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
            "flow_gpm": round(self.flow_gpm, 2),
        }

    def tick(self, dt_seconds: float) -> None:
        for n, boiler in enumerate(self.boilers, start=1):
            self.registry.set(f"boiler{n}_ok", 1.0 if boiler.proven else 0.0)

        distribution_units = self.distribution_units
        if distribution_units:
            target_hws = sum(boiler.hws_temp_f for boiler in distribution_units) / len(distribution_units)
            target_flow = self.params.design_flow_per_boiler_gpm * len(distribution_units)
        else:
            target_hws = self.params.idle_header_temp_f
            target_flow = 0.0

        tc = self.params.header_time_constant_seconds
        self._hws_common = self.approach(self._hws_common, target_hws, dt_seconds, tc)
        self._flow_common = self.approach(self._flow_common, target_flow, dt_seconds, tc)
        self.runtime_seconds += dt_seconds
