"""
Exhaust fan equipment model.

    exh_air_damper   AO  WebCTRL -> sim
    exh_fan_ss        BO  WebCTRL -> sim
    fan_status         BI  sim -> WebCTRL, after a proof delay
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class ExhaustFanParameters:
    proof_delay_seconds: float = 4.0


class ExhaustFanModel(EquipmentModel):
    def __init__(self, equipment_id: str, registry: PointRegistry, parameters: ExhaustFanParameters | None = None):
        super().__init__(equipment_id, registry)
        self.params = parameters or ExhaustFanParameters()
        self._running = False
        self._running_frac = 0.0

    def tick(self, dt_seconds: float) -> None:
        cmd = self.registry.get_commanded("exh_fan_ss") == 1.0
        self._running_frac = self.approach(self._running_frac, 1.0 if cmd else 0.0, dt_seconds, self.params.proof_delay_seconds)
        self._running = self._running_frac > 0.5
        self.registry.set("fan_status", 1.0 if self._running else 0.0)
        self.runtime_seconds += dt_seconds
