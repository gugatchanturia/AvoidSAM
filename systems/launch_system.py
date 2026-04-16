from core.directions import Direction
from core.vector import Vector2D
from core.grid import Grid
from entities.sam_truck import SAMTruck
from entities.aircraft import Aircraft
from entities.missile import Missile


def _simulate_positions(
    start: Vector2D,
    direction: Direction,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
    is_missile: bool,
) -> list[tuple[int, int]]:
    pos = Vector2D(start.x, start.y)
    tiles = []
    for _ in range(max_steps):
        new_pos = pos + direction.value * (speed * dt)
        if not grid.in_bounds(new_pos):
            if is_missile:
                break
            else:
                tiles.append(grid.to_tile(pos))
                continue
        pos = new_pos
        tiles.append(grid.to_tile(pos))
    return tiles


def find_launch_solution(
    truck: SAMTruck,
    aircraft: Aircraft,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 30,
) -> tuple[Direction, int] | None:
    aircraft_tiles = _simulate_positions(
        aircraft.position, aircraft.direction, aircraft.speed,
        dt, grid, max_future_steps, is_missile=False,
    )

    best_direction = None
    best_step = max_future_steps + 1

    for candidate in Direction:
        missile_tiles = _simulate_positions(
            truck.position, candidate, missile_speed,
            dt, grid, max_future_steps, is_missile=True,
        )

        for step, (m_tile, a_tile) in enumerate(zip(missile_tiles, aircraft_tiles)):
            if m_tile == a_tile:
                if step < best_step:
                    best_step = step
                    best_direction = candidate
                break

    if best_direction is None:
        return None

    return (best_direction, best_step)


def find_launch_direction(
    truck: SAMTruck,
    aircraft: Aircraft,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 30,
) -> Direction | None:
    result = find_launch_solution(truck, aircraft, missile_speed, dt, grid, max_future_steps)
    return result[0] if result is not None else None


def launch_missile(
    truck: SAMTruck,
    aircraft: Aircraft,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 30,
) -> Missile | None:
    if truck.has_fired:
        return None

    result = find_launch_solution(truck, aircraft, missile_speed, dt, grid, max_future_steps)

    if result is None:
        return None

    direction, _ = result
    truck.has_fired = True

    return Missile(
        position=Vector2D(truck.position.x, truck.position.y),
        speed=missile_speed,
        direction=direction,
        active=True,
    )