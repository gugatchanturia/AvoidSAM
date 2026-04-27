from dataclasses import dataclass
from core.vector import Vector2D


@dataclass
class Grid:
    width:  int
    height: int

    def to_tile(self, pos: Vector2D) -> tuple[int, int]:
        return (int(pos.x), int(pos.y))

    def in_bounds(self, pos: Vector2D) -> bool:
        return 0.0 <= pos.x < self.width and 0.0 <= pos.y < self.height