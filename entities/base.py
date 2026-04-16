from dataclasses import dataclass
from core.vector import Vector2D
from core.directions import Direction
from core.grid import Grid

@dataclass
class BaseEntity:
    position: Vector2D
    speed: float
    direction: Direction

    def tile(self, grid: Grid) -> tuple[int, int]:
        return grid.to_tile(self.position)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(pos={self.position}, speed={self.speed}, dir={self.direction.name})"