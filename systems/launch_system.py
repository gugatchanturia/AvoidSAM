"""
AvoidSAM — Launch System

Missile decision primitive
--------------------------
Analytic-first: solve the radius-aware quadratic for earliest intercept time,
snap ideal direction to nearest 32-direction bin ± SNAP_NEIGHBOURS, verify
with exact runtime-consistent simulation, fall back to full-32 sweep only
when analytic solve says intercept should exist but snapped dirs all failed.

Truck search
------------
16-direction fast subset first, widen to full 32 if category finds nothing.
move_steps and wait_steps are hard-capped.
move_then_wait_then_fire uses wait >= 1 only (wait=0 is move_then_fire).

Target motion consumed through TargetPredictor interface for future extensibility.
"""

from __future__ import annotations
import math
from dataclasses import dataclass

from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from core.vector import Vector2D
from core.grid import Grid
from entities.sam_truck import SAMTruck
from entities.missile import Missile
from systems.collision_system import HIT_RADIUS
from systems.target_predictor import TargetPredictor, ConstantVelocityPredictor

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

_MAX_MOVE_STEPS = 20
_MAX_WAIT_STEPS = 20
_SNAP_NEIGHBOURS = 2

_TRUCK_DIRS_FAST: list[DirectionVector] = [DIRECTIONS[i * 2] for i in range(16)]
_TRUCK_DIRS_FULL: list[DirectionVector] = DIRECTIONS


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruckPlan:
    plan_type: str
    move_direction: DirectionVector | None
    move_steps: int
    wait_steps: int
    fire_direction: DirectionVector
    missile_steps: int
    fire_tick: int
    intercept_tick: int


@dataclass
class PlannerDiagnostic:
    candidates_evaluated: int = 0
    directions_verified: int = 0
    fallback_used: bool = False
    any_continuous: bool = False
    no_solution_reason: str = ""
    predictor_name: str = ""


# ---------------------------------------------------------------------------
# Entity simulation helpers
# ---------------------------------------------------------------------------

def _copy_pos(pos: Vector2D) -> Vector2D:
    return Vector2D(pos.x, pos.y)


def _simulate_entity_clamped(
    start: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
) -> list[Vector2D]:
    """Simulate truck: clamps at map boundary. Index 0 = start."""
    pos = _copy_pos(start)
    out = [_copy_pos(pos)]
    for _ in range(max_steps):
        new_pos = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        pos = new_pos if grid.in_bounds(new_pos) else pos
        out.append(_copy_pos(pos))
    return out


