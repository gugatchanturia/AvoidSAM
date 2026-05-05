from __future__ import annotations

import math
from abc import ABC, abstractmethod

from core.directions import DirectionVector
from core.grid import Grid
from core.vector import Vector2D

from game import constants as C
from game.pva_rules import crossed_exit_tile

_TILE = tuple[int, int]


class TargetPredictor(ABC):
    @abstractmethod
    def predict(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        ...

    def predict_set(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[list[Vector2D]]:
        return [self.predict(position, direction, speed, dt, grid, max_steps)]

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class ConstantVelocityPredictor(TargetPredictor):
    @property
    def name(self) -> str:
        return "ConstantVelocity"

    def predict(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        return _simulate_straight(position, direction, speed, dt, grid, max_steps)


_MAX_BRANCH_TURN = math.radians(float(C.MAX_TURN_ANGLE_DEG))


class TurnAwarePredictor(TargetPredictor):
    """
    Bounded futures: coarse turn-window timing + SAM post-turn direction candidates.
    Player may choose any heading; predictor only expands plausible escaping branches.
    """

    def __init__(
        self,
        turn_min_remaining: int,
        turn_max_remaining: int,
        valid_exit_tiles: frozenset[_TILE],
        sam_post_turn_dirs: list[DirectionVector],
        turn_tick_samples: int = 6,
        max_branch_dirs: int = 6,
    ):
        self._turn_min = max(0, int(turn_min_remaining))
        self._turn_max = max(0, int(turn_max_remaining))
        self._valid_exit = valid_exit_tiles
        self._sam_turn_dirs = list(sam_post_turn_dirs) if sam_post_turn_dirs else []
        self._tick_samples = max(2, min(8, int(turn_tick_samples)))
        self._max_branch_dirs = max(1, min(12, int(max_branch_dirs)))

    @property
    def name(self) -> str:
        return "TurnAware"

    def predict(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        filt = _simulate_straight_respecting_exit(
            position, direction, speed, dt, grid, max_steps, self._valid_exit,
        )
        if filt:
            return filt

        for path in self._build_paths(position, direction, speed, dt, grid, max_steps):
            if path:
                return path

        return _simulate_safe_prefix(
            position, direction, speed, dt, grid, max_steps, self._valid_exit,
        )

    def predict_set(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[list[Vector2D]]:
        paths = self._build_paths(position, direction, speed, dt, grid, max_steps)
        if not paths:
            sp = _simulate_safe_prefix(
                position,
                direction,
                speed,
                dt,
                grid,
                max_steps,
                self._valid_exit,
            )
            return [sp] if sp else []

        capped = sorted(paths, key=lambda lst: (-len(lst), _path_key(lst)))[: C.TURN_AWARE_MAX_PATHS]
        return capped if capped else [paths[0]]

    def _build_paths(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[list[Vector2D]]:
        paths: list[list[Vector2D]] = []
        seen: set[tuple[tuple[float, float], ...]] = set()

        straight_f = _simulate_straight_respecting_exit(
            position, direction, speed, dt, grid, max_steps, self._valid_exit,
        )
        if straight_f:
            _append_unique_path(paths, seen, straight_f)

        if self._turn_max <= 0 or self._turn_min > self._turn_max:
            return paths

        if not self._sam_turn_dirs:
            return paths

        branch_pick = _select_branch_dirs_bounded(
            self._sam_turn_dirs,
            direction,
            max_branch_dirs=self._max_branch_dirs,
        )
        if not branch_pick:
            return paths

        ref_curve = _reference_path_turn_branching(position, direction, speed, dt, grid, max_steps)

        ticks = _build_turn_tick_samples(self._turn_min, self._turn_max, None, self._tick_samples)

        for turn_tick in ticks:
            if turn_tick >= len(ref_curve):
                break

            prefix = ref_curve[: turn_tick + 1]
            turn_pos = ref_curve[turn_tick]
            remaining = max_steps - turn_tick
            if remaining <= 0:
                continue

            for turn_dir in branch_pick:
                if turn_dir.index == direction.index:
                    continue
                pdelta = abs(_wrap_angle_rad(math.atan2(turn_dir.y, turn_dir.x) - math.atan2(direction.y, direction.x)))
                if min(pdelta, 2 * math.pi - pdelta) > _MAX_BRANCH_TURN + 1e-6:
                    continue

                post_f = _simulate_straight_respecting_exit(
                    turn_pos,
                    turn_dir,
                    speed,
                    dt,
                    grid,
                    remaining,
                    self._valid_exit,
                )
                if not post_f or len(post_f) <= 1:
                    continue

                merged = prefix + post_f[1:]
                _append_unique_path(paths, seen, merged)

        return paths


class SingleTurnPredictor(TargetPredictor):
    @property
    def name(self) -> str:
        return "SingleTurn(stub)"

    def predict(
        self,
        position: Vector2D,
        direction: DirectionVector,
        speed: float,
        dt: float,
        grid: Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        raise NotImplementedError("SingleTurnPredictor not yet implemented.")


def _wrap_angle_rad(a: float) -> float:
    while a <= -math.pi:
        a += 2 * math.pi
    while a > math.pi:
        a -= 2 * math.pi
    return a


def _path_key(path: list[Vector2D]) -> tuple[tuple[float, float], ...]:
    return tuple((round(p.x, 4), round(p.y, 4)) for p in path)


def _simulate_straight_respecting_exit(
    position: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
    valid_exit: frozenset[_TILE],
) -> list[Vector2D] | None:
    pos = Vector2D(position.x, position.y)
    out = [Vector2D(pos.x, pos.y)]

    for _ in range(max_steps):
        nxt = Vector2D(pos.x + direction.x * speed * dt, pos.y + direction.y * speed * dt)
        if not grid.in_bounds(nxt):
            exit_tile = crossed_exit_tile(pos, nxt, grid)
            if exit_tile is not None and exit_tile in valid_exit:
                return out
            return None
        pos = nxt
        out.append(Vector2D(pos.x, pos.y))

    return out


def _simulate_safe_prefix(
    position: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
    valid_exit: frozenset[_TILE],
) -> list[Vector2D]:
    pos = Vector2D(position.x, position.y)
    out_path = [Vector2D(pos.x, pos.y)]

    for _ in range(max_steps):
        nxt = Vector2D(pos.x + direction.x * speed * dt, pos.y + direction.y * speed * dt)
        if not grid.in_bounds(nxt):
            tile = crossed_exit_tile(pos, nxt, grid)
            if tile is not None and tile in valid_exit:
                return out_path
            return out_path
        pos = nxt
        out_path.append(Vector2D(pos.x, pos.y))

    return out_path


def _simulate_straight(
    position: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
) -> list[Vector2D]:
    pos = Vector2D(position.x, position.y)
    out = [Vector2D(pos.x, pos.y)]

    for _ in range(max_steps):
        nxt = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        if not grid.in_bounds(nxt):
            break
        pos = nxt
        out.append(Vector2D(pos.x, pos.y))

    return out


def _reference_path_turn_branching(
    position: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    grid: Grid,
    max_steps: int,
) -> list[Vector2D]:
    pos = Vector2D(position.x, position.y)
    out = [Vector2D(pos.x, pos.y)]

    for _ in range(max_steps):
        nxt = Vector2D(pos.x + direction.x * speed * dt, pos.y + direction.y * speed * dt)
        if not grid.in_bounds(nxt):
            return out
        pos = nxt
        out.append(Vector2D(pos.x, pos.y))

    return out


def _build_turn_tick_samples(
    turn_min_tick: int,
    turn_max_tick: int,
    turn_tick_step: int | None,
    samples: int,
) -> list[int]:
    if turn_max_tick < turn_min_tick:
        return []

    if turn_tick_step is not None and turn_tick_step > 0:
        lst = list(range(turn_min_tick, turn_max_tick + 1, turn_tick_step))
        if not lst or lst[-1] != turn_max_tick:
            lst.append(turn_max_tick)
        return _dedupe_sorted(lst)

    if turn_min_tick == turn_max_tick:
        return [turn_min_tick]

    interval = turn_max_tick - turn_min_tick
    if interval + 1 <= samples:
        return list(range(turn_min_tick, turn_max_tick + 1))

    ticks = [turn_min_tick]
    for k in range(1, samples - 1):
        frac = k / float(samples - 1)
        ticks.append(round(turn_min_tick + frac * interval))
    ticks.append(turn_max_tick)
    ok = sorted({int(round(t)) for t in ticks if turn_min_tick <= round(t) <= turn_max_tick})
    if not ok or ok[0] != turn_min_tick:
        ok.insert(0, turn_min_tick)
    if ok[-1] != turn_max_tick:
        ok.append(turn_max_tick)
    return _dedupe_sorted(ok)


def _direction_angle(direction: DirectionVector) -> float:
    return math.atan2(direction.y, direction.x)


def _select_branch_dirs_bounded(
    dirs: list[DirectionVector],
    current_dir: DirectionVector,
    max_branch_dirs: int,
) -> list[DirectionVector]:
    if max_branch_dirs <= 0:
        return []

    curr_a = _direction_angle(current_dir)
    candidates: list[tuple[DirectionVector, float]] = []

    for direction in dirs:
        if getattr(direction, "index", None) == getattr(current_dir, "index", -999):
            continue
        da = _direction_angle(direction)
        signed_delta = _wrap_angle_rad(da - curr_a)
        if abs(signed_delta) <= 1e-6:
            continue
        if abs(signed_delta) > _MAX_BRANCH_TURN + 1e-6:
            continue
        candidates.append((direction, signed_delta))

    if not candidates:
        return []

    by_abs = sorted(candidates, key=lambda t: abs(t[1]))
    leftmost = min(candidates, key=lambda t: t[1])[0]
    rightmost = max(candidates, key=lambda t: t[1])[0]

    picked: list[DirectionVector] = []
    idx_used: set[int] = set()

    def append_dir(dv: DirectionVector) -> None:
        if getattr(dv, "index", None) not in idx_used:
            idx_used.add(dv.index)
            picked.append(dv)

    append_dir(by_abs[0][0])
    append_dir(leftmost)
    append_dir(rightmost)
    for d, _ in by_abs[1:]:
        if len(picked) >= max_branch_dirs:
            break
        append_dir(d)
    return picked[:max_branch_dirs]


def _append_unique_path(
    paths: list[list[Vector2D]],
    seen: set[tuple[tuple[float, float], ...]],
    path: list[Vector2D],
) -> None:
    key = _path_key(path)
    if key in seen:
        return
    seen.add(key)
    paths.append(path)


def _dedupe_sorted(values: list[int]) -> list[int]:
    seen: set[int] = set()
    out_list: list[int] = []
    for v in sorted(values):
        if v not in seen:
            seen.add(v)
            out_list.append(v)
    return out_list
