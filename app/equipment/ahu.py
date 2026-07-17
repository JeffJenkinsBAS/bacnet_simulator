"""
AHU-1 equipment model.

Point set and behavior per the Phase 1 architecture (original doc's Mechanical
Behavior Expectations, plus the hard-interlock design in Phase 1 Addendum 2/3):

    cooling_valve, heating_valve, preheat_valve, economizer   AO  WebCTRL -> sim, 0-100%
    ra_fan_ss, sa_fan_ss                                       BO  WebCTRL -> sim
    high_static_pressure_trip, freezestat_trip                  BV  WebCTRL -> sim, INTERLOCK
    ahu_ma_temp, ahu_ra_temp, ahu_ra_humidity, ahu_sa_temp       AI  sim -> WebCTRL
    ra_smoke_detector, sa_smoke_detector                          BI  sim -> WebCTRL (fault-library driven; idle in Phase 3 baseline)

Note: there is deliberately no published fan-status point -- the governing
Network I/O report never specified one as a simulator-facing network point
for AHU-1 (RA Fan SS / SA Fan SS are commands only), so none is invented
here. Fan "running" state is tracked internally to drive the thermal model,
just not exposed on BACnet.

Interlocks are checked first, every tick, ahead of normal command
processing -- see Phase 1 Addendum 2 §5. While either trip is active, the
AHU forces itself into a fixed safe state regardless of what WebCTRL is
currently commanding, the way a real hardwired safety circuit would.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class AhuParameters:
    fan_start_time_constant_seconds: float = 3.0
    economizer_time_constant_seconds: float = 15.0
    plenum_idle_time_constant_seconds: float = 300.0  # MA drift rate with fans off (no forced airflow)
    coil_time_constant_seconds: float = 20.0
    space_time_constant_seconds: float = 120.0
    chilled_water_leaving_temp_f: float = 44.0
    hot_water_leaving_temp_f: float = 140.0
    preheat_leaving_temp_f: float = 55.0
    ra_setpoint_f: float = 72.0
    ra_humidity_setpoint_pct: float = 50.0


class AhuModel(EquipmentModel):
    def __init__(
        self,
        equipment_id: str,
        registry: PointRegistry,
        site_registry: PointRegistry,
        parameters: AhuParameters | None = None,
    ):
        super().__init__(equipment_id, registry)
        self.site_registry = site_registry
        self.params = parameters or AhuParameters()

        self.fan_running = False
        self._fan_running_frac = 0.0
        self._ma_temp = 60.0
        self._ra_temp = self.params.ra_setpoint_f
        self._ra_humidity = self.params.ra_humidity_setpoint_pct
        self._sa_temp = 55.0

        # Exposed to other equipment models (VAV boxes) via direct in-process
        # reference, not through BACnet -- see SingleDuctVavModel(ahu_model=...).
        self.available_static_pressure_inwc = 1.2

    @property
    def effective_sa_temp_f(self) -> float:
        return self._sa_temp

    def tick(self, dt_seconds: float) -> None:
        # --- Interlocks first, ahead of any normal command processing ---
        high_static_trip = self.registry.get_commanded("high_static_pressure_trip") == 1.0
        freezestat_trip = self.registry.get_commanded("freezestat_trip") == 1.0

        sa_fan_cmd = self.registry.get_commanded("sa_fan_ss") == 1.0
        ra_fan_cmd = self.registry.get_commanded("ra_fan_ss") == 1.0
        cooling_pct = max(0.0, min(100.0, self.registry.get_commanded("cooling_valve") or 0.0))
        heating_pct = max(0.0, min(100.0, self.registry.get_commanded("heating_valve") or 0.0))
        preheat_pct = max(0.0, min(100.0, self.registry.get_commanded("preheat_valve") or 0.0))
        econ_pct = max(0.0, min(100.0, self.registry.get_commanded("economizer") or 0.0))

        if high_static_trip:
            # Hard shutdown: both fans forced off regardless of command.
            sa_fan_cmd = False
            ra_fan_cmd = False
        if freezestat_trip:
            # Real freezestat behavior: kill the supply fan, drive the OA
            # damper closed, drive heating valve to fail-safe fully-open to
            # protect the coil (standard freeze response: stop fan, close OA,
            # open HW valve -- not just the first and last).
            sa_fan_cmd = False
            econ_pct = 0.0
            heating_pct = 100.0
        if not sa_fan_cmd:
            # OA dampers spring-return closed whenever the supply fan is off.
            econ_pct = 0.0

        self._fan_running_frac = self.approach(self._fan_running_frac, 1.0 if sa_fan_cmd else 0.0,
                                                dt_seconds, self.params.fan_start_time_constant_seconds)
        self.fan_running = self._fan_running_frac > 0.5

        oa_temp = self.site_registry.get("oa_temp")

        # --- Mixed air temp: economizer blends OA and RA, then preheat adds heat ---
        target_ma = (econ_pct / 100.0) * oa_temp + (1.0 - econ_pct / 100.0) * self._ra_temp
        # No airflow -> no forced blend; the plenum slowly equalizes with the
        # building instead of responding at full economizer speed.
        ma_tc = (
            self.params.economizer_time_constant_seconds
            if self.fan_running
            else self.params.plenum_idle_time_constant_seconds
        )
        self._ma_temp = self.approach(self._ma_temp, target_ma, dt_seconds, ma_tc)
        if preheat_pct > 0.0 and self._ma_temp < self.params.preheat_leaving_temp_f:
            preheat_target = self._ma_temp + (preheat_pct / 100.0) * (self.params.preheat_leaving_temp_f - self._ma_temp)
            self._ma_temp = self.approach(self._ma_temp, preheat_target, dt_seconds, self.params.coil_time_constant_seconds)

        # --- Supply air temp: only changes while the fan is actually moving air ---
        if self.fan_running:
            target_sa = self._ma_temp
            if cooling_pct > 0.0:
                target_sa -= (cooling_pct / 100.0) * (self._ma_temp - self.params.chilled_water_leaving_temp_f)
            if heating_pct > 0.0:
                target_sa += (heating_pct / 100.0) * (self.params.hot_water_leaving_temp_f - self._ma_temp)
            self._sa_temp = self.approach(self._sa_temp, target_sa, dt_seconds, self.params.coil_time_constant_seconds)
        # fan off: SA temp just holds at whatever it last was (dead air in the duct)

        # --- Return air: slow drift toward the space setpoint, nudged by SA temp when fan runs ---
        ra_target = self.params.ra_setpoint_f
        if self.fan_running:
            ra_target = 0.9 * self.params.ra_setpoint_f + 0.1 * self._sa_temp
        self._ra_temp = self.approach(self._ra_temp, ra_target, dt_seconds, self.params.space_time_constant_seconds)
        self._ra_humidity = self.approach(
            self._ra_humidity, self.params.ra_humidity_setpoint_pct, dt_seconds, self.params.space_time_constant_seconds
        )

        self.registry.set("ahu_ma_temp", self._ma_temp)
        self.registry.set("ahu_ra_temp", self._ra_temp)
        self.registry.set("ahu_ra_humidity", self._ra_humidity)
        self.registry.set("ahu_sa_temp", self._sa_temp)

        self.runtime_seconds += dt_seconds
