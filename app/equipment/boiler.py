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
from app.faults import FaultType
from app.registry import PointRegistry


@dataclass
class BoilerParameters:
    # Representative gas-boiler pre-purge and trial-for-ignition sequence.
    # OEM/site-specific values remain parameter overrides.
    purge_seconds: float = 30.0
    ignition_seconds: float = 5.0
    hws_setpoint_f: float = 180.0
    hws_time_constant_seconds: float = 12.0
    pump_start_delay_seconds: float = 3.0
    minimum_hws_setpoint_f: float = 100.0
    maximum_hws_setpoint_f: float = 200.0
    nominal_output_capacity_btuh: float = 600_000.0
    minimum_firing_fraction: float = 0.20
    firing_time_constant_seconds: float = 15.0
    firing_deadband_f: float = 1.0
    maximum_leaving_temp_above_setpoint_f: float = 5.0
    standby_temp_f: float = 100.0


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
        self._hws_temp = self.params.standby_temp_f
        self._hwr_temp = self.params.standby_temp_f
        self._hydronic_flow_gpm = 0.0
        self._hydronic_conditions_configured = False
        self._firing_fraction = 0.0
        self._heat_output_btuh = 0.0
        self._active_setpoint_f = self.params.hws_setpoint_f
        self._circ_pump_running = False
        self._hw_pump_running = False
        self._circ_pump_frac = 0.0
        self._hw_pump_frac = 0.0

    @property
    def proven(self) -> bool:
        """Public proof status, mirrored to the Boiler Manager's boilerN_ok points."""
        return self._proven

    @property
    def hws_temp_f(self) -> float:
        return self._hws_temp

    @property
    def hwr_temp_f(self) -> float:
        return self._hwr_temp

    @property
    def flow_gpm(self) -> float:
        return self._hydronic_flow_gpm

    @property
    def firing_rate_pct(self) -> float:
        return 100.0 * self._firing_fraction

    @property
    def heat_output_btuh(self) -> float:
        return self._heat_output_btuh

    @property
    def active_setpoint_f(self) -> float:
        return self._active_setpoint_f

    def set_hydronic_conditions(self, return_temp_f: float, flow_gpm: float) -> None:
        """Receive common-return temperature and this boiler's branch flow."""
        self._hwr_temp = float(return_temp_f)
        self._hydronic_flow_gpm = max(0.0, float(flow_gpm))
        self._hydronic_conditions_configured = True

    @property
    def hw_pump_running(self) -> bool:
        """Distribution-pump proof used by downstream AHU/VAV coils."""
        return self._hw_pump_running

    @property
    def circ_pump_running(self) -> bool:
        return self._circ_pump_running

    def _forced_off(self, alias: str) -> bool:
        parameters = self.registry.point_fault_parameters(alias, FaultType.forced_status)
        return parameters is not None and not bool(parameters.get("value"))

    def tick(self, dt_seconds: float) -> None:
        unit_ss = self.registry.get_commanded("boiler_ss") == 1.0
        ss = unit_ss
        if self.manager_registry is not None and self.manager_enable_alias is not None:
            ss = ss or (self.manager_registry.get_commanded(self.manager_enable_alias) == 1.0)
        # A stuck-false unit start input represents a failed local permissive
        # or ignition chain. The manager enable cannot bypass that physical
        # fault merely because it is another logical source of the request.
        stuck_start = self.registry.point_fault_parameters(
            "boiler_ss", FaultType.stuck_value
        )
        if stuck_start is not None and not bool(stuck_start.get("value", unit_ss)):
            ss = False
        circ_pump_cmd = self.registry.get_commanded("circ_pump_ss") == 1.0
        hw_pump_cmd = self.registry.get_commanded("hw_pump_ss") == 1.0
        hws_reset = self.registry.get_commanded("hws_stpt_reset")

        # Pumps advance first: the low-water/flow interlock below needs this
        # tick's pump state.
        self._circ_pump_frac = self.approach(
            self._circ_pump_frac, 1.0 if circ_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._circ_pump_running = self._circ_pump_frac > 0.5
        if self._forced_off("circ_pump_status"):
            self._circ_pump_running = False
        self._hw_pump_frac = self.approach(
            self._hw_pump_frac, 1.0 if hw_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._hw_pump_running = self._hw_pump_frac > 0.5
        if self._forced_off("hw_pump_status"):
            self._hw_pump_running = False

        # Flow interlock: no circulating pump -> no ignition permit (real
        # boilers lock out on low-water/no-flow); losing the pump mid-run
        # drops proof and restarts the purge+ignition sequence.
        physical_proof_failure = self._forced_off("boiler_ok")
        if ss and self._circ_pump_running and not physical_proof_failure:
            self._enabled_seconds += dt_seconds
        else:
            self._enabled_seconds = 0.0
        self._proven = (
            ss and self._circ_pump_running
            and self._enabled_seconds >= (self.params.purge_seconds + self.params.ignition_seconds)
            and not physical_proof_failure
        )

        setpoint = (
            hws_reset
            if hws_reset is not None and hws_reset >= self.params.minimum_hws_setpoint_f
            else self.params.hws_setpoint_f
        )
        setpoint = max(
            self.params.minimum_hws_setpoint_f,
            min(self.params.maximum_hws_setpoint_f, setpoint),
        )
        self._active_setpoint_f = setpoint

        # In the integrated graph, firing is load-driven: the boiler must
        # replace 500 * GPM * delta-T, and cannot add useful heat without
        # circulation. Standalone tests retain the legacy warm-up response.
        if self._hydronic_conditions_configured:
            flow = self._hydronic_flow_gpm
            required_btuh = 500.0 * flow * max(0.0, setpoint - self._hwr_temp)
            if not self._proven or flow <= 0.01 or self._hws_temp > setpoint + self.params.firing_deadband_f:
                target_firing = 0.0
            else:
                target_firing = min(
                    1.0,
                    max(
                        self.params.minimum_firing_fraction,
                        required_btuh / max(self.params.nominal_output_capacity_btuh, 1.0),
                    ),
                )
            self._firing_fraction = (
                self.approach(
                    self._firing_fraction,
                    target_firing,
                    dt_seconds,
                    self.params.firing_time_constant_seconds,
                )
                if self._proven
                else 0.0
            )
            useful_heat = (
                self._firing_fraction * self.params.nominal_output_capacity_btuh
                if self._proven
                else 0.0
            )
            maximum_leaving = setpoint + self.params.maximum_leaving_temp_above_setpoint_f
            useful_heat = min(
                useful_heat,
                500.0 * flow * max(0.0, maximum_leaving - self._hwr_temp),
            ) if flow > 0.01 else 0.0
            self._heat_output_btuh = useful_heat
            target = (
                self._hwr_temp + useful_heat / max(500.0 * flow, 1.0)
                if flow > 0.01
                else self._hwr_temp
            )
        else:
            self._firing_fraction = (
                self.approach(
                    self._firing_fraction,
                    1.0,
                    dt_seconds,
                    self.params.firing_time_constant_seconds,
                )
                if self._proven
                else 0.0
            )
            self._heat_output_btuh = 0.0
            target = setpoint if self._proven else self.params.standby_temp_f

        self._hws_temp = self.approach(
            self._hws_temp,
            target,
            dt_seconds,
            self.params.hws_time_constant_seconds,
        )

        self.registry.set("boiler_ok", 1.0 if self._proven else 0.0)
        aliases = set(self.registry.all_points())
        telemetry = {
            "hwr_temp": self._hwr_temp,
            "hws_temp": self._hws_temp,
            "boiler_flow": self._hydronic_flow_gpm,
            "firing_rate": self.firing_rate_pct,
            "circ_pump_status": 1.0 if self._circ_pump_running else 0.0,
            "hw_pump_status": 1.0 if self._hw_pump_running else 0.0,
        }
        for alias, value in telemetry.items():
            if alias in aliases:
                self.registry.set(alias, value)

        self.runtime_seconds += dt_seconds

    def operating_snapshot(self) -> dict:
        return {
            "proven": self.proven,
            "circ_pump_running": self.circ_pump_running,
            "hw_pump_running": self.hw_pump_running,
            "hwr_temp_f": round(self.hwr_temp_f, 2),
            "hws_temp_f": round(self.hws_temp_f, 2),
            "flow_gpm": round(self.flow_gpm, 2),
            "firing_rate_pct": round(self.firing_rate_pct, 2),
            "heat_output_btuh": round(self.heat_output_btuh, 1),
            "setpoint_f": round(self.active_setpoint_f, 2),
        }
