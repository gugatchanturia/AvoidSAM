from __future__ import annotations

import math

from core.directions import DirectionVector, DIRECTIONS
from core.grid import Grid
from core.vector import Vector2D

from game import constants as C

_EPS = 1e-9
Tile = tuple[int, int]

# Launch-only filter: straight discrete run must need at least this many integration steps
# before the first boundary crossing. Tuned for DT=1/8 and speed~5 (~0.625 grid units/step:
# 10 steps ≈ 6.25 units) to drop obvious corner-skims without blocking normal deep launches.
PVA_MIN_LAUNCH_STEPS_BEFORE_EXIT = 10

# Launch-only: stronger floor for left/right↔top/bottom “adjacent edge” shortcuts (SAM needs time).
PVA_MIN_ADJACENT_EDGE_LAUNCH_STEPS = 24

_LR_EDGE = frozenset({"left", "right"})
_TB_EDGE = frozenset({"top", "bottom"})

# Launch-only: cap on straight launch simulation steps (used for preview geometry and filtering).
_PVA_LAUNCH_STEP_CAP = 768

# Legacy widesector helper (still used nowhere in PVA; kept for tooling / docs parity)
LONG_EDGE_HALF_SPAN = 5
SHORT_EDGE_HALF_SPAN = 3


def all_border_tiles(grid: Grid) -> list[Tile]:
    tiles: list[Tile] = []
    for x in range(grid.width):
        tiles.append((x, 0))
        if grid.height > 1:
            tiles.append((x, grid.height - 1))
    for y in range(1, grid.height - 1):
        tiles.append((0, y))
        if grid.width > 1:
            tiles.append((grid.width - 1, y))
    return tiles


def is_border_tile(grid: Grid, tile: Tile) -> bool:
    x, y = tile
    return x == 0 or y == 0 or x == grid.width - 1 or y == grid.height - 1


def tile_pos(tile: Tile) -> Vector2D:
    return Vector2D(float(tile[0]), float(tile[1]))


def _angle_diff(a: float, b: float) -> float:
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return abs(d)


def _corner_id(grid: Grid, tile: Tile) -> str | None:
    x, y = tile
    w, h = grid.width, grid.height
    if not ((x == 0 or x == w - 1) and (y == 0 or y == h - 1)):
        return None
    if x == 0 and y == 0:
        return "tl"
    if x == w - 1 and y == 0:
        return "tr"
    if x == 0 and y == h - 1:
        return "bl"
    return "br"


def border_tile_edges(grid: Grid, tile: Tile) -> frozenset[str]:
    """Board edges ('top','bottom','left','right') the spawn tile lies on."""
    x, y = tile
    w, h = grid.width, grid.height
    e: set[str] = set()
    if y == 0:
        e.add("top")
    if y == h - 1:
        e.add("bottom")
    if x == 0:
        e.add("left")
    if x == w - 1:
        e.add("right")
    return frozenset(e)


def _border_edges_for_tile(grid: Grid, tile: Tile) -> frozenset[str]:
    """Board edges the spawn/position tile lies on (wrapper for ``border_tile_edges``)."""
    return border_tile_edges(grid, tile)


def _exit_edges_for_tile(grid: Grid, tile: Tile) -> frozenset[str]:
    """For a border exit cell, which board edge(s) that exit tile belongs to."""
    return border_tile_edges(grid, tile)


def _is_adjacent_edge_launch(spawn_edges: frozenset[str], exit_edges: frozenset[str]) -> bool:
    """
    True for perpendicular short-runs: left/right spawn with top/bottom exit, or
    top/bottom spawn with left/right exit. Opposite-edge and same-edge launches are False.
    """
    if (spawn_edges & _LR_EDGE) and (exit_edges & _TB_EDGE):
        return True
    if (spawn_edges & _TB_EDGE) and (exit_edges & _LR_EDGE):
        return True
    return False


ESCAPE_DEBUG_LAST: dict[str, int | float | str | bool | None] = {}


