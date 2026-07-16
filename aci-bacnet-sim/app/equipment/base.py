"""
Equipment Models base class.

Every piece of simulated mechanical equipment subclasses this. An equipment
model reads commanded values and interlocks from the PointRegistry, advances
its own internal physics/state on each tick(), and writes results back to
the PointRegistry. It never touches bacpypes3 directly and never imports the
transport layer — see Phase 1 architecture, layering rule.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.registry import PointRegistry

logger = logging.getLogger("aci_sim.equipment")


class EquipmentModel(ABC):
    equipment_id: str

    def __init__(self, equipment_id: str, registry: PointRegistry):
        self.equipment_id = equipment_id
        self.registry = registry
        self.runtime_seconds: float = 0.0

    @abstractmethod
    def tick(self, dt_seconds: float) -> None:
        """
        Advance the equipment's simulated state by dt_seconds of simulated
        time. Must read commanded values via self.registry.get_commanded(),
        and publish results via self.registry.set(). Interlocks (if any)
        must be checked first, before any normal command processing — see
        Phase 1 Addendum 2 §5.
        """
        raise NotImplementedError

    @staticmethod
    def approach(current: float, target: float, dt_seconds: float, time_constant_seconds: float) -> float:
        """
        Simple first-order exponential approach toward a target value, used
        by every equipment model to give damper/valve/thermal response a
        realistic lag instead of an instant jump. A larger time_constant
        means slower, more sluggish response.
        """
        if time_constant_seconds <= 0:
            return target
        alpha = 1.0 - pow(2.718281828, -dt_seconds / time_constant_seconds)
        return current + (target - current) * alpha
