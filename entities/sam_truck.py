from dataclasses import dataclass
from core.directions import DirectionVector, DIRECTIONS
from entities.base import BaseEntity


@dataclass
class SAMTruck(BaseEntity):
    has_fired: bool = False