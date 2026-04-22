from dataclasses import dataclass

from core.directions import Direction
from core.vector import Vector2D
from core.grid import Grid
from entities.sam_truck import SAMTruck
from entities.aircraft import Aircraft
from entities.missile import Missile


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruckPlan:
    plan_type: str          # "fire_now" | "wait_then_fire" | "move_then_fire" | "move_then_wait_then_fire"
    move_direction: Direction | None
    move_steps: int         # ticks truck moves before stopping
    wait_steps: int         # ticks truck waits after moving, before firing
    fire_direction: Direction
    missile_steps: int      # ticks from launch until intercept
    fire_tick: int          # move_steps + wait_steps  (ticks from NOW until launch)
    intercept_tick: int     # fire_tick + missile_steps


# ---------------------------------------------------------------------------
# Internal simulation helpers
# ---------------------------------------------------------------------------

def _copy_pos(pos: Vector2D) -> Vector2D:
    return Vector2D(pos.x, pos.y)


def _simulate_positions(
    start: Vector2D,
    direction: Direction,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
    is_missile: bool,
) -> list[Vector2D]:
    """
    Returns a list of positions of length up to max_steps + 1.
    Index 0 = starting position (before any movement).
    Index k = position after k ticks of movement.
    Missile: stops appending once it would leave bounds.
    Aircraft/truck: clamps to last valid position and continues.
    """
    pos = _copy_pos(start)
    positions = [_copy_pos(pos)]
    for _ in range(max_steps):
        new_pos = pos + direction.value * (speed * dt)
        if not grid.in_bounds(new_pos):
            if is_missile:
                break
            positions.append(_copy_pos(pos))   # clamped
            continue
        pos = new_pos
        positions.append(_copy_pos(pos))
    return positions


def _simulate_tiles(
    start: Vector2D,
    direction: Direction,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
    is_missile: bool,
) -> list[tuple[int, int]]:
    return [
        grid.to_tile(p)
        for p in _simulate_positions(start, direction, speed, dt, grid, max_steps, is_missile)
    ]


def _best_missile_intercept(
    fire_pos: Vector2D,
    aircraft_tiles: list[tuple[int, int]],
    fire_tick: int,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int,
) -> tuple[Direction, int] | None:
    """
    Try all 8 missile directions from fire_pos.

    aircraft_tiles[i] = aircraft tile at tick i relative to NOW (index 0 = current).
    fire_tick         = ticks from NOW until launch.
    missile_steps k   = missile has moved k ticks after launch.
    Interception when missile_tiles[k] == aircraft_tiles[fire_tick + k].

    Returns (best_direction, missile_steps) or None.
    """
    best_direction = None
    best_missile_steps = max_future_steps + 1

    for candidate in Direction:
        missile_tiles = _simulate_tiles(
            fire_pos, candidate, missile_speed,
            dt, grid, max_future_steps, is_missile=True,
        )
        # missile_tiles[0] is the launch tile (no movement yet)
        # missile_tiles[k] is after k ticks of missile movement
        for k in range(1, len(missile_tiles)):
            ac_index = fire_tick + k
            if ac_index >= len(aircraft_tiles):
                break
            if missile_tiles[k] == aircraft_tiles[ac_index]:
                if k < best_missile_steps:
                    best_missile_steps = k
                    best_direction = candidate
                break

    if best_direction is None:
        return None
    return (best_direction, best_missile_steps)


# ---------------------------------------------------------------------------
# Per-category plan finders
# ---------------------------------------------------------------------------

