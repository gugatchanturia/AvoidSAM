from dataclasses import dataclass
from entities.base import BaseEntity


@dataclass
class Missile(BaseEntity):
    active: bool = True
    steps_alive: int = 0