def direction_reaches_locked_exit_discrete(
    grid: Grid,
    position: Vector2D,
    direction: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    speed: float,
    dt: float,
    max_steps: int,
) -> bool:
    """
    Runtime-consistent: straight discrete integration until out of bounds, then
    accept only if crossed_exit_tile lies in valid_exit_tiles.
    """
    if not valid_exit_tiles or max_steps <= 0:
        return False
    mag = math.hypot(direction.x, direction.y)
    if mag < _EPS:
        return False

    pos = Vector2D(position.x, position.y)
    for _ in range(int(max_steps)):
        nxt = Vector2D(
            pos.x + direction.x * float(speed) * float(dt),
            pos.y + direction.y * float(speed) * float(dt),
        )
        if grid.in_bounds(nxt):
            pos = nxt
            continue
        crossed = crossed_exit_tile(pos, nxt, grid)
        return crossed is not None and crossed in valid_exit_tiles
    return False


def _ray_boundary_candidates(
    grid: Grid, start: Vector2D, dx: float, dy: float
) -> list[tuple[float, str, float]]:
    cands: list[tuple[float, str, float]] = []
    if abs(dx) > _EPS:
        t_left = (0.0 - start.x) / dx
        if t_left > _EPS:
            y_hit = start.y + t_left * dy
            if -_EPS <= y_hit <= grid.height - 1 + _EPS:
                cands.append((t_left, "left", y_hit))
        t_right = ((grid.width - 1) - start.x) / dx
        if t_right > _EPS:
            y_hit = start.y + t_right * dy
            if -_EPS <= y_hit <= grid.height - 1 + _EPS:
                cands.append((t_right, "right", y_hit))
    if abs(dy) > _EPS:
        t_top = (0.0 - start.y) / dy
        if t_top > _EPS:
            x_hit = start.x + t_top * dx
            if -_EPS <= x_hit <= grid.width - 1 + _EPS:
                cands.append((t_top, "top", x_hit))
        t_bottom = ((grid.height - 1) - start.y) / dy
        if t_bottom > _EPS:
            x_hit = start.x + t_bottom * dx
            if -_EPS <= x_hit <= grid.width - 1 + _EPS:
                cands.append((t_bottom, "bottom", x_hit))
    return cands


def first_ray_boundary_edges(grid: Grid, origin: Vector2D, direction: DirectionVector) -> frozenset[str]:
    """Which board edge(s) the infinite normalized ray meets first."""
    mag = math.hypot(direction.x, direction.y)
    if mag < _EPS:
        return frozenset()
    dx = direction.x / mag
    dy = direction.y / mag
    cands = _ray_boundary_candidates(grid, Vector2D(origin.x, origin.y), dx, dy)
    if not cands:
        return frozenset()
    min_t = min(t for t, _, _ in cands)
    return frozenset(edge for t, edge, _ in cands if abs(t - min_t) <= 1e-6)


def _edge_tile_from_hit(grid: Grid, edge: str, coord: float) -> Tile:
    idx = int(round(coord))
    if edge == "left":
        return (0, max(0, min(grid.height - 1, idx)))
    if edge == "right":
        return (grid.width - 1, max(0, min(grid.height - 1, idx)))
    if edge == "top":
        return (max(0, min(grid.width - 1, idx)), 0)
    return (max(0, min(grid.width - 1, idx)), grid.height - 1)


def first_boundary_hit_coord_for_debug(grid: Grid, origin: Vector2D, direction: DirectionVector) -> str:
    """Short string for logs: edge + primary coordinate."""
    mag = math.hypot(direction.x, direction.y)
    if mag < _EPS:
        return "?"
    dx = direction.x / mag
    dy = direction.y / mag
    cands = _ray_boundary_candidates(grid, Vector2D(origin.x, origin.y), dx, dy)
    if not cands:
        return "no_hit"
    min_t = min(t for t, _, _ in cands)
    for t, edge, coord in cands:
        if abs(t - min_t) <= 1e-6:
            return f"{edge}@{coord:.3f}"
    return "?"


