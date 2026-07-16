"""
Site conditions model. Publishes Outside Air Temperature (AV:80) and
Outside Air Humidity (AV:81) that every other equipment model reads.

Instructor changes (e.g. switching to a "winter" preset) are applied as a
target the model approaches smoothly rather than an instant jump, so
downstream equipment (chillers, AHU mixed air, etc.) sees a realistic
transition instead of a step change no real building would ever produce.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.equipment.base import EquipmentModel
from app.registry import PointRegistry


@dataclass
class SiteParameters:
    transition_time_constant_seconds: float = 60.0


class SiteModel(EquipmentModel):
    def __init__(self, equipment_id: str, registry: PointRegistry, parameters: SiteParameters | None = None):
        super().__init__(equipment_id, registry)
        self.params = parameters or SiteParameters()
        self.target_oa_temp_f = 70.0
        self.target_oa_humidity_pct = 50.0
        self._oa_temp = 70.0
        self._oa_humidity = 50.0

    def tick(self, dt_seconds: float) -> None:
        self._oa_temp = self.approach(
            self._oa_temp, self.target_oa_temp_f, dt_seconds, self.params.transition_time_constant_seconds
        )
        self._oa_humidity = self.approach(
            self._oa_humidity, self.target_oa_humidity_pct, dt_seconds, self.params.transition_time_constant_seconds
        )
        self.registry.set("oa_temp", self._oa_temp)
        self.registry.set("oa_humidity", self._oa_humidity)
        self.runtime_seconds += dt_seconds
