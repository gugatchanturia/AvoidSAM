from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionVector:
    x: float
    y: float
    index: int      # 0..31

    def __iter__(self):
        return iter((self.x, self.y))


def _build_directions(n: int = 32) -> list[DirectionVector]:
    step = 2 * math.pi / n
    return [
        DirectionVector(
            x=math.cos(i * step),
            y=math.sin(i * step),
            index=i,
        )
        for i in range(n)
    ]


# The canonical 32-direction list.  Everything that used to iterate
# over Direction (enum) now iterates over DIRECTIONS.
DIRECTIONS: list[DirectionVector] = _build_directions(32)


def nearest_direction(dx: float, dy: float) -> DirectionVector:
    """Return the DirectionVector whose angle is closest to atan2(dy, dx)."""
    if dx == 0.0 and dy == 0.0:
        return DIRECTIONS[0]
    angle = math.atan2(dy, dx)
    step  = 2 * math.pi / len(DIRECTIONS)
    index = round(angle / step) % len(DIRECTIONS)
    return DIRECTIONS[index]


def direction_angle(d: DirectionVector) -> float:
    return math.atan2(d.y, d.x)