def _sorted_truck_dirs(
    truck_dirs: list[DirectionVector],
    truck_pos: Vector2D,
    aircraft_positions: list[Vector2D],
) -> list[DirectionVector]:
    """
    Search promising truck move directions first.
    This only changes search order, not correctness.
    """
    if not aircraft_positions:
        return truck_dirs

    target = aircraft_positions[min(3, len(aircraft_positions) - 1)]
    dx = target.x - truck_pos.x
    dy = target.y - truck_pos.y
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-9:
        return truck_dirs

    return sorted(
        truck_dirs,
        key=lambda d: d.x * dx + d.y * dy,
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Analytic radius-aware intercept solve
# ---------------------------------------------------------------------------

def _analytic_intercept_time(
    q: Vector2D,
    a0: Vector2D,
    vx: float,
    vy: float,
    vm: float,
) -> float | None:
    """
    Solve  ||a0 + v*t - q|| = vm*t + R  for smallest t >= 0.

    Let r = a0 - q. Then:
        (v·v - vm^2) t^2
      + 2*(r·v - vm*R) t
      + (r·r - R^2) = 0
    """
    R = HIT_RADIUS
    rx = a0.x - q.x
    ry = a0.y - q.y

    A = vx * vx + vy * vy - vm * vm
    B = 2.0 * (rx * vx + ry * vy - vm * R)
    C = rx * rx + ry * ry - R * R

    if abs(A) < 1e-9:
        if abs(B) < 1e-9:
            return 0.0 if C <= 0.0 else None
        t = -C / B
        return t if t >= 0.0 else None

    disc = B * B - 4.0 * A * C
    if disc < 0.0:
        return None

    sq = math.sqrt(disc)
    t1 = (-B - sq) / (2.0 * A)
    t2 = (-B + sq) / (2.0 * A)
    pos_roots = [t for t in (t1, t2) if t >= -1e-9]
    if not pos_roots:
        return None
    return max(0.0, min(pos_roots))


def _ideal_direction(
    fire_pos: Vector2D,
    ac_pos: Vector2D,
    ac_vx: float,
    ac_vy: float,
    vm: float,
) -> DirectionVector | None:
    """Return nearest-32-dir ideal missile direction from analytic solve, or None."""
    t = _analytic_intercept_time(fire_pos, ac_pos, ac_vx, ac_vy, vm)
    if t is None:
        return None

    px = ac_pos.x + ac_vx * t
    py = ac_pos.y + ac_vy * t
    dx = px - fire_pos.x
    dy = py - fire_pos.y
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-9:
        return None
    return nearest_direction(dx / mag, dy / mag)


# ---------------------------------------------------------------------------
# Exact missile simulation + hit check
# ---------------------------------------------------------------------------

def _verify_missile(
    fire_pos: Vector2D,
    aircraft_positions: list[Vector2D],
    fire_tick: int,
    missile_dir: DirectionVector,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
) -> int | None:
    """
    Returns missile steps k >= 1 at which hit-radius is reached, else None.
    Aircraft positions absent beyond escape point => automatic None.
    """
    mx, my = fire_pos.x, fire_pos.y
    for k in range(1, max_steps + 1):
        mx += missile_dir.x * missile_speed * dt
        my += missile_dir.y * missile_speed * dt
        if not grid.in_bounds(Vector2D(mx, my)):
            return None
        ac_idx = fire_tick + k
        if ac_idx >= len(aircraft_positions):
            return None
        ap = aircraft_positions[ac_idx]
        if math.sqrt((mx - ap.x) ** 2 + (my - ap.y) ** 2) <= HIT_RADIUS:
            return k
    return None


# ---------------------------------------------------------------------------
# Core missile search
# ---------------------------------------------------------------------------

def _find_missile_direction(
    fire_pos: Vector2D,
    aircraft_positions: list[Vector2D],
    fire_tick: int,
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    remaining_horizon: int,
    diag: PlannerDiagnostic,
) -> tuple[DirectionVector, int] | None:
    if fire_tick >= len(aircraft_positions):
        return None

    ac_pos = aircraft_positions[fire_tick]

    ideal = _ideal_direction(fire_pos, ac_pos, ac_vx, ac_vy, missile_speed)
    if ideal is not None:
        diag.any_continuous = True

    fast_candidates: list[DirectionVector] = []
    if ideal is not None:
        seen: set[int] = set()
        for delta in range(-_SNAP_NEIGHBOURS, _SNAP_NEIGHBOURS + 1):
            idx = (ideal.index + delta) % len(DIRECTIONS)
            if idx not in seen:
                seen.add(idx)
                fast_candidates.append(DIRECTIONS[idx])

    best_dir = None
    best_steps = remaining_horizon + 1

    for d in fast_candidates:
        diag.directions_verified += 1
        k = _verify_missile(
            fire_pos,
            aircraft_positions,
            fire_tick,
            d,
            missile_speed,
            dt,
            grid,
            remaining_horizon,
        )
        if k is not None and k < best_steps:
            best_steps = k
            best_dir = d

    if best_dir is not None:
        return (best_dir, best_steps)

    if ideal is not None:
        diag.fallback_used = True
        fast_idxs = {d.index for d in fast_candidates}
        for d in DIRECTIONS:
            if d.index in fast_idxs:
                continue
            diag.directions_verified += 1
            k = _verify_missile(
                fire_pos,
                aircraft_positions,
                fire_tick,
                d,
                missile_speed,
                dt,
                grid,
                remaining_horizon,
            )
            if k is not None and k < best_steps:
                best_steps = k
                best_dir = d

    return (best_dir, best_steps) if best_dir is not None else None


# ---------------------------------------------------------------------------
# Plan sort / pruning helpers
# ---------------------------------------------------------------------------

def _plan_key(p: TruckPlan, current_dir: DirectionVector) -> tuple:
    penalty = 0 if (p.move_direction is None or p.move_direction is current_dir) else 1
    return (p.intercept_tick + penalty, p.fire_tick, p.move_steps, penalty)


def _effective_limit(local_best: TruckPlan | None, best_limit: int | None) -> int | None:
    if local_best is None:
        return best_limit
    return local_best.intercept_tick if best_limit is None else min(local_best.intercept_tick, best_limit)


# ---------------------------------------------------------------------------
# Per-category plan finders
# ---------------------------------------------------------------------------

def _find_fire_now(
    truck_pos: Vector2D,
    aircraft_positions: list[Vector2D],
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    diag: PlannerDiagnostic,
) -> TruckPlan | None:
    diag.candidates_evaluated += 1
    result = _find_missile_direction(
        truck_pos,
        aircraft_positions,
        0,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        horizon,
        diag,
    )
    if result is None:
        return None
    fire_dir, ms = result
    return TruckPlan("fire_now", None, 0, 0, fire_dir, ms, 0, ms)


def _find_wait_then_fire(
    truck_pos: Vector2D,
    aircraft_positions: list[Vector2D],
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    diag: PlannerDiagnostic,
    best_limit: int | None = None,
) -> TruckPlan | None:
    best = None
    for wait in range(1, min(_MAX_WAIT_STEPS, horizon) + 1):
        effective_limit = _effective_limit(best, best_limit)
        if effective_limit is not None and (wait + 1) > effective_limit:
            break

        remaining = horizon - wait
        if remaining <= 0 or wait >= len(aircraft_positions):
            break

        diag.candidates_evaluated += 1
        result = _find_missile_direction(
            truck_pos,
            aircraft_positions,
            wait,
            ac_vx,
            ac_vy,
            missile_speed,
            dt,
            grid,
            remaining,
            diag,
        )
        if result is None:
            continue

        fire_dir, ms = result
        c = TruckPlan("wait_then_fire", None, 0, wait, fire_dir, ms, wait, wait + ms)
        if best is None or (c.intercept_tick, c.fire_tick) < (best.intercept_tick, best.fire_tick):
            best = c
    return best


def _search_move_plans(
    truck_dirs: list[DirectionVector],
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_positions: list[Vector2D],
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    diag: PlannerDiagnostic,
    include_wait: bool,
    best_limit: int | None = None,
) -> TruckPlan | None:
    best = None
    wait_start = 1 if include_wait else 0

    ordered_dirs = _sorted_truck_dirs(truck_dirs, truck_pos, aircraft_positions)

    for move_dir in ordered_dirs:
        truck_positions = _simulate_entity_clamped(
            truck_pos,
            move_dir,
            truck_speed,
            dt,
            grid,
            min(_MAX_MOVE_STEPS, horizon),
        )

        for move_steps in range(1, len(truck_positions)):
            effective_limit = _effective_limit(best, best_limit)
            earliest_fire_tick = move_steps + (1 if include_wait else 0)
            if effective_limit is not None and (earliest_fire_tick + 1) > effective_limit:
                break

            max_wait = 0 if not include_wait else min(_MAX_WAIT_STEPS, horizon - move_steps)

            for wait in range(wait_start, max_wait + 1):
                fire_tick = move_steps + wait

                effective_limit = _effective_limit(best, best_limit)
                if effective_limit is not None and (fire_tick + 1) > effective_limit:
                    break

                remaining = horizon - fire_tick
                if remaining <= 0 or fire_tick >= len(aircraft_positions):
                    break

                diag.candidates_evaluated += 1
                result = _find_missile_direction(
                    truck_positions[move_steps],
                    aircraft_positions,
                    fire_tick,
                    ac_vx,
                    ac_vy,
                    missile_speed,
                    dt,
                    grid,
                    remaining,
                    diag,
                )
                if result is None:
                    continue

                fire_dir, ms = result
                plan_type = "move_then_wait_then_fire" if include_wait else "move_then_fire"
                c = TruckPlan(
                    plan_type,
                    move_dir,
                    move_steps,
                    wait,
                    fire_dir,
                    ms,
                    fire_tick,
                    fire_tick + ms,
                )
                if best is None or (c.intercept_tick, c.fire_tick, c.move_steps) < (
                    best.intercept_tick,
                    best.fire_tick,
                    best.move_steps,
                ):
                    best = c
    return best


def _find_move_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_positions: list[Vector2D],
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    diag: PlannerDiagnostic,
    best_limit: int | None = None,
) -> TruckPlan | None:
    best = _search_move_plans(
        _TRUCK_DIRS_FAST,
        truck_pos,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        horizon,
        diag,
        include_wait=False,
        best_limit=best_limit,
    )
    if best is not None:
        return best

    diag.fallback_used = True
    return _search_move_plans(
        _TRUCK_DIRS_FULL,
        truck_pos,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        horizon,
        diag,
        include_wait=False,
        best_limit=best_limit,
    )


