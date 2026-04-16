from dataclasses import dataclass
from entities.base import BaseEntity

@dataclass
class SAMTruck(BaseEntity):
    has_fired: bool = False