from enum import Enum
from math import sqrt
from core.vector import Vector2D

_D = 1.0 / sqrt(2)

class Direction(Enum):
    N  = Vector2D( 0.0,  -1.0)
    NE = Vector2D( _D,   -_D)
    E  = Vector2D( 1.0,   0.0)
    SE = Vector2D( _D,    _D)
    S  = Vector2D( 0.0,   1.0)
    SW = Vector2D(-_D,    _D)
    W  = Vector2D(-1.0,   0.0)
    NW = Vector2D(-_D,   -_D)