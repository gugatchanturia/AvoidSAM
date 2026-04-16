from dataclasses import dataclass
from core.vector import Vector2D

@dataclass
class Grid:
    width: int
    height: int

    def to_tile(self, pos: Vector2D) -> tuple[int, int]:
        """Convert a float position to integer tile coordinates (floor)."""
        return (int(pos.x), int(pos.y))

    def in_bounds(self, pos: Vector2D) -> bool:
        """Return True if the float position falls within the grid."""
        tx, ty = self.to_tile(pos)
        return 0 <= tx < self.width and 0 <= ty < self.height