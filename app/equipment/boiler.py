"""
Boiler equipment model. One instance per unit (Boiler-1/2/3).

Behavior per the governing brief's Boiler expectations:
    - Enable initiates purge and ignition timing before proof appears.
    - Hot-water supply temperature is tracked internally (there is no
      published HWS Temp point for the boiler in the governing point list --
      only Boiler OK, Boiler S/S, Circ Pump S/S, HW Pump S/S, HWS Stpt Reset
      -- so internal temperature isn't published on BACnet, only the
      resulting OK/proof status is).

Note on scope: full lockout/failed-ignition fault behavior belongs to the
Phase 4 fault library. This model implements the normal-operation purge ->
ignition -> proof sequence so that library has real state to hook into,
without pre-building fault injection itself yet.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class BoilerParameters:
    purge_seconds: float = 15.0
    ignition_seconds: float = 10.0
    hws_setpoint_f: float = 180.0
    hws_time_constant_seconds: float = 60.0
    pump_start_delay_seconds: float = 3.0


class BoilerModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        parameters: BoilerParameters | None = None,
        manager_registry: PointRegistry | None = None,
        manager_enable_alias: str | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.params = parameters or BoilerParameters()
        # Boiler Manager wiring: the manager group's enable_boilerN point is
        # a second front door for the same start command (real plants: the
        # manager's enable IS the unit's start chain). Either point starts
        # the boiler, so unit-only EIKON programs keep working unchanged.
        self.manager_registry = manager_registry
        self.manager_enable_alias = manager_enable_alias

        self._enabled_seconds = 0.0
        self._proven = False
        self._hws_temp = 100.0  # internal only, not published -- see module docstring
        self._circ_pump_running = False
        self._hw_pump_running = False
        self._circ_pump_frac = 0.0
        self._hw_pump_frac = 0.0

    @property
    def proven(self) -> bool:
        """Public proof status, mirrored to the Boiler Manager's boilerN_ok points."""
        return self._proven

    def tick(self, dt_seconds: float) -> None:
        ss = self.registry.get_commanded("boiler_ss") == 1.0
        if self.manager_registry is not None and self.manager_enable_alias is not None:
            ss = ss or (self.manager_registry.get_commanded(self.manager_enable_alias) == 1.0)
        circ_pump_cmd = self.registry.get_commanded("circ_pump_ss") == 1.0
        hw_pump_cmd = self.registry.get_commanded("hw_pump_ss") == 1.0
        hws_reset = self.registry.get_commanded("hws_stpt_reset")

        # Pumps advance first: the low-water/flow interlock below needs this
        # tick's pump state.
        self._circ_pump_frac = self.approach(
            self._circ_pump_frac, 1.0 if circ_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._circ_pump_running = self._circ_pump_frac > 0.5
        self._hw_pump_frac = self.approach(
            self._hw_pump_frac, 1.0 if hw_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._hw_pump_running = self._hw_pump_frac > 0.5

        # Flow interlock: no circulating pump -> no ignition permit (real
        # boilers lock out on low-water/no-flow); losing the pump mid-run
        # drops proof and restarts the purge+ignition sequence.
        if ss and self._circ_pump_running:
            self._enabled_seconds += dt_seconds
        else:
            self._enabled_seconds = 0.0
        self._proven = (
            ss and self._circ_pump_running
            and self._enabled_seconds >= (self.params.purge_seconds + self.params.ignition_seconds)
        )

        setpoint = hws_reset if hws_reset else self.params.hws_setpoint_f
        target = setpoint if self._proven else 100.0
        self._hws_temp = self.approach(self._hws_temp, target, dt_seconds, self.params.hws_time_constant_seconds)

        self.registry.set("boiler_ok", 1.0 if self._proven else 0.0)

        self.runtime_seconds += dt_seconds