def _find_move_then_wait_then_fire(
    truck_pos: Vector2D,
    truck_speed: float,
    aircraft_positions: list[Vector2D],
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    diag: PlannerDiagnostic,
    best_limit: int | None = None,
) -> TruckPlan | None:
    best = _search_move_plans(
        _TRUCK_DIRS_FAST,
        truck_pos,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        horizon,
        diag,
        include_wait=True,
        best_limit=best_limit,
    )
    if best is not None:
        return best

    diag.fallback_used = True
    return _search_move_plans(
        _TRUCK_DIRS_FULL,
        truck_pos,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        horizon,
        diag,
        include_wait=True,
        best_limit=best_limit,
    )


# ---------------------------------------------------------------------------
# No-solution diagnostic
# ---------------------------------------------------------------------------

def _build_no_solution_reason(
    diag: PlannerDiagnostic,
    any_positions_exist: bool,
) -> str:
    if not any_positions_exist:
        return "AIRCRAFT ALREADY ESCAPED (no positions)"

    if diag.candidates_evaluated == 0:
        return "NO CANDIDATES EVALUATED (radar lock missing or horizon=0)"

    if diag.any_continuous:
        return (
            "Continuous intercept geometry exists for some candidates "
            "but no discrete 32-dir verified shot found "
            "(discretization or map-boundary limit)"
        )

    return (
        "No continuous intercept found across all searched staging positions "
        f"(cands={diag.candidates_evaluated}, predictor={diag.predictor_name})"
    )


