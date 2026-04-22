from dataclasses import dataclass
from core.vector import Vector2D
from core.directions import Direction


@dataclass(frozen=True)
class Scenario:
    name: str
    aircraft_pos: Vector2D
    aircraft_dir: Direction
    truck_pos: Vector2D
    truck_dir: Direction = Direction.N


SCENARIOS: list[Scenario] = [
    Scenario(
        name="Truck below, aircraft east",
        aircraft_pos=Vector2D(2.0, 4.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(10.0, 14.0),
    ),
    Scenario(
        name="Truck far southeast",
        aircraft_pos=Vector2D(2.0, 2.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(28.0, 15.0),
    ),
    Scenario(
        name="Truck ahead of flight path",
        aircraft_pos=Vector2D(2.0, 4.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(22.0, 4.0),
    ),
    Scenario(
        name="Truck behind flight path",
        aircraft_pos=Vector2D(18.0, 4.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(2.0, 10.0),
    ),
    Scenario(
        name="Diagonal intercept geometry",
        aircraft_pos=Vector2D(2.0, 2.0),
        aircraft_dir=Direction.SE,
        truck_pos=Vector2D(20.0, 5.0),
    ),
    Scenario(
        name="Likely wait-then-fire",
        aircraft_pos=Vector2D(2.0, 8.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(14.0, 10.0),
    ),
    Scenario(
        name="Likely move-then-fire",
        aircraft_pos=Vector2D(2.0, 2.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(28.0, 10.0),
    ),
    Scenario(
        name="Likely move-then-wait-then-fire",
        aircraft_pos=Vector2D(2.0, 2.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(24.0, 14.0),
    ),
    Scenario(
        name="Truck near aircraft, NW offset",
        aircraft_pos=Vector2D(14.0, 8.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(8.0, 3.0),
    ),
    Scenario(
        name="Aircraft heading south",
        aircraft_pos=Vector2D(16.0, 1.0),
        aircraft_dir=Direction.S,
        truck_pos=Vector2D(4.0, 10.0),
    ),
    Scenario(
        name="Crossing paths geometry",
        aircraft_pos=Vector2D(2.0, 9.0),
        aircraft_dir=Direction.NE,
        truck_pos=Vector2D(20.0, 14.0),
    ),
    Scenario(
        name="Truck far left, aircraft east",
        aircraft_pos=Vector2D(16.0, 6.0),
        aircraft_dir=Direction.E,
        truck_pos=Vector2D(2.0, 14.0),
    ),
]