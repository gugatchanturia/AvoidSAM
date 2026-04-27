from dataclasses import dataclass, field
from core.grid import Grid
from core.vector import Vector2D
from entities.aircraft import Aircraft
from entities.sam_truck import SAMTruck
from entities.missile import Missile


@dataclass
class GameState:
    grid:      Grid
    aircraft:  Aircraft
    sam_truck: SAMTruck
    missiles:  list[Missile]          = field(default_factory=list)
    tick:      int                    = 0
    intercepted: bool                 = False
    failed:    bool                   = False
    # Radar history: list of observed aircraft positions (one per tick)
    aircraft_history: list[Vector2D]  = field(default_factory=list)