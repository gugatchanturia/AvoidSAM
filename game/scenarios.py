from dataclasses import dataclass
from core.vector import Vector2D
from core.directions import nearest_direction, DIRECTIONS


@dataclass(frozen=True)
class Scenario:
    name:         str
    aircraft_pos: Vector2D
    aircraft_dir_angle: float   # radians; used to pick nearest of 32 dirs
    truck_pos:    Vector2D


import math

SCENARIOS = [
    Scenario("Truck below, aircraft east",          Vector2D(2.0,  4.0),  0.0,              Vector2D(10.0, 14.0)),
    Scenario("Truck far southeast",                  Vector2D(2.0,  2.0),  0.0,              Vector2D(28.0, 15.0)),
    Scenario("Truck ahead of flight path",           Vector2D(2.0,  4.0),  0.0,              Vector2D(22.0,  4.0)),
    Scenario("Truck behind flight path",             Vector2D(18.0, 4.0),  0.0,              Vector2D( 2.0, 10.0)),
    Scenario("Diagonal intercept geometry",          Vector2D(2.0,  2.0),  math.pi/4,        Vector2D(20.0,  5.0)),
    Scenario("Likely wait-then-fire",                Vector2D(2.0,  8.0),  0.0,              Vector2D(14.0, 10.0)),
    Scenario("Likely move-then-fire",                Vector2D(2.0,  2.0),  0.0,              Vector2D(28.0, 10.0)),
    Scenario("Likely move-then-wait-then-fire",      Vector2D(2.0,  2.0),  0.0,              Vector2D(24.0, 14.0)),
    Scenario("Truck near aircraft, NW offset",       Vector2D(14.0, 8.0),  0.0,              Vector2D( 8.0,  3.0)),
    Scenario("Aircraft heading south",               Vector2D(16.0, 1.0),  math.pi/2,        Vector2D( 4.0, 10.0)),
    Scenario("Crossing paths geometry",              Vector2D(2.0,  9.0), -math.pi/4,        Vector2D(20.0, 14.0)),
    Scenario("Truck far left, aircraft east",        Vector2D(16.0, 6.0),  0.0,              Vector2D( 2.0, 14.0)),
]