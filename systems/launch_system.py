from __future__ import annotations
import math
from dataclasses import dataclass

from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from core.vector import Vector2D
from core.grid import Grid
from entities.sam_truck import SAMTruck
from entities.aircraft import Aircraft
from entities.missile import Missile
from systems.collision_system import HIT_RADIUS


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruckPlan:
    plan_type:      str
    move_direction: DirectionVector | None
    move_steps:     int
    wait_steps:     int
    fire_direction: DirectionVector
    missile_steps:  int
    fire_tick:      int
    intercept_tick: int


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def _copy_pos(pos: Vector2D) -> Vector2D:
    return Vector2D(pos.x, pos.y)


def _simulate_positions(
    start:     Vector2D,
    direction: DirectionVector,
    speed:     float,
    dt:        float,
    grid:      Grid,
    max_steps: int,
    is_missile: bool,
) -> list[Vector2D]:
    """
    Returns positions list; index 0 = start (before movement).
    Missile  : stops if it would leave bounds.
    Aircraft/Truck : clamps to last valid pos and continues.
    """
    pos       = _copy_pos(start)
    positions = [_copy_pos(pos)]
    for _ in range(max_steps):
        new_pos = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        if not grid.in_bounds(new_pos):
            if is_missile:
                break
            positions.append(_copy_pos(pos))   # clamped
            continue
        pos = new_pos
        positions.append(_copy_pos(pos))
    return positions


def _intercept_step(
    fire_pos:       Vector2D,
    aircraft_positions: list[Vector2D],
    fire_tick:      int,
    missile_dir:    DirectionVector,
    missile_speed:  float,
    dt:             float,
    grid:           Grid,
    max_steps:      int,
) -> int | None:
    """
    Simulate missile from fire_pos in missile_dir.
    Return the missile step k (>=1) at which distance to aircraft <= HIT_RADIUS,
    comparing missile_positions[k] vs aircraft_positions[fire_tick + k].
    Returns None if no intercept within max_steps.
    """
    missile_pos = _copy_pos(fire_pos)
    for k in range(1, max_steps + 1):
        new_missile = Vector2D(
            missile_pos.x + missile_dir.x * missile_speed * dt,
            missile_pos.y + missile_dir.y * missile_speed * dt,
        )
        if not grid.in_bounds(new_missile):
            return None
        missile_pos = new_missile

        ac_index = fire_tick + k
        if ac_index >= len(aircraft_positions):
            return None

        dist = missile_pos.distance_to(aircraft_positions[ac_index])
        if dist <= HIT_RADIUS:
            return k

    return None


def _best_missile_for_position(
    fire_pos:           Vector2D,
    aircraft_positions: list[Vector2D],
    fire_tick:          int,
    missile_speed:      float,
    dt:                 float,
    grid:               Grid,
    remaining_horizon:  int,
) -> tuple[DirectionVector, int] | None:
    best_dir   = None
    best_steps = remaining_horizon + 1

    for candidate in DIRECTIONS:
        k = _intercept_step(
            fire_pos, aircraft_positions, fire_tick,
            candidate, missile_speed, dt, grid, remaining_horizon,
        )
        if k is not None and k < best_steps:
            best_steps = k
            best_dir   = candidate

    if best_dir is None:
        return None
    return (best_dir, best_steps)


# ---------------------------------------------------------------------------
# Per-category finders
# ---------------------------------------------------------------------------

def _plan_key(p: TruckPlan, current_dir: DirectionVector) -> tuple:
    direction_penalty = (
        0 if (p.move_direction is None or p.move_direction is current_dir) else 1
    )
    return (p.intercept_tick + direction_penalty, p.fire_tick, p.move_steps, direction_penalty)


def _find_fire_now(
    truck_pos: Vector2D,
    aircraft_positions: list[Vector2D],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    result = _best_missile_for_position(
        truck_pos, aircraft_positions, 0, missile_speed, dt, grid, horizon,
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
    aircraft_positions: list[Vector2D],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best = None
    for wait_steps in range(1, horizon):
        remaining = horizon - wait_steps
        if remaining <= 0 or wait_steps >= len(aircraft_positions):
            break
        result = _best_missile_for_position(
            truck_pos, aircraft_positions, wait_steps, missile_speed, dt, grid, remaining,
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
        if best is None or (candidate.intercept_tick, candidate.fire_tick) < (best.intercept_tick, best.fire_tick):
            best = candidate
    return best


def _find_move_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_positions: list[Vector2D],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best = None
    for move_dir in DIRECTIONS:
        truck_positions = _simulate_positions(
            truck_pos, move_dir, truck_speed, dt, grid, horizon, is_missile=False,
        )
        max_move = min(len(truck_positions) - 1, 12)
        for move_steps in range(1, max_move + 1):
            fire_tick = move_steps
            remaining = horizon - fire_tick
            if remaining <= 0 or fire_tick >= len(aircraft_positions):
                break
            result = _best_missile_for_position(
                truck_positions[move_steps], aircraft_positions,
                fire_tick, missile_speed, dt, grid, remaining,
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
            if best is None or (candidate.intercept_tick, candidate.fire_tick, candidate.move_steps) < \
                               (best.intercept_tick,   best.fire_tick,   best.move_steps):
                best = candidate
    return best


def _find_move_then_wait_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_positions: list[Vector2D],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
) -> TruckPlan | None:
    best = None
    for move_dir in DIRECTIONS:
        truck_positions = _simulate_positions(
            truck_pos, move_dir, truck_speed, dt, grid, horizon, is_missile=False,
        )
        max_move = min(len(truck_positions) - 1, 12)
        for move_steps in range(1, max_move + 1):
            max_wait = min(horizon - move_steps, 12)
            for wait_steps in range(1, max_wait + 1):
                fire_tick = move_steps + wait_steps
                remaining = horizon - fire_tick
                if remaining <= 0 or fire_tick >= len(aircraft_positions):
                    break
                result = _best_missile_for_position(
                    truck_positions[move_steps], aircraft_positions,
                    fire_tick, missile_speed, dt, grid, remaining,
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
                if best is None or (candidate.intercept_tick, candidate.fire_tick, candidate.move_steps) < \
                                   (best.intercept_tick,   best.fire_tick,   best.move_steps):
                    best = candidate
    return best


# ---------------------------------------------------------------------------
# Top-level planner
# ---------------------------------------------------------------------------

def find_best_truck_plan(
    truck:          SAMTruck,
    aircraft_pos:   Vector2D,
    aircraft_dir:   DirectionVector | None,
    aircraft_speed: float | None,
    truck_speed:    float,
    missile_speed:  float,
    dt:             float,
    grid:           Grid,
    max_future_steps: int = 40,
) -> tuple[
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
]:
    # No radar lock yet → cannot plan
    if aircraft_dir is None or aircraft_speed is None:
        return None, None, None, None, None

    # Build a temporary aircraft-like object for simulation
    class _FakeAircraft:
        position  = aircraft_pos
        direction = aircraft_dir
        speed     = aircraft_speed

    fake_ac = _FakeAircraft()

    aircraft_positions = _simulate_positions(
        aircraft_pos, aircraft_dir, aircraft_speed,
        dt, grid, max_future_steps, is_missile=False,
    )

    fire_now  = _find_fire_now(truck.position, aircraft_positions, missile_speed, dt, grid, max_future_steps)
    wait_plan = _find_wait_then_fire(truck.position, aircraft_positions, missile_speed, dt, grid, max_future_steps)
    move_plan = _find_move_then_fire(truck.position, truck_speed, aircraft_positions, missile_speed, dt, grid, max_future_steps)
    mwf_plan  = _find_move_then_wait_then_fire(truck.position, truck_speed, aircraft_positions, missile_speed, dt, grid, max_future_steps)

    candidates = [p for p in (fire_now, wait_plan, move_plan, mwf_plan) if p is not None]
    best = (
        min(candidates, key=lambda p: _plan_key(p, truck.direction))
        if candidates else None
    )

    return fire_now, wait_plan, move_plan, mwf_plan, best


def launch_missile_in_direction(
    truck:         SAMTruck,
    missile_speed: float,
    direction:     DirectionVector,
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