def _tiles_along_edge_hit(grid: Grid, edge: str, center_idx: int, stripe_half: int) -> set[Tile]:
    out: set[Tile] = set()
    if edge in ("top", "bottom"):
        y = 0 if edge == "top" else grid.height - 1
        lo = max(0, center_idx - stripe_half)
        hi = min(grid.width - 1, center_idx + stripe_half)
        for x in range(lo, hi + 1):
            out.add((x, y))
    else:
        x = 0 if edge == "left" else grid.width - 1
        lo = max(0, center_idx - stripe_half)
        hi = min(grid.height - 1, center_idx + stripe_half)
        for yi in range(lo, hi + 1):
            out.add((x, yi))
    return out


def _stripe_tiles_for_crossed_exit_border(
    grid: Grid,
    exit_tile: Tile,
    stripe_half: int,
) -> set[Tile]:
    """Strip of border tiles on the same edge as ``exit_tile`` (from discrete ``crossed_exit_tile``)."""
    x, y = exit_tile
    w1, h1 = grid.width - 1, grid.height - 1
    if y == 0:
        return _tiles_along_edge_hit(grid, "top", x, stripe_half)
    if y == h1:
        return _tiles_along_edge_hit(grid, "bottom", x, stripe_half)
    if x == 0:
        return _tiles_along_edge_hit(grid, "left", y, stripe_half)
    if x == w1:
        return _tiles_along_edge_hit(grid, "right", y, stripe_half)
    return set()


def first_boundary_exit_tiles(
    grid: Grid,
    origin: Vector2D,
    direction: DirectionVector,
    stripe_half: int = 0,
) -> set[Tile]:
    mag = math.hypot(direction.x, direction.y)
    if mag < _EPS:
        return set()
    dx = direction.x / mag
    dy = direction.y / mag
    start = Vector2D(origin.x, origin.y)
    cands = _ray_boundary_candidates(grid, start, dx, dy)
    if not cands:
        return set()
    min_t = min(t for t, _, _ in cands)
    touched = [(edge, coord) for t, edge, coord in cands if abs(t - min_t) <= 1e-6]
    out: set[Tile] = set()
    for edge, coord in touched:
        ci = int(round(coord))
        if edge in ("top", "bottom"):
            ci = max(0, min(grid.width - 1, ci))
        else:
            ci = max(0, min(grid.height - 1, ci))
        if stripe_half <= 0:
            out.add(_edge_tile_from_hit(grid, edge, coord))
        else:
            out |= _tiles_along_edge_hit(grid, edge, ci, stripe_half)
    return out


def is_escape_direction_valid(
    grid: Grid,
    position: Vector2D,
    direction: DirectionVector,
    allowed_exit_tiles: frozenset[Tile],
    stripe_half: int = 0,
) -> bool:
    if not allowed_exit_tiles:
        return False
    first_tiles = first_boundary_exit_tiles(grid, position, direction, stripe_half=stripe_half)
    if not first_tiles:
        return False
    return first_tiles <= allowed_exit_tiles


def is_direction_exits_inside_locked_tiles(
    grid: Grid,
    position: Vector2D,
    direction: DirectionVector,
    locked_exit_tiles: frozenset[Tile],
) -> bool:
    """Match runtime: ``crossed_exit_tile`` resolves to one tile; compare to Ray first-hit cell(s) only."""
    if not locked_exit_tiles:
        return False
    exact = first_boundary_exit_tiles(grid, position, direction, stripe_half=0)
    if not exact:
        return False
    return exact <= locked_exit_tiles


def exit_stripe_half_for_pva() -> int:
    return max(0, int(C.PVA_EXIT_STRIPE_HALF))


