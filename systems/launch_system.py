from __future__ import annotations

import math
from dataclasses import dataclass

from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from core.grid import Grid
from core.vector import Vector2D
from entities.missile import Missile
from entities.sam_truck import SAMTruck
from game import constants as C
from systems.collision_system import HIT_RADIUS
from systems.target_predictor import ConstantVelocityPredictor, TargetPredictor

_MAX_MOVE_STEPS = 6
_MAX_WAIT_STEPS = 6
_SNAP_NEIGHBOURS = 1
_MISSILE_DIR_BUDGET = 9

_TRUCK_DIRS_FAST: list[DirectionVector] = [DIRECTIONS[i] for i in range(0, len(DIRECTIONS), 4)]
_TRUCK_DIRS_MED: list[DirectionVector] = [DIRECTIONS[i] for i in range(0, len(DIRECTIONS), 2)]


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
    futures_hit: int = 1
    futures_total: int = 1
    primary_hit: bool = False
    primary_intercept_tick: int = 10**9
    weighted_hits: int = 0


@dataclass(frozen=True)
class DirectionScore:
    primary_hit: bool
    primary_step: int
    hit_count: int
    weighted_hits: int
    worst_hit_step: int


@dataclass
class PlannerDiagnostic:
    candidates_evaluated: int = 0
    directions_verified: int = 0
    fallback_used: bool = False
    any_continuous: bool = False
    futures_evaluated: int = 0
    no_solution_reason: str = ""
    predictor_name: str = ""


def _copy_pos(pos: Vector2D) -> Vector2D:
    return Vector2D(pos.x, pos.y)


def _direction_index(direction: DirectionVector) -> int:
    return getattr(direction, "index", DIRECTIONS.index(direction))


def _plan_search_key(plan: TruckPlan) -> tuple[int, int, int, int, int, int, int]:
    misses = max(0, plan.futures_total - plan.futures_hit)
    return (
        0 if plan.primary_hit else 1,
        -plan.weighted_hits,
        misses,
        plan.primary_intercept_tick,
        plan.intercept_tick,
        plan.fire_tick,
        plan.move_steps,
    )


def _plan_key(plan: TruckPlan, current_dir: DirectionVector) -> tuple[int, int, int, int, int, int, int, int]:
    misses = max(0, plan.futures_total - plan.futures_hit)
    penalty = 0 if (plan.move_direction is None or plan.move_direction is current_dir) else 1
    return (
        0 if plan.primary_hit else 1,
        -plan.weighted_hits,
        misses,
        plan.primary_intercept_tick,
        plan.intercept_tick,
        plan.fire_tick,
        plan.move_steps,
        penalty,
    )


def _plan_key_pva(plan: TruckPlan, current_dir: DirectionVector) -> tuple[int, int, int, int, int, int, int, int, int]:
    """PVA tie-break: after hit quality matches, slightly prefer staging (move/wait) over raw fire_now."""
    misses = max(0, plan.futures_total - plan.futures_hit)
    penalty = 0 if (plan.move_direction is None or plan.move_direction is current_dir) else 1
    staged = 0 if (plan.move_steps > 0 or plan.wait_steps > 0) else 1
    return (
        0 if plan.primary_hit else 1,
        -plan.weighted_hits,
        misses,
        staged,
        plan.primary_intercept_tick,
        plan.intercept_tick,
        plan.fire_tick,
        plan.move_steps,
        penalty,
    )


def _direction_score_key(score: DirectionScore) -> tuple[int, int, int, int, int]:
    return (
        0 if score.primary_hit else 1,
        -score.weighted_hits,
        -score.hit_count,
        score.primary_step,
        score.worst_hit_step,
    )


def _simulate_entity_clamped(
    start: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
) -> list[Vector2D]:
    pos = _copy_pos(start)
    out = [_copy_pos(pos)]

    for _ in range(max_steps):
        nxt = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        pos = nxt if grid.in_bounds(nxt) else pos
        out.append(_copy_pos(pos))

    return out


