from __future__ import annotations

import math

from core.directions import DirectionVector, DIRECTIONS
from core.grid import Grid
from core.vector import Vector2D

from game import constants as C

_EPS = 1e-9
Tile = tuple[int, int]

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

def first_exit_tick_straight(
    grid: Grid,
    position: Vector2D,
    direction: DirectionVector,
    speed: float,
    dt: float,
    max_ticks: int,
) -> int | None:
    """
    Simulate straight (no-turn) motion and return the first tick index (1-based)
    where the next step would leave the grid, or None if it stays in-bounds for
    the full checked window.
    """
    pos = Vector2D(position.x, position.y)
    steps = max(0, int(max_ticks))
    for tick in range(1, steps + 1):
        nxt = Vector2D(pos.x + direction.x * speed * dt, pos.y + direction.y * speed * dt)
        if not grid.in_bounds(nxt):
            return tick
        pos = nxt
    return None


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


ESCAPE_DEBUG_LAST: dict[str, int | float | str | bool | None] = {}


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
    spd = float(C.AIRCRAFT_SPEED) if speed is None else float(speed)
    step_dt = float(C.DT) if dt is None else float(dt)
    tw_max = int(C.TURN_WINDOW_MAX) if turn_window_max is None else int(turn_window_max)
    stripe = exit_stripe_half_for_pva()
    start = tile_pos(tile)
    exits_early = first_exit_tick_straight(grid, start, direction, spd, step_dt, tw_max)
    out = set()
    if exits_early is None or exits_early > tw_max:
        out = first_boundary_exit_tiles(grid, start, direction, stripe_half=stripe)
    ESCAPE_DEBUG_LAST["stripe_half"] = stripe
    ESCAPE_DEBUG_LAST["locked_exit_count"] = len(out)
    ESCAPE_DEBUG_LAST["first_hit_debug"] = first_boundary_hit_coord_for_debug(grid, start, direction)
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

    ESCAPE_DEBUG_LAST["pva_launch_half_cone_deg"] = half_deg_used
    ESCAPE_DEBUG_LAST["pva_launch_base_rad"] = base_angle
    ESCAPE_DEBUG_LAST["pva_launch_cone_expand_extra_deg"] = max(0.0, half_deg_used - base_half_deg)
    tw_max = int(C.TURN_WINDOW_MAX)
    kept: list[DirectionVector] = []
    for d in picked:
        exit_tick = first_exit_tick_straight(grid, start, d, speed, dt, tw_max)
        if exit_tick is not None and exit_tick <= tw_max:
            continue
        kept.append(d)
    ESCAPE_DEBUG_LAST["pva_launch_count"] = len(kept)
    return kept


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
) -> list[DirectionVector]:
    del speed, dt, max_primary, max_total, lookahead_steps
    return legal_turn_directions(
        grid,
        position,
        current_heading,
        valid_exit_tiles,
        exit_stripe_half=exit_stripe_half,
        max_turn_angle_deg=None,
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
