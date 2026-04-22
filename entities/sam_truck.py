from dataclasses import dataclass
from core.directions import Direction
from entities.base import BaseEntity


@dataclass
class SAMTruck(BaseEntity):
    has_fired: bool = False

    planned_move_direction: Direction | None = None
    planned_move_steps_remaining: int = 0
    planned_wait_steps_remaining: int = 0
    planned_fire_direction: Direction | None = None