def _sorted_truck_dirs(
    truck_dirs: list[DirectionVector],
    truck_pos: Vector2D,
    representative_path: list[Vector2D],
) -> list[DirectionVector]:
    if not representative_path:
        return truck_dirs

    sample_idxs = sorted(
        {
            min(3, len(representative_path) - 1),
            min(6, len(representative_path) - 1),
            min(9, len(representative_path) - 1),
        }
    )
    target_x = 0.0
    target_y = 0.0
    for idx in sample_idxs:
        target_x += representative_path[idx].x
        target_y += representative_path[idx].y
    target_x /= len(sample_idxs)
    target_y /= len(sample_idxs)

    dx = target_x - truck_pos.x
    dy = target_y - truck_pos.y
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-9:
        return truck_dirs

    return sorted(truck_dirs, key=lambda d: d.x * dx + d.y * dy, reverse=True)


def _path_velocity_at(path: list[Vector2D], idx: int, dt: float) -> tuple[float, float]:
    if not path:
        return (0.0, 0.0)

    if idx + 1 < len(path):
        p0 = path[idx]
        p1 = path[idx + 1]
    elif idx > 0:
        p0 = path[idx - 1]
        p1 = path[idx]
    else:
        return (0.0, 0.0)

    return ((p1.x - p0.x) / dt, (p1.y - p0.y) / dt)


def _analytic_intercept_time(
    fire_pos: Vector2D,
    ac_pos: Vector2D,
    vx: float,
    vy: float,
    vm: float,
) -> float | None:
    rx = ac_pos.x - fire_pos.x
    ry = ac_pos.y - fire_pos.y
    r = HIT_RADIUS

    a = vx * vx + vy * vy - vm * vm
    b = 2.0 * (rx * vx + ry * vy - vm * r)
    c = rx * rx + ry * ry - r * r

    if abs(a) < 1e-9:
        if abs(b) < 1e-9:
            return 0.0 if c <= 0.0 else None
        t = -c / b
        return t if t >= 0.0 else None

    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None

    sq = math.sqrt(disc)
    roots = [t for t in ((-b - sq) / (2.0 * a), (-b + sq) / (2.0 * a)) if t >= -1e-9]
    if not roots:
        return None

    return max(0.0, min(roots))


def _ideal_direction(
    fire_pos: Vector2D,
    ac_pos: Vector2D,
    ac_vx: float,
    ac_vy: float,
    missile_speed: float,
) -> DirectionVector | None:
    t = _analytic_intercept_time(fire_pos, ac_pos, ac_vx, ac_vy, missile_speed)
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
    mx = fire_pos.x
    my = fire_pos.y

    for k in range(1, max_steps + 1):
        mx += missile_dir.x * missile_speed * dt
        my += missile_dir.y * missile_speed * dt

        if not grid.in_bounds(Vector2D(mx, my)):
            return None

        ac_idx = fire_tick + k
        if ac_idx >= len(aircraft_positions):
            return None

        ap = aircraft_positions[ac_idx]
        if math.hypot(mx - ap.x, my - ap.y) <= HIT_RADIUS:
            return k

    return None


def _aim_direction(from_pos: Vector2D, to_pos: Vector2D) -> DirectionVector | None:
    dx = to_pos.x - from_pos.x
    dy = to_pos.y - from_pos.y
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-9:
        return None
    return nearest_direction(dx / mag, dy / mag)


def _add_direction_vote(votes: dict[int, int], direction: DirectionVector, base_weight: int) -> None:
    if base_weight <= 0:
        return

    base_idx = _direction_index(direction)
    for offset in range(-_SNAP_NEIGHBOURS, _SNAP_NEIGHBOURS + 1):
        idx = (base_idx + offset) % len(DIRECTIONS)
        weight = max(1, base_weight - abs(offset))
        votes[idx] = votes.get(idx, 0) + weight


