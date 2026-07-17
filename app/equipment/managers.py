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
    header_time_constant_seconds: float = 20.0  # header sensors blend, they don't step
    idle_header_temp_f: float = 54.0  # stagnant loop drifts here with no chillers proven
    header_return_rise_f: float = 10.0


class ChwPlantManagerModel(EquipmentModel):
    """Publishes the Chiller Manager's plant-level status points."""

    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        chillers: list[ChillerModel],
        parameters: ChwPlantParameters | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.chillers = chillers
        self.params = parameters or ChwPlantParameters()
        self._chws_common = self.params.idle_header_temp_f
        self._chwr_common = self.params.idle_header_temp_f
        self._flow_common = 0.0

    def tick(self, dt_seconds: float) -> None:
        for n, chiller in enumerate(self.chillers, start=1):
            self.registry.set(f"chiller{n}_ok", 1.0 if chiller.proven else 0.0)

        proven = [c for c in self.chillers if c.proven]
        if proven:
            # Header supply temp follows the coldest proven machine; flow is
            # what the running CHW pumps are actually moving.
            target_chws = min(c.chws_temp_f for c in proven)
            target_chwr = target_chws + self.params.header_return_rise_f
        else:
            target_chws = self.params.idle_header_temp_f
            target_chwr = self.params.idle_header_temp_f
        pumping = sum(1 for c in self.chillers if c.chw_pump_running)
        target_flow = pumping * self.params.design_flow_per_chiller_gpm

        tc = self.params.header_time_constant_seconds
        self._chws_common = self.approach(self._chws_common, target_chws, dt_seconds, tc)
        self._chwr_common = self.approach(self._chwr_common, target_chwr, dt_seconds, tc)
        self._flow_common = self.approach(self._flow_common, target_flow, dt_seconds, tc)

        self.registry.set("chws_temp_common", self._chws_common)
        self.registry.set("chwr_temp_common", self._chwr_common)
        self.registry.set("chws_flow_common", self._flow_common)

        self.runtime_seconds += dt_seconds


class BoilerManagerModel(EquipmentModel):
    """Publishes the Boiler Manager's boilerN_ok proof mirrors."""

    def __init__(self, equipment_id: str, registry: PointRegistry, boilers: list[BoilerModel]):
        super().__init__(equipment_id, registry)
        self.boilers = boilers

    def tick(self, dt_seconds: float) -> None:
        for n, boiler in enumerate(self.boilers, start=1):
            self.registry.set(f"boiler{n}_ok", 1.0 if boiler.proven else 0.0)
        self.runtime_seconds += dt_seconds