def project_valid_exit_tiles(
    grid: Grid,
    tile: Tile,
    direction: DirectionVector,
    speed: float | None = None,
    dt: float | None = None,
    turn_window_min: int | None = None,
    turn_window_max: int | None = None,
) -> set[Tile]:
    del turn_window_min, turn_window_max
    spd = float(C.AIRCRAFT_SPEED) if speed is None else float(speed)
    ddt = float(C.DT) if dt is None else float(dt)
    stripe = exit_stripe_half_for_pva()
    start = tile_pos(tile)
    out, steps, ex_tile = _discrete_exit_stripe_and_steps(
        grid, start, direction, spd, ddt, stripe_half=int(stripe),
    )
    ESCAPE_DEBUG_LAST["stripe_half"] = stripe
    ESCAPE_DEBUG_LAST["locked_exit_count"] = len(out)
    ESCAPE_DEBUG_LAST["projected_exit_tile"] = ex_tile
    ESCAPE_DEBUG_LAST["first_discrete_exit_steps"] = steps
    if ex_tile is not None:
        ESCAPE_DEBUG_LAST["first_hit_debug"] = f"disc_exit={ex_tile} steps={steps}"
    else:
        ESCAPE_DEBUG_LAST["first_hit_debug"] = f"disc_no_exit steps={steps}"
    return out


def _collect_sam_cone_launch_dirs(
    grid: Grid,
    start: Vector2D,
    base_angle: float,
    half_rad: float,
    occupied: frozenset[str],
    speed: float,
    dt: float,
) -> list[DirectionVector]:
    picked: list[DirectionVector] = []
    for d in DIRECTIONS:
        ang = math.atan2(d.y, d.x)
        if _angle_diff(ang, base_angle) > half_rad + 1e-9:
            continue
        nxt = Vector2D(start.x + d.x * speed * dt, start.y + d.y * speed * dt)
        if not grid.in_bounds(nxt):
            continue
        hit = first_ray_boundary_edges(grid, start, d)
        if hit & occupied:
            continue
        picked.append(d)
    return picked


def legal_launch_directions(
    grid: Grid,
    tile: Tile,
    sam_position: Vector2D,
    speed: float,
    dt: float,
) -> list[DirectionVector]:
    """
    Initial headings: cone around (SAM - spawn), into the board, no immediate
    re-exit through the spawn edge(s). May expand cone slightly (tunable) to reach
    a minimum direction count without bypassing safety rules.
    """
    start = tile_pos(tile)
    bx = sam_position.x - start.x
    by = sam_position.y - start.y
    blen = math.hypot(bx, by)
    if blen < _EPS:
        bx, by = 0.0, 1.0
        blen = 1.0
    base_angle = math.atan2(by, bx)

    is_corner = _corner_id(grid, tile) is not None
    base_half_deg = (
        float(C.PVA_LAUNCH_CONE_HALF_DEG_CORNER)
        if is_corner
        else float(C.PVA_LAUNCH_CONE_HALF_DEG_EDGE)
    )
    min_count = int(C.PVA_MIN_LAUNCH_DIRS_CORNER) if is_corner else int(C.PVA_MIN_LAUNCH_DIRS_EDGE)
    step = float(C.PVA_LAUNCH_CONE_EXPAND_STEP_DEG)
    max_extra = float(C.PVA_LAUNCH_CONE_EXPAND_MAX_DEG)

    occupied = border_tile_edges(grid, tile)
    picked: list[DirectionVector] = []
    half_deg_used = base_half_deg

    extra = 0.0
    while extra <= max_extra + 1e-9:
        half_deg = base_half_deg + extra
        half_rad = math.radians(half_deg)
        picked = _collect_sam_cone_launch_dirs(
            grid, start, base_angle, half_rad, occupied, speed, dt,
        )
        half_deg_used = half_deg
        if len(picked) >= min_count or extra >= max_extra - 1e-9:
            break
        extra += step

    raw_pick = picked
    spawn_edges = _border_edges_for_tile(grid, tile)
    picked_out: list[DirectionVector] = []
    rejected_short = 0
    rejected_adjacent_short = 0
    rejected_no_exit = 0
    stripe_h = exit_stripe_half_for_pva()
    for d in picked:
        stripe_tiles, steps, ex_t = _discrete_exit_stripe_and_steps(
            grid, start, d, float(speed), float(dt), stripe_half=int(stripe_h),
        )
        if not stripe_tiles or ex_t is None:
            rejected_no_exit += 1
            continue
        if steps < PVA_MIN_LAUNCH_STEPS_BEFORE_EXIT:
            rejected_short += 1
            continue
        exit_edges = _exit_edges_for_tile(grid, ex_t)
        if _is_adjacent_edge_launch(spawn_edges, exit_edges) and steps < PVA_MIN_ADJACENT_EDGE_LAUNCH_STEPS:
            rejected_adjacent_short += 1
            continue
        picked_out.append(d)
    picked = picked_out

    ESCAPE_DEBUG_LAST["pva_launch_half_cone_deg"] = half_deg_used
    ESCAPE_DEBUG_LAST["pva_launch_base_rad"] = base_angle
    ESCAPE_DEBUG_LAST["pva_launch_cone_expand_extra_deg"] = max(0.0, half_deg_used - base_half_deg)
    ESCAPE_DEBUG_LAST["pva_launch_raw_count"] = len(raw_pick)
    ESCAPE_DEBUG_LAST["pva_launch_count"] = len(picked)
    ESCAPE_DEBUG_LAST["pva_launch_rejected_short"] = rejected_short
    ESCAPE_DEBUG_LAST["pva_launch_rejected_adjacent_short"] = rejected_adjacent_short
    ESCAPE_DEBUG_LAST["pva_launch_rejected_no_exit"] = rejected_no_exit
    return picked