def _build_missile_direction_candidates(
    fire_pos: Vector2D,
    futures: list[list[Vector2D]],
    fire_tick: int,
    missile_speed: float,
    dt: float,
    diag: PlannerDiagnostic,
) -> list[DirectionVector]:
    votes: dict[int, int] = {}

    for path_idx, path in enumerate(futures):
        if fire_tick >= len(path):
            continue

        path_weight = 3 if path_idx == 0 else 1

        ac_pos = path[fire_tick]
        vx, vy = _path_velocity_at(path, fire_tick, dt)
        ideal = _ideal_direction(fire_pos, ac_pos, vx, vy, missile_speed)
        if ideal is not None:
            diag.any_continuous = True
            _add_direction_vote(votes, ideal, 5 * path_weight)

        for sample in (1, 2, 4, 6):
            ac_idx = fire_tick + sample
            if ac_idx >= len(path):
                break

            aim = _aim_direction(fire_pos, path[ac_idx])
            if aim is None:
                continue

            if path_idx == 0:
                base = 4 if sample <= 2 else 3
            else:
                base = 2 if sample <= 2 else 1

            _add_direction_vote(votes, aim, base)

    if votes:
        ordered = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        return [DIRECTIONS[idx] for idx, _ in ordered[:_MISSILE_DIR_BUDGET]]

    diag.fallback_used = True
    return [DIRECTIONS[i] for i in range(0, len(DIRECTIONS), 4)]