def _find_fire_now(
    truck_pos: Vector2D,
    aircraft_tiles: list[tuple[int, int]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    result = _best_missile_intercept(
        truck_pos, aircraft_tiles,
        fire_tick=0,
        missile_speed=missile_speed,
        dt=dt, grid=grid,
        max_future_steps=horizon,
    )
    if result is None:
        return None
    fire_dir, missile_steps = result
    return TruckPlan(
        plan_type="fire_now",
        move_direction=None,
        move_steps=0,
        wait_steps=0,
        fire_direction=fire_dir,
        missile_steps=missile_steps,
        fire_tick=0,
        intercept_tick=missile_steps,
    )


def _find_wait_then_fire(
    truck_pos: Vector2D,
    aircraft_tiles: list[tuple[int, int]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best_plan: TruckPlan | None = None

    for wait_steps in range(1, horizon):
        remaining = horizon - wait_steps
        if remaining <= 0:
            break
        result = _best_missile_intercept(
            truck_pos, aircraft_tiles,
            fire_tick=wait_steps,
            missile_speed=missile_speed,
            dt=dt, grid=grid,
            max_future_steps=remaining,
        )
        if result is None:
            continue
        fire_dir, missile_steps = result
        candidate = TruckPlan(
            plan_type="wait_then_fire",
            move_direction=None,
            move_steps=0,
            wait_steps=wait_steps,
            fire_direction=fire_dir,
            missile_steps=missile_steps,
            fire_tick=wait_steps,
            intercept_tick=wait_steps + missile_steps,
        )
        if best_plan is None or _plan_key(candidate) < _plan_key(best_plan):
            best_plan = candidate

    return best_plan


def _find_move_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_tiles: list[tuple[int, int]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best_plan: TruckPlan | None = None

    for move_dir in Direction:
        truck_positions = _simulate_positions(
            truck_pos, move_dir, truck_speed,
            dt, grid, horizon, is_missile=False,
        )
        max_move_steps = len(truck_positions) - 1

        for move_steps in range(1, max_move_steps + 1):
            fire_tick = move_steps
            remaining = horizon - fire_tick
            if remaining <= 0:
                break
            if fire_tick >= len(aircraft_tiles):
                break

            staging_pos = truck_positions[move_steps]
            result = _best_missile_intercept(
                staging_pos, aircraft_tiles,
                fire_tick=fire_tick,
                missile_speed=missile_speed,
                dt=dt, grid=grid,
                max_future_steps=remaining,
            )
            if result is None:
                continue

            fire_dir, missile_steps = result
            candidate = TruckPlan(
                plan_type="move_then_fire",
                move_direction=move_dir,
                move_steps=move_steps,
                wait_steps=0,
                fire_direction=fire_dir,
                missile_steps=missile_steps,
                fire_tick=fire_tick,
                intercept_tick=fire_tick + missile_steps,
            )
            if best_plan is None or _plan_key(candidate) < _plan_key(best_plan):
                best_plan = candidate

    return best_plan


def _find_move_then_wait_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_tiles: list[tuple[int, int]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best_plan: TruckPlan | None = None

    for move_dir in Direction:
        truck_positions = _simulate_positions(
            truck_pos, move_dir, truck_speed,
            dt, grid, horizon, is_missile=False,
        )
        max_move_steps = len(truck_positions) - 1

        for move_steps in range(1, max_move_steps + 1):
            staging_pos = truck_positions[move_steps]

            for wait_steps in range(1, horizon - move_steps + 1):
                fire_tick = move_steps + wait_steps
                remaining = horizon - fire_tick
                if remaining <= 0:
                    break
                if fire_tick >= len(aircraft_tiles):
                    break

                result = _best_missile_intercept(
                    staging_pos, aircraft_tiles,
                    fire_tick=fire_tick,
                    missile_speed=missile_speed,
                    dt=dt, grid=grid,
                    max_future_steps=remaining,
                )
                if result is None:
                    continue

                fire_dir, missile_steps = result
                candidate = TruckPlan(
                    plan_type="move_then_wait_then_fire",
                    move_direction=move_dir,
                    move_steps=move_steps,
                    wait_steps=wait_steps,
                    fire_direction=fire_dir,
                    missile_steps=missile_steps,
                    fire_tick=fire_tick,
                    intercept_tick=fire_tick + missile_steps,
                )
                if best_plan is None or _plan_key(candidate) < _plan_key(best_plan):
                    best_plan = candidate

    return best_plan


# ---------------------------------------------------------------------------
# Plan sorting key and top-level planner
# ---------------------------------------------------------------------------

def _plan_key(p: TruckPlan) -> tuple:
    return (p.intercept_tick, p.fire_tick, p.move_steps)


def find_best_truck_plan(
    truck: SAMTruck,
    aircraft: Aircraft,
    truck_speed: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 40,
) -> tuple[
    TruckPlan | None,   # fire_now
    TruckPlan | None,   # wait_then_fire
    TruckPlan | None,   # move_then_fire
    TruckPlan | None,   # move_then_wait_then_fire
    TruckPlan | None,   # best overall
]:
    aircraft_tiles = _simulate_tiles(
        aircraft.position, aircraft.direction, aircraft.speed,
        dt, grid, max_future_steps, is_missile=False,
    )

    fire_now   = _find_fire_now(truck.position, aircraft_tiles, missile_speed, dt, grid, max_future_steps)
    wait_plan  = _find_wait_then_fire(truck.position, aircraft_tiles, missile_speed, dt, grid, max_future_steps)
    move_plan  = _find_move_then_fire(truck.position, truck_speed, aircraft_tiles, missile_speed, dt, grid, max_future_steps)
    mwf_plan   = _find_move_then_wait_then_fire(truck.position, truck_speed, aircraft_tiles, missile_speed, dt, grid, max_future_steps)

    candidates = [p for p in (fire_now, wait_plan, move_plan, mwf_plan) if p is not None]
    best = min(candidates, key=_plan_key) if candidates else None

    return fire_now, wait_plan, move_plan, mwf_plan, best


# ---------------------------------------------------------------------------
# Launch helpers
# ---------------------------------------------------------------------------

def find_launch_solution(
    truck: SAMTruck,
    aircraft: Aircraft,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 30,
) -> tuple[Direction, int] | None:
    aircraft_tiles = _simulate_tiles(
        aircraft.position, aircraft.direction, aircraft.speed,
        dt, grid, max_future_steps, is_missile=False,
    )
    return _best_missile_intercept(
        truck.position, aircraft_tiles,
        fire_tick=0,
        missile_speed=missile_speed,
        dt=dt, grid=grid,
        max_future_steps=max_future_steps,
    )


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


def launch_missile_in_direction(
    truck: SAMTruck,
    missile_speed: float,
    direction: Direction,
) -> Missile | None:
    if truck.has_fired:
        return None
    truck.has_fired = True
    return Missile(
        position=Vector2D(truck.position.x, truck.position.y),
        speed=missile_speed,
        direction=direction,
        active=True,
    )


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
        position=_copy_pos(truck.position),
        speed=missile_speed,
        direction=direction,
        active=True,
    )