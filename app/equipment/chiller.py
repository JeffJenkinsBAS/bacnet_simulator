"""
Chiller equipment model. One instance per unit (Chiller-1/2/3), each with
its own PointRegistry, but sharing the plant-level Emerg/Refrig Shutdown
Trip interlocks published on ACI-SIM-CHW-PLANT -- passed in as
`plant_registry` so this model can check them without touching bacpypes3
or crossing the transport layer.

Behavior per the governing brief's Chiller expectations:
    - Enable does not produce immediate proof (start delay).
    - Chilled-water supply temperature changes gradually toward setpoint.
    - Condenser/tower side tracks outside air temperature when the tower
      fan is running, drifts toward ambient (with a much longer time
      constant) when it isn't.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class ChillerParameters:
    # Run proof is accelerated for the showcase so a healthy start proves
    # before the command center's 15-real-second failure timer. CHWS thermal
    # pull-down remains intentionally slower.
    start_delay_seconds: float = 10.0
    chws_setpoint_f: float = 44.0
    minimum_chws_setpoint_f: float = 38.0
    maximum_chws_setpoint_f: float = 54.0
    chws_time_constant_seconds: float = 45.0
    chwr_rise_when_loaded_f: float = 10.0
    design_chw_flow_gpm: float = 300.0
    idle_evaporator_temp_f: float = 70.0
    pump_start_delay_seconds: float = 3.0
    isolation_valve_time_constant_seconds: float = 4.0
    tower_fan_start_delay_seconds: float = 5.0
    tower_approach_f: float = 7.0  # CWS approaches ambient WET-bulb + this when the tower fan is running
    tower_time_constant_seconds: float = 30.0
    basin_time_constant_seconds: float = 300.0  # basin tracks OA much more slowly
    no_fan_load_penalty_f: float = 25.0  # CWS rise above OA dry-bulb when rejecting heat with no tower fan
    high_head_trip_f: float = 105.0  # condenser water temp at which the unit trips (auto-recovers when it cools)


class ChillerModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        site_registry: PointRegistry,
        plant_registry: PointRegistry,
        parameters: ChillerParameters | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.site_registry = site_registry
        self.plant_registry = plant_registry
        self.params = parameters or ChillerParameters()

        self._enabled_seconds = 0.0
        self._proven = False
        self._chws_temp = self.params.idle_evaporator_temp_f
        self._chwr_temp = self.params.idle_evaporator_temp_f
        self._evaporator_inlet_temp_f = self.params.idle_evaporator_temp_f
        self._evaporator_flow_gpm = 0.0
        self._evaporator_heat_removed_btuh = 0.0
        self._cws_temp = 75.0
        self._cwr_temp = 80.0
        self._basin_temp = 70.0
        self._chw_pump_running = False
        self._cw_pump_running = False
        self._tower_fan_running = False
        self._chw_pump_frac = 0.0
        self._cw_pump_frac = 0.0
        self._tower_fan_frac = 0.0
        self._chw_iso_frac = 0.0

    @property
    def proven(self) -> bool:
        """Public proof status, mirrored to the plant manager's chillerN_ok points."""
        return self._proven

    @property
    def chws_temp_f(self) -> float:
        return self._chws_temp

    @property
    def chwr_temp_f(self) -> float:
        return self._chwr_temp

    @property
    def evaporator_heat_removed_btuh(self) -> float:
        return self._evaporator_heat_removed_btuh

    def set_evaporator_conditions(
        self,
        *,
        return_temp_f: float,
        flow_gpm: float,
    ) -> None:
        """Accept the parent plant header state for the next thermal tick."""
        self._evaporator_inlet_temp_f = float(return_temp_f)
        self._evaporator_flow_gpm = max(0.0, float(flow_gpm))

    @property
    def chw_pump_running(self) -> bool:
        return self._chw_pump_running

    @property
    def chw_isolation_open(self) -> bool:
        return self._chw_iso_frac > 0.5

    def tick(self, dt_seconds: float) -> None:
        emerg_trip = self.plant_registry.get_commanded("emerg_shutdown_trip") == 1.0
        refrig_trip = self.plant_registry.get_commanded("refrig_shutdown_trip") == 1.0
        try:
            remote_shutdown = self.plant_registry.get_commanded("remote_shutdown") == 1.0
        except KeyError:
            remote_shutdown = False  # plant config without a remote_shutdown point (partial test configs)

        enable = self.registry.get_commanded("chiller_enable") == 1.0
        ss = self.registry.get_commanded("chiller_ss") == 1.0
        chw_iso_cmd = self.registry.get_commanded("chw_iso_valve") == 1.0
        chw_pump_cmd = self.registry.get_commanded("chw_pump_ss") == 1.0
        cw_pump_cmd = self.registry.get_commanded("cw_pump_ss") == 1.0
        ct_fan_cmd = self.registry.get_commanded("ct_fan_ss") == 1.0
        chws_reset = self.registry.get_commanded("chws_stpt_reset")

        run_command = enable and ss
        if remote_shutdown:
            # Chiller Manager's plant-level Remote Shutdown: stops the unit;
            # pumps stay on their own commands (WebCTRL owns pump sequencing).
            run_command = False
        if emerg_trip or refrig_trip:
            run_command = False
            chw_iso_cmd = False
            chw_pump_cmd = False
            cw_pump_cmd = False
            ct_fan_cmd = False

        # Pumps advance first: the flow-proving interlock below needs
        # this tick's pump state, not last tick's.
        self._chw_pump_frac = self.approach(
            self._chw_pump_frac, 1.0 if chw_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._chw_pump_running = self._chw_pump_frac > 0.5
        self._cw_pump_frac = self.approach(
            self._cw_pump_frac, 1.0 if cw_pump_cmd else 0.0, dt_seconds, self.params.pump_start_delay_seconds
        )
        self._cw_pump_running = self._cw_pump_frac > 0.5
        self._tower_fan_frac = self.approach(
            self._tower_fan_frac, 1.0 if ct_fan_cmd else 0.0, dt_seconds, self.params.tower_fan_start_delay_seconds
        )
        self._tower_fan_running = self._tower_fan_frac > 0.5
        self._chw_iso_frac = self.approach(
            self._chw_iso_frac,
            1.0 if chw_iso_cmd else 0.0,
            dt_seconds,
            self.params.isolation_valve_time_constant_seconds,
        )

        # Flow-proving interlock: a real chiller gets no start permit without
        # proven evaporator AND condenser water flow, and trips if flow is
        # lost while running. High condenser water temp also trips the unit
        # (high-head cutout); it auto-recovers here once the loop cools,
        # standing in for a manual-reset lockout for training convenience.
        flow_proven = (
            self.chw_isolation_open
            and self._chw_pump_running
            and self._cw_pump_running
        )
        high_head = self._cws_temp >= self.params.high_head_trip_f
        if run_command and flow_proven and not high_head:
            self._enabled_seconds += dt_seconds
        else:
            self._enabled_seconds = 0.0
        self._proven = (
            run_command and flow_proven and not high_head
            and self._enabled_seconds >= self.params.start_delay_seconds
        )

        setpoint = (
            chws_reset
            if chws_reset is not None
            and self.params.minimum_chws_setpoint_f <= chws_reset <= self.params.maximum_chws_setpoint_f
            else self.params.chws_setpoint_f
        )
        evaporator_flow = (
            self._evaporator_flow_gpm
            if self._chw_pump_running and self.chw_isolation_open
            else 0.0
        )
        if evaporator_flow > 0.0:
            target_chwr = self._evaporator_inlet_temp_f
            if self._proven:
                # The compressor modulates only enough to reach setpoint and
                # cannot remove more than the unit's design 500*GPM*delta-T
                # capacity.  Load therefore determines delta-T; proof alone
                # no longer fabricates a fixed 10 F rise.
                nominal_capacity_btuh = (
                    500.0
                    * self.params.design_chw_flow_gpm
                    * self.params.chwr_rise_when_loaded_f
                )
                required_btuh = max(
                    0.0,
                    500.0
                    * evaporator_flow
                    * (target_chwr - setpoint),
                )
                removed_btuh = min(nominal_capacity_btuh, required_btuh)
                target_chws = target_chwr - (
                    removed_btuh / max(500.0 * evaporator_flow, 1.0)
                )
            else:
                removed_btuh = 0.0
                # With circulation but no compressor, water passes through
                # the evaporator without refrigeration and the loop warms
                # from downstream building load.
                target_chws = target_chwr
        else:
            removed_btuh = 0.0
            # A stopped primary pump isolates this barrel from forced flow,
            # but it still shares the plant's slowly changing water/ambient
            # state.  Do not pin an idle evaporator to an artificial 55 F.
            target_chws = self._evaporator_inlet_temp_f
            target_chwr = self._evaporator_inlet_temp_f

        self._chws_temp = self.approach(
            self._chws_temp,
            target_chws,
            dt_seconds,
            self.params.chws_time_constant_seconds,
        )
        self._chwr_temp = self.approach(
            self._chwr_temp,
            target_chwr,
            dt_seconds,
            self.params.chws_time_constant_seconds,
        )
        self._evaporator_heat_removed_btuh = removed_btuh

        # --- Condenser / tower side ---
        # Evaporative cooling pulls tower water toward ambient WET-bulb, not
        # dry-bulb. Field approximation (adequate mid-range): WB = DB - (100-RH)/5.
        oa_temp = self.site_registry.get("oa_temp")
        try:
            oa_rh = self.site_registry.get("oa_humidity")
        except KeyError:
            oa_rh = 50.0  # partial test site configs may only publish oa_temp
        wet_bulb = min(oa_temp, oa_temp - (100.0 - oa_rh) / 5.0)
        if self._tower_fan_running:
            target_cws = wet_bulb + self.params.tower_approach_f
        elif self._proven:
            # Rejecting heat with no tower fan: condenser water CLIMBS toward
            # a trip, it does not drift down to ambient (the old model had
            # this backwards -- the fan stopping made the water colder).
            target_cws = oa_temp + self.params.no_fan_load_penalty_f
        else:
            target_cws = oa_temp
        self._cws_temp = self.approach(self._cws_temp, target_cws, dt_seconds, self.params.tower_time_constant_seconds)
        nominal_capacity_btuh = max(
            1.0,
            500.0
            * self.params.design_chw_flow_gpm
            * self.params.chwr_rise_when_loaded_f,
        )
        condenser_load_fraction = max(
            0.0,
            min(1.0, self._evaporator_heat_removed_btuh / nominal_capacity_btuh),
        )
        target_cwr = self._cws_temp + 8.0 * condenser_load_fraction
        self._cwr_temp = self.approach(self._cwr_temp, target_cwr, dt_seconds, self.params.tower_time_constant_seconds)
        self._basin_temp = self.approach(self._basin_temp, oa_temp, dt_seconds, self.params.basin_time_constant_seconds)

        self.registry.set("chiller_status", 1.0 if self._proven else 0.0)
        self.registry.set("chw_iso_vlv_sts", 1.0 if self.chw_isolation_open else 0.0)
        self.registry.set("chw_pump_status", 1.0 if self._chw_pump_running else 0.0)
        self.registry.set("cw_pump_status", 1.0 if self._cw_pump_running else 0.0)
        self.registry.set("ct_fan_status", 1.0 if self._tower_fan_running else 0.0)
        self.registry.set("ct_vfd_fault", 0.0)
        self.registry.set("chwr_temp", self._chwr_temp)
        self.registry.set("chws_temp", self._chws_temp)
        self.registry.set("cwr_temp", self._cwr_temp)
        self.registry.set("cws_temp", self._cws_temp)
        self.registry.set("cws_basin_temp", self._basin_temp)

        self.runtime_seconds += dt_seconds
