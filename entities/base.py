from __future__ import annotations
from dataclasses import dataclass
from core.vector import Vector2D
from core.directions import DirectionVector, DIRECTIONS
from core.grid import Grid


@dataclass
class BaseEntity:
    position:  Vector2D
    speed:     float
    direction: DirectionVector

    def tile(self, grid: Grid) -> tuple[int, int]:
        return grid.to_tile(self.position)

    def __repr__(self) -> str:
        return (f"{self.__class__.__name__}("
                f"pos={self.position}, "
                f"speed={self.speed:.2f}, "
                f"dir=({self.direction.x:.3f},{self.direction.y:.3f}))")