def _score_direction_multi(
    fire_pos: Vector2D,
    futures: list[list[Vector2D]],
    fire_tick: int,
    missile_dir: DirectionVector,
    missile_speed: float,
    dt: float,
    grid: Grid,
    remaining_horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> DirectionScore | None:
    diag.directions_verified += 1

    verify_budget = max(1, min(remaining_horizon, missile_verify_cap))
    hit_steps: list[int | None] = []
    for path in futures:
        hit_steps.append(
            _verify_missile(
                fire_pos=fire_pos,
                aircraft_positions=path,
                fire_tick=fire_tick,
                missile_dir=missile_dir,
                missile_speed=missile_speed,
                dt=dt,
                grid=grid,
                max_steps=verify_budget,
            )
        )

    hit_count = sum(1 for k in hit_steps if k is not None)
    if hit_count == 0:
        return None

    primary_hit = bool(hit_steps) and hit_steps[0] is not None
    primary_step = hit_steps[0] if primary_hit else verify_budget + 1
    worst_hit_step = max(k for k in hit_steps if k is not None)

    total = len(futures)
    if not primary_hit:
        if total <= 3:
            min_support = 1
        elif total <= 6:
            min_support = 2
        else:
            min_support = 3

        if hit_count < min_support:
            return None

    weighted_hits = hit_count + (3 if primary_hit else 0)
    if primary_hit and total >= 6 and hit_count >= 2:
        weighted_hits += 1

    return DirectionScore(
        primary_hit=primary_hit,
        primary_step=primary_step,
        hit_count=hit_count,
        weighted_hits=weighted_hits,
        worst_hit_step=worst_hit_step,
    )


def _find_missile_direction_multi(
    fire_pos: Vector2D,
    futures: list[list[Vector2D]],
    fire_tick: int,
    missile_speed: float,
    dt: float,
    grid: Grid,
    remaining_horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> tuple[DirectionVector, DirectionScore] | None:
    candidates = _build_missile_direction_candidates(
        fire_pos=fire_pos,
        futures=futures,
        fire_tick=fire_tick,
        missile_speed=missile_speed,
        dt=dt,
        diag=diag,
    )

    best_dir: DirectionVector | None = None
    best_score: DirectionScore | None = None

    for direction in candidates:
        score = _score_direction_multi(
            fire_pos=fire_pos,
            futures=futures,
            fire_tick=fire_tick,
            missile_dir=direction,
            missile_speed=missile_speed,
            dt=dt,
            grid=grid,
            remaining_horizon=remaining_horizon,
            missile_verify_cap=missile_verify_cap,
            diag=diag,
        )
        if score is None:
            continue

        if best_score is None or _direction_score_key(score) < _direction_score_key(best_score):
            best_dir = direction
            best_score = score

    if best_dir is None or best_score is None:
        return None

    return (best_dir, best_score)


def _make_plan(
    plan_type: str,
    move_dir: DirectionVector | None,
    move_steps: int,
    wait_steps: int,
    fire_dir: DirectionVector,
    score: DirectionScore,
    fire_tick: int,
    futures_total: int,
) -> TruckPlan:
    return TruckPlan(
        plan_type=plan_type,
        move_direction=move_dir,
        move_steps=move_steps,
        wait_steps=wait_steps,
        fire_direction=fire_dir,
        missile_steps=score.worst_hit_step,
        fire_tick=fire_tick,
        intercept_tick=fire_tick + score.worst_hit_step,
        futures_hit=score.hit_count,
        futures_total=futures_total,
        primary_hit=score.primary_hit,
        primary_intercept_tick=(fire_tick + score.primary_step) if score.primary_hit else 10**9,
        weighted_hits=score.weighted_hits,
    )


def _find_fire_now_multi(
    truck_pos: Vector2D,
    futures: list[list[Vector2D]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> TruckPlan | None:
    diag.candidates_evaluated += 1

    result = _find_missile_direction_multi(
        fire_pos=truck_pos,
        futures=futures,
        fire_tick=0,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        remaining_horizon=horizon,
        missile_verify_cap=missile_verify_cap,
        diag=diag,
    )
    if result is None:
        return None

    fire_dir, score = result
    return _make_plan(
        plan_type="fire_now",
        move_dir=None,
        move_steps=0,
        wait_steps=0,
        fire_dir=fire_dir,
        score=score,
        fire_tick=0,
        futures_total=len(futures),
    )


def _find_wait_then_fire_multi(
    truck_pos: Vector2D,
    futures: list[list[Vector2D]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> TruckPlan | None:
    best: TruckPlan | None = None
    total_futures = len(futures)
    min_path_len = min((len(p) for p in futures), default=0)

    for wait in range(1, min(_MAX_WAIT_STEPS, horizon) + 1):
        fire_tick = wait
        remaining = horizon - fire_tick
        if remaining <= 0 or fire_tick >= min_path_len:
            break

        if best is not None and best.primary_hit and best.futures_hit == total_futures and fire_tick + 1 >= best.intercept_tick:
            break

        diag.candidates_evaluated += 1

        result = _find_missile_direction_multi(
            fire_pos=truck_pos,
            futures=futures,
            fire_tick=fire_tick,
            missile_speed=missile_speed,
            dt=dt,
            grid=grid,
            remaining_horizon=remaining,
            missile_verify_cap=missile_verify_cap,
            diag=diag,
        )
        if result is None:
            continue

        fire_dir, score = result
        candidate = _make_plan(
            plan_type="wait_then_fire",
            move_dir=None,
            move_steps=0,
            wait_steps=wait,
            fire_dir=fire_dir,
            score=score,
            fire_tick=fire_tick,
            futures_total=total_futures,
        )

        if best is None or _plan_search_key(candidate) < _plan_search_key(best):
            best = candidate

    return best


def _search_move_plans_multi(
    truck_dirs: list[DirectionVector],
    truck_pos: Vector2D,
    truck_speed: float,
    futures: list[list[Vector2D]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
    include_wait: bool,
) -> TruckPlan | None:
    best: TruckPlan | None = None
    total_futures = len(futures)
    min_path_len = min((len(p) for p in futures), default=0)
    representative = futures[0] if futures else []

    ordered_dirs = _sorted_truck_dirs(truck_dirs, truck_pos, representative)
    wait_start = 1 if include_wait else 0

    for move_dir in ordered_dirs:
        truck_positions = _simulate_entity_clamped(
            start=truck_pos,
            direction=move_dir,
            speed=truck_speed,
            dt=dt,
            grid=grid,
            max_steps=min(_MAX_MOVE_STEPS, horizon),
        )

        for move_steps in range(1, len(truck_positions)):
            if best is not None and best.primary_hit and best.futures_hit == total_futures and move_steps + 1 >= best.intercept_tick:
                break

            max_wait = 0 if not include_wait else min(_MAX_WAIT_STEPS, horizon - move_steps)
            for wait_steps in range(wait_start, max_wait + 1):
                fire_tick = move_steps + wait_steps
                remaining = horizon - fire_tick

                if remaining <= 0 or fire_tick >= min_path_len:
                    break

                if best is not None and best.primary_hit and best.futures_hit == total_futures and fire_tick + 1 >= best.intercept_tick:
                    break

                diag.candidates_evaluated += 1

                result = _find_missile_direction_multi(
                    fire_pos=truck_positions[move_steps],
                    futures=futures,
                    fire_tick=fire_tick,
                    missile_speed=missile_speed,
                    dt=dt,
                    grid=grid,
                    remaining_horizon=remaining,
                    missile_verify_cap=missile_verify_cap,
                    diag=diag,
                )
                if result is None:
                    continue

                fire_dir, score = result
                candidate = _make_plan(
                    plan_type="move_then_wait_then_fire" if include_wait else "move_then_fire",
                    move_dir=move_dir,
                    move_steps=move_steps,
                    wait_steps=wait_steps,
                    fire_dir=fire_dir,
                    score=score,
                    fire_tick=fire_tick,
                    futures_total=total_futures,
                )

                if best is None or _plan_search_key(candidate) < _plan_search_key(best):
                    best = candidate

    return best


def _find_move_then_fire_multi(
    truck_pos: Vector2D,
    truck_speed: float,
    futures: list[list[Vector2D]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> TruckPlan | None:
    best = _search_move_plans_multi(
        truck_dirs=_TRUCK_DIRS_FAST,
        truck_pos=truck_pos,
        truck_speed=truck_speed,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=horizon,
        missile_verify_cap=missile_verify_cap,
        diag=diag,
        include_wait=False,
    )

    if best is None or (not best.primary_hit and best.futures_hit < max(2, len(futures) // 3)):
        diag.fallback_used = True
        alt = _search_move_plans_multi(
            truck_dirs=_TRUCK_DIRS_MED,
            truck_pos=truck_pos,
            truck_speed=truck_speed,
            futures=futures,
            missile_speed=missile_speed,
            dt=dt,
            grid=grid,
            horizon=horizon,
            missile_verify_cap=missile_verify_cap,
            diag=diag,
            include_wait=False,
        )
        if alt is not None and (best is None or _plan_search_key(alt) < _plan_search_key(best)):
            best = alt

    return best


def _find_move_then_wait_then_fire_multi(
    truck_pos: Vector2D,
    truck_speed: float,
    futures: list[list[Vector2D]],
    missile_speed: float,
    dt: float,
    grid: Grid,
    horizon: int,
    missile_verify_cap: int,
    diag: PlannerDiagnostic,
) -> TruckPlan | None:
    best = _search_move_plans_multi(
        truck_dirs=_TRUCK_DIRS_FAST,
        truck_pos=truck_pos,
        truck_speed=truck_speed,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=horizon,
        missile_verify_cap=missile_verify_cap,
        diag=diag,
        include_wait=True,
    )

    if best is None or (not best.primary_hit and best.futures_hit < max(2, len(futures) // 3)):
        diag.fallback_used = True
        alt = _search_move_plans_multi(
            truck_dirs=_TRUCK_DIRS_MED,
            truck_pos=truck_pos,
            truck_speed=truck_speed,
            futures=futures,
            missile_speed=missile_speed,
            dt=dt,
            grid=grid,
            horizon=horizon,
            missile_verify_cap=missile_verify_cap,
            diag=diag,
            include_wait=True,
        )
        if alt is not None and (best is None or _plan_search_key(alt) < _plan_search_key(best)):
            best = alt

    return best


def _build_no_solution_reason(
    diag: PlannerDiagnostic,
    any_positions_exist: bool,
) -> str:
    if not any_positions_exist:
        return "AIRCRAFT ALREADY ESCAPED (no future positions)"
    if diag.candidates_evaluated == 0:
        return "NO CANDIDATES EVALUATED"
    if diag.any_continuous:
        return (
            "Continuous intercept geometry appeared in searched states, "
            "but no discrete verified shot was found under the current search budget"
        )
    return (
        "No verified intercept found in searched staging states "
        f"(predictor={diag.predictor_name}, cands={diag.candidates_evaluated})"
    )


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
    missile_verify_cap: int | None = None,
) -> bool:
    fire_pos = truck.position
    if plan.move_direction is not None and plan.move_steps > 0:
        truck_positions = _simulate_entity_clamped(
            start=truck.position,
            direction=plan.move_direction,
            speed=truck.speed,
            dt=dt,
            grid=grid,
            max_steps=plan.move_steps,
        )
        fire_pos = truck_positions[plan.move_steps]

    aircraft_positions = predictor.predict(
        aircraft_pos,
        aircraft_dir,
        aircraft_speed,
        dt,
        grid,
        horizon,
    )

    mcap = C.MISSILE_MAX_STEPS if missile_verify_cap is None else missile_verify_cap
    remaining = horizon - max(0, plan.fire_tick)
    verify_steps = max(1, min(remaining, mcap))

    k = _verify_missile(
        fire_pos=fire_pos,
        aircraft_positions=aircraft_positions,
        fire_tick=plan.fire_tick,
        missile_dir=plan.fire_direction,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        max_steps=verify_steps,
    )
    return k is not None


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
    missile_verify_cap: int | None = None,
    prefer_truck_staging: bool = False,
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

    mcap = int(C.MISSILE_MAX_STEPS if missile_verify_cap is None else missile_verify_cap)

    futures = predictor.predict_set(
        position=aircraft_pos,
        direction=aircraft_dir,
        speed=aircraft_speed,
        dt=dt,
        grid=grid,
        max_steps=max_future_steps,
    )
    diag.futures_evaluated = len(futures)

    fire_now = _find_fire_now_multi(
        truck_pos=truck.position,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=max_future_steps,
        missile_verify_cap=mcap,
        diag=diag,
    )

    wait_plan = _find_wait_then_fire_multi(
        truck_pos=truck.position,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=max_future_steps,
        missile_verify_cap=mcap,
        diag=diag,
    )

    move_plan = _find_move_then_fire_multi(
        truck_pos=truck.position,
        truck_speed=truck_speed,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=max_future_steps,
        missile_verify_cap=mcap,
        diag=diag,
    )

    move_wait_plan = _find_move_then_wait_then_fire_multi(
        truck_pos=truck.position,
        truck_speed=truck_speed,
        futures=futures,
        missile_speed=missile_speed,
        dt=dt,
        grid=grid,
        horizon=max_future_steps,
        missile_verify_cap=mcap,
        diag=diag,
    )

    candidates = [p for p in (fire_now, wait_plan, move_plan, move_wait_plan) if p is not None]
    if candidates:
        key_fn = _plan_key_pva if prefer_truck_staging else _plan_key
        best = min(candidates, key=lambda p: key_fn(p, truck.direction))
    else:
        best = None

    if best is None:
        representative = futures[0] if futures else []
        diag.no_solution_reason = _build_no_solution_reason(
            diag=diag,
            any_positions_exist=len(representative) > 0,
        )

    return fire_now, wait_plan, move_plan, move_wait_plan, best, diag


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