# ---------------------------------------------------------------------------
# Plan validity check
# ---------------------------------------------------------------------------

def validate_plan(
    plan: TruckPlan,
    truck: SAMTruck,
    aircraft_pos: Vector2D,
    aircraft_dir: DirectionVector,
    aircraft_speed: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    predictor: TargetPredictor,
) -> bool:
    """
    Cheap validation. In practice this is most trustworthy for fire_now plans.
    """
    aircraft_positions = predictor.predict(
        aircraft_pos,
        aircraft_dir,
        aircraft_speed,
        dt,
        grid,
        horizon,
    )
    fire_tick = plan.fire_tick
    k = _verify_missile(
        truck.position,
        aircraft_positions,
        fire_tick,
        plan.fire_direction,
        missile_speed,
        dt,
        grid,
        horizon,
    )
    return k is not None


# ---------------------------------------------------------------------------
# Top-level planner
# ---------------------------------------------------------------------------

def find_best_truck_plan(
    truck: SAMTruck,
    aircraft_pos: Vector2D,
    aircraft_dir: DirectionVector | None,
    aircraft_speed: float | None,
    truck_speed: float,
    missile_speed: float,
    dt: float,
    grid: Grid,
    max_future_steps: int = 40,
    predictor: TargetPredictor | None = None,
) -> tuple[
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
    TruckPlan | None,
    PlannerDiagnostic,
]:
    if predictor is None:
        predictor = ConstantVelocityPredictor()

    diag = PlannerDiagnostic(predictor_name=predictor.name)

    if aircraft_dir is None or aircraft_speed is None:
        diag.no_solution_reason = "NO RADAR LOCK YET"
        return None, None, None, None, None, diag

    ac_vx = aircraft_dir.x * aircraft_speed
    ac_vy = aircraft_dir.y * aircraft_speed

    aircraft_positions = predictor.predict(
        aircraft_pos,
        aircraft_dir,
        aircraft_speed,
        dt,
        grid,
        max_future_steps,
    )

    fire_now = _find_fire_now(
        truck.position,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        max_future_steps,
        diag,
    )

    best_limit = fire_now.intercept_tick if fire_now is not None else None

    wait_plan = _find_wait_then_fire(
        truck.position,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        max_future_steps,
        diag,
        best_limit=best_limit,
    )
    if wait_plan is not None:
        best_limit = wait_plan.intercept_tick if best_limit is None else min(best_limit, wait_plan.intercept_tick)

    move_plan = _find_move_then_fire(
        truck.position,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        max_future_steps,
        diag,
        best_limit=best_limit,
    )
    if move_plan is not None:
        best_limit = move_plan.intercept_tick if best_limit is None else min(best_limit, move_plan.intercept_tick)

    mwf_plan = _find_move_then_wait_then_fire(
        truck.position,
        truck_speed,
        aircraft_positions,
        ac_vx,
        ac_vy,
        missile_speed,
        dt,
        grid,
        max_future_steps,
        diag,
        best_limit=best_limit,
    )

    candidates = [p for p in (fire_now, wait_plan, move_plan, mwf_plan) if p is not None]
    best = min(candidates, key=lambda p: _plan_key(p, truck.direction)) if candidates else None

    if best is None:
        diag.no_solution_reason = _build_no_solution_reason(
            diag,
            any_positions_exist=len(aircraft_positions) > 0,
        )

    return fire_now, wait_plan, move_plan, mwf_plan, best, diag


# ---------------------------------------------------------------------------
# Launch helper
# ---------------------------------------------------------------------------

def launch_missile_in_direction(
    truck: SAMTruck,
    missile_speed: float,
    direction: DirectionVector,
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