def legal_turn_directions(
    grid: Grid,
    position: Vector2D,
    current_heading: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    exit_stripe_half: int,
    max_turn_angle_deg: float | None = None,
) -> list[DirectionVector]:
    _ = exit_stripe_half  # locked stripe is only for deployment UI; turn legality uses first-hit cells vs this set
    if not valid_exit_tiles:
        return []
    deg = float(C.MAX_TURN_ANGLE_DEG) if max_turn_angle_deg is None else float(max_turn_angle_deg)
    limit_rad = math.radians(deg)
    curr_a = math.atan2(current_heading.y, current_heading.x)

    picked: list[DirectionVector] = []
    ch_idx = getattr(current_heading, "index", -1)
    for d in DIRECTIONS:
        da = math.atan2(d.y, d.x)
        if _angle_diff(da, curr_a) > limit_rad + 1e-9:
            continue
        if not is_direction_exits_inside_locked_tiles(grid, position, d, valid_exit_tiles):
            continue
        picked.append(d)

    picked.sort(key=lambda dv: (0 if getattr(dv, "index", -999) == ch_idx else 1, getattr(dv, "index", 0)))
    ESCAPE_DEBUG_LAST["legal_turn_count"] = len(picked)
    return picked


def explain_legal_turn_empty(
    grid: Grid,
    position: Vector2D,
    current_heading: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    exit_stripe_half: int,
    tick: int,
    turn_window_min: int,
    turn_window_max: int,
    turn_used: bool,
) -> list[str]:
    """Human-readable reasons when no legal turn directions exist."""
    _ = exit_stripe_half
    reasons: list[str] = []
    if turn_used:
        reasons.append("turn_already_used")
    if tick < turn_window_min or tick > turn_window_max:
        reasons.append(f"outside_turn_window(tick={tick}, need {turn_window_min}..{turn_window_max})")
    if not valid_exit_tiles:
        reasons.append("no_locked_exit_tiles")
    if turn_used or not valid_exit_tiles:
        return reasons

    limit_rad = math.radians(float(C.MAX_TURN_ANGLE_DEG))
    curr_a = math.atan2(current_heading.y, current_heading.x)
    in_cone = 0
    first_hit_ok = 0
    for d in DIRECTIONS:
        da = math.atan2(d.y, d.x)
        if _angle_diff(da, curr_a) > limit_rad + 1e-9:
            continue
        in_cone += 1
        if is_direction_exits_inside_locked_tiles(grid, position, d, valid_exit_tiles):
            first_hit_ok += 1
    if in_cone == 0:
        reasons.append("no_direction_within_max_turn_angle")
    elif first_hit_ok == 0:
        reasons.append("first_hit_cell_not_in_locked_exits")
    else:
        reasons.append("unexpected_empty")
    return reasons


def _sample_turn_prefix_ticks(tmin: int, tmax: int) -> list[int]:
    """Deterministic 3–5 tick offsets inside the remaining turn window (inclusive)."""
    tmin = max(0, int(tmin))
    tmax = max(0, int(tmax))
    if tmax < tmin:
        return []
    if tmin == tmax:
        return [tmin]
    mid = (tmin + tmax) // 2
    ticks = [tmin, mid, tmax]
    span = tmax - tmin
    if span >= 4:
        q1 = tmin + max(1, span // 4)
        q3 = tmax - max(1, span // 4)
        for q in (q1, q3):
            if tmin < q < tmax and q not in ticks:
                ticks.append(q)
    ok = sorted({int(t) for t in ticks if tmin <= t <= tmax})
    return ok[:5]


def _position_after_straight_steps(
    grid: Grid,
    position: Vector2D,
    heading: DirectionVector,
    speed: float,
    dt: float,
    steps: int,
) -> Vector2D | None:
    """Straight flight for ``steps`` integration steps; None if trajectory leaves bounds early."""
    pos = Vector2D(position.x, position.y)
    for _ in range(max(0, int(steps))):
        nxt = Vector2D(
            pos.x + heading.x * speed * dt,
            pos.y + heading.y * speed * dt,
        )
        if not grid.in_bounds(nxt):
            return None
        pos = nxt
    return pos


def _rollout_reaches_locked_exit_only(
    grid: Grid,
    position: Vector2D,
    direction: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    speed: float,
    dt: float,
    lookahead_steps: int,
) -> bool:
    """
    Extra SAM candidates only: straight rollout must cross a boundary within a bounded budget,
    via crossed_exit_tile, and exit only through valid_exit_tiles (not stay in-bounds only).
    """
    if not valid_exit_tiles or lookahead_steps <= 0:
        return False

    span = grid.width + grid.height
    budget = max(int(lookahead_steps), span * 8)
    budget = min(budget, 768)

    pos = Vector2D(position.x, position.y)
    for _ in range(budget):
        nxt = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        if not grid.in_bounds(nxt):
            tile = crossed_exit_tile(pos, nxt, grid)
            if tile is not None and tile in valid_exit_tiles:
                return True
            return False
        pos = nxt
    return False


def predictor_turn_directions(
    grid: Grid,
    position: Vector2D,
    current_heading: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    speed: float,
    dt: float,
    lookahead_steps: int,
    exit_stripe_half: int = 0,
    max_dirs: int = 5,
    turn_min_remaining: int | None = None,
    turn_max_remaining: int | None = None,
) -> list[DirectionVector]:
    """
    SAM predictor only: strict legal headings from current position first, then post-turn headings
    that reach locked exits when evaluated from future straight-prefix positions inside the turn window.
    """
    strict = legal_turn_directions(
        grid,
        position,
        current_heading,
        valid_exit_tiles,
        exit_stripe_half=exit_stripe_half,
        max_turn_angle_deg=None,
    )
    strict_idx = {getattr(d, "index", -1) for d in strict}

    out: list[DirectionVector] = []
    used: set[int] = set()
    for d in strict:
        if len(out) >= max_dirs:
            break
        di = getattr(d, "index", -1)
        if di in used:
            continue
        out.append(d)
        used.add(di)

    limit_rad = math.radians(float(C.MAX_TURN_ANGLE_DEG))
    curr_a = math.atan2(current_heading.y, current_heading.x)
    ch_idx = getattr(current_heading, "index", -1)

    late_ordered: list[DirectionVector] = []
    seen_late: set[int] = set()
    late_dirs_added = 0
    prefix_ticks: list[int] = []

    if (
        valid_exit_tiles
        and turn_min_remaining is not None
        and turn_max_remaining is not None
    ):
        prefix_ticks = _sample_turn_prefix_ticks(turn_min_remaining, turn_max_remaining)
        for tick_off in prefix_ticks:
            fp = _position_after_straight_steps(
                grid, position, current_heading, float(speed), float(dt), tick_off,
            )
            if fp is None:
                continue
            for d in DIRECTIONS:
                di = getattr(d, "index", -1)
                if di == ch_idx:
                    continue
                if di in seen_late:
                    continue
                da = math.atan2(d.y, d.x)
                if _angle_diff(da, curr_a) > limit_rad + 1e-9:
                    continue
                if not _rollout_reaches_locked_exit_only(
                    grid, fp, d, valid_exit_tiles, float(speed), float(dt), int(lookahead_steps),
                ):
                    continue
                late_ordered.append(d)
                seen_late.add(di)
                if di not in strict_idx:
                    late_dirs_added += 1

    for d in late_ordered:
        if len(out) >= max_dirs:
            break
        di = getattr(d, "index", -1)
        if di in used:
            continue
        out.append(d)
        used.add(di)

    ESCAPE_DEBUG_LAST["predictor_late_turn_dirs"] = late_dirs_added
    ESCAPE_DEBUG_LAST["predictor_late_turn_prefix_ticks"] = len(prefix_ticks)

    return out


def predictor_post_turn_candidates(
    grid: Grid,
    position: Vector2D,
    current_heading: DirectionVector,
    valid_exit_tiles: frozenset[Tile],
    speed: float,
    dt: float,
    max_primary: int = 12,
    max_total: int = 18,
    lookahead_steps: int = 32,
    exit_stripe_half: int = 0,
    turn_min_remaining: int | None = None,
    turn_max_remaining: int | None = None,
) -> list[DirectionVector]:
    del max_primary, max_total
    return predictor_turn_directions(
        grid,
        position,
        current_heading,
        valid_exit_tiles,
        speed=float(speed),
        dt=float(dt),
        lookahead_steps=int(lookahead_steps),
        exit_stripe_half=exit_stripe_half,
        max_dirs=5,
        turn_min_remaining=turn_min_remaining,
        turn_max_remaining=turn_max_remaining,
    )


def _add_edge_sector(grid: Grid, edge: str, center_idx: int, out: set[Tile]) -> None:
    if edge in ("top", "bottom"):
        y = 0 if edge == "top" else grid.height - 1
        lo = max(0, center_idx - LONG_EDGE_HALF_SPAN)
        hi = min(grid.width - 1, center_idx + LONG_EDGE_HALF_SPAN)
        for x in range(lo, hi + 1):
            out.add((x, y))
    else:
        x = 0 if edge == "left" else grid.width - 1
        lo = max(0, center_idx - SHORT_EDGE_HALF_SPAN)
        hi = min(grid.height - 1, center_idx + SHORT_EDGE_HALF_SPAN)
        for yi in range(lo, hi + 1):
            out.add((x, yi))


def sectors_from_heading_ray(grid: Grid, origin: Vector2D, direction: DirectionVector) -> set[Tile]:
    start = Vector2D(origin.x, origin.y)
    dx = direction.x
    dy = direction.y
    candidates = _ray_boundary_candidates(grid, start, dx, dy)
    if not candidates:
        return set()
    min_t = min(t for t, _, _ in candidates)
    touched = [(edge, coord) for t, edge, coord in candidates if abs(t - min_t) <= 1e-6]
    out: set[Tile] = set()
    for edge, coord in touched:
        ci = int(round(coord))
        if edge in ("top", "bottom"):
            ci = max(0, min(grid.width - 1, ci))
        else:
            ci = max(0, min(grid.height - 1, ci))
        _add_edge_sector(grid, edge, ci, out)
    return out


def crossed_exit_tile(prev_pos: Vector2D, next_pos: Vector2D, grid: Grid) -> Tile | None:
    dx = next_pos.x - prev_pos.x
    dy = next_pos.y - prev_pos.y
    candidates: list[tuple[float, str, float]] = []

    if abs(dx) > _EPS:
        t_left = (0.0 - prev_pos.x) / dx
        if _EPS < t_left <= 1.0 + _EPS:
            y = prev_pos.y + t_left * dy
            if -_EPS <= y <= grid.height - 1 + _EPS:
                candidates.append((t_left, "left", y))
        t_right = ((grid.width - 1) - prev_pos.x) / dx
        if _EPS < t_right <= 1.0 + _EPS:
            y = prev_pos.y + t_right * dy
            if -_EPS <= y <= grid.height - 1 + _EPS:
                candidates.append((t_right, "right", y))

    if abs(dy) > _EPS:
        t_top = (0.0 - prev_pos.y) / dy
        if _EPS < t_top <= 1.0 + _EPS:
            x = prev_pos.x + t_top * dx
            if -_EPS <= x <= grid.width - 1 + _EPS:
                candidates.append((t_top, "top", x))
        t_bottom = ((grid.height - 1) - prev_pos.y) / dy
        if _EPS < t_bottom <= 1.0 + _EPS:
            x = prev_pos.x + t_bottom * dx
            if -_EPS <= x <= grid.width - 1 + _EPS:
                candidates.append((t_bottom, "bottom", x))

    if not candidates:
        if next_pos.x < 0:
            return (0, max(0, min(grid.height - 1, int(round(next_pos.y)))))
        if next_pos.x > grid.width - 1:
            return (grid.width - 1, max(0, min(grid.height - 1, int(round(next_pos.y)))))
        if next_pos.y < 0:
            return (max(0, min(grid.width - 1, int(round(next_pos.x)))), 0)
        if next_pos.y > grid.height - 1:
            return (max(0, min(grid.width - 1, int(round(next_pos.x)))), grid.height - 1)
        return None

    _, edge, coord = min(candidates, key=lambda item: item[0])
    idx = int(round(coord))
    if edge == "left":
        return (0, max(0, min(grid.height - 1, idx)))
    if edge == "right":
        return (grid.width - 1, max(0, min(grid.height - 1, idx)))
    if edge == "top":
        return (max(0, min(grid.width - 1, idx)), 0)
    return (max(0, min(grid.width - 1, idx)), grid.height - 1)


def _discrete_exit_stripe_and_steps(
    grid: Grid,
    start: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    stripe_half: int,
) -> tuple[set[Tile], int, Tile | None]:
    """
    Straight discrete integration until out of bounds; stripe around ``crossed_exit_tile``.
    Aligns with ``App._move_pva_aircraft`` (same step and ``crossed_exit_tile``).
    """
    mag = math.hypot(direction.x, direction.y)
    if mag < _EPS:
        return set(), 0, None

    span = grid.width + grid.height
    max_steps = min(_PVA_LAUNCH_STEP_CAP, max(128, span * 8))

    pos = Vector2D(start.x, start.y)
    for step_idx in range(1, max_steps + 1):
        nxt = Vector2D(
            pos.x + direction.x * speed * dt,
            pos.y + direction.y * speed * dt,
        )
        if not grid.in_bounds(nxt):
            ex = crossed_exit_tile(pos, nxt, grid)
            if ex is None:
                return set(), step_idx, None
            stripe = _stripe_tiles_for_crossed_exit_border(grid, ex, int(stripe_half))
            return stripe, step_idx, ex
        pos = nxt

    return set(), max_steps, None
