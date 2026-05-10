import colorsys
import math
import sys
from datetime import datetime
from pathlib import Path

import pygame

from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from core.vector import Vector2D
from game.app import App
from game import constants as C
from game.pva_rules import all_border_tiles, is_border_tile


class Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()


class SoundManager:
    """
    Cosmetic-only sound helper. All sounds are optional and failure-safe:
    missing files or mixer init failures should never crash the game.
    """

    CH_PLANNING = 0
    CH_AIRCRAFT = 1
    CH_EFFECTS = 2
    CH_RESULT = 3

    def __init__(self) -> None:
        self.enabled = False
        self._warned: set[str] = set()

        self._snd_missile = None
        self._snd_win = None
        self._snd_lose = None
        self._snd_planning = None
        self._snd_aircraft = None

        self._ch_planning = None
        self._ch_aircraft = None
        self._ch_effects = None
        self._ch_result = None

        # Event de-spam / scheduling
        self._prev_mode: str | None = None
        self._prev_phase: str | None = None
        self._prev_tick: int | None = None
        self._prev_missile_count: int = 0
        self._result_played_for_round: bool = False
        self._missile_stop_at_ms: int = 0
        self._muted: bool = False

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        print(msg)

    def init(self) -> None:
        try:
            pygame.mixer.init()
            self.enabled = True
        except Exception as e:
            self.enabled = False
            self._warn_once("mixer_init", f"[sound] mixer init failed; sounds disabled ({e})")
            return

        self._ch_planning = pygame.mixer.Channel(self.CH_PLANNING)
        self._ch_aircraft = pygame.mixer.Channel(self.CH_AIRCRAFT)
        self._ch_effects = pygame.mixer.Channel(self.CH_EFFECTS)
        self._ch_result = pygame.mixer.Channel(self.CH_RESULT)

        base = Path("assets") / "sounds"
        self._snd_missile = self._load_sound(base / "missile_launch.mp3", "missile_launch.mp3", volume=0.70)
        self._snd_win = self._load_sound(base / "result_win.mp3", "result_win.mp3", volume=0.80)
        self._snd_lose = self._load_sound(base / "result_lose.mp3", "result_lose.mp3", volume=0.80)
        self._snd_planning = self._load_sound(base / "planning_loop.mp3", "planning_loop.mp3", volume=0.25)
        self._snd_aircraft = self._load_sound(base / "aircraft_idle_loop.mp3", "aircraft_idle_loop.mp3", volume=0.20)

    def _load_sound(self, path: Path, name: str, *, volume: float) -> "pygame.mixer.Sound | None":
        try:
            if not path.exists():
                self._warn_once(f"missing:{name}", f"[sound] missing: {path} (skipping)")
                return None
            snd = pygame.mixer.Sound(str(path))
            snd.set_volume(float(volume))
            return snd
        except Exception as e:
            self._warn_once(f"load_fail:{name}", f"[sound] failed to load {path} (skipping): {e}")
            return None

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._muted:
            self.stop_all(immediate=True)

    def is_muted(self) -> bool:
        return bool(self._muted)

    def stop_all(self, *, immediate: bool = False) -> None:
        if not self.enabled:
            return
        for ch in (self._ch_planning, self._ch_aircraft, self._ch_effects, self._ch_result):
            try:
                if ch is not None:
                    if immediate:
                        ch.stop()
                    else:
                        ch.fadeout(200)
            except Exception:
                pass

    def update(self, app: App, *, tick_ms: float) -> None:
        if not self.enabled:
            return
        if self._muted:
            if self._ch_planning is not None and self._ch_planning.get_busy():
                self._ch_planning.stop()
            if self._ch_aircraft is not None and self._ch_aircraft.get_busy():
                self._ch_aircraft.stop()
            if self._ch_effects is not None and self._ch_effects.get_busy():
                self._ch_effects.stop()
            if self._ch_result is not None and self._ch_result.get_busy():
                self._ch_result.stop()
            self._missile_stop_at_ms = 0
        now_ms = int(pygame.time.get_ticks())

        mode = getattr(app, "mode", None)
        phase = getattr(app, "pva_phase", None)
        s = getattr(app, "state", None)

        # Track "round" boundaries to ensure result plays once per round.
        if mode == App.MODE_PVA and phase == App.PVA_RUNNING and self._prev_phase != App.PVA_RUNNING:
            self._result_played_for_round = False

        # Stop loops when leaving PVA or when the round ends.
        scenario_done = False
        try:
            scenario_done = bool(app.scenario_finished())
        except Exception:
            scenario_done = False

        if mode != App.MODE_PVA or scenario_done or phase == App.PVA_END:
            if not self._muted:
                if self._ch_planning is not None and self._ch_planning.get_busy():
                    self._ch_planning.fadeout(220)
                if self._ch_aircraft is not None and self._ch_aircraft.get_busy():
                    self._ch_aircraft.fadeout(220)

        # Aircraft idle loop while PVA running.
        if (
            mode == App.MODE_PVA
            and phase == App.PVA_RUNNING
            and not scenario_done
            and getattr(app, "has_aircraft", False)
            and (not self._muted)
        ):
            if self._snd_aircraft is not None and self._ch_aircraft is not None and (not self._ch_aircraft.get_busy()):
                self._ch_aircraft.play(self._snd_aircraft, loops=-1, fade_ms=120)

        # Planning loop: quiet loop while the SAM is actively planning/replanning (PVA running, has lock, not fired).
        has_lock = (getattr(app, "inferred_direction", None) is not None) and (getattr(app, "inferred_speed", None) is not None)
        fired = False
        try:
            fired = bool(s is not None and s.sam_truck.has_fired)
        except Exception:
            fired = False
        planning_active = (
            mode == App.MODE_PVA
            and phase == App.PVA_RUNNING
            and (not scenario_done)
            and has_lock
            and (not fired)
            and (not self._muted)
        )
        if planning_active:
            if self._snd_planning is not None and self._ch_planning is not None and (not self._ch_planning.get_busy()):
                self._ch_planning.play(self._snd_planning, loops=-1, fade_ms=140)
        else:
            if not self._muted and self._ch_planning is not None and self._ch_planning.get_busy():
                self._ch_planning.fadeout(200)

        # Missile launch effect: detect newly created missiles; clamp to 2.5s max.
        missile_count = 0
        try:
            missile_count = len(s.missiles) if s is not None else 0
        except Exception:
            missile_count = 0
        if missile_count > self._prev_missile_count:
            if (
                (not self._muted)
                and self._snd_missile is not None
                and self._ch_effects is not None
            ):
                self._ch_effects.play(self._snd_missile, loops=0, fade_ms=20)
                self._missile_stop_at_ms = now_ms + 2500
        if self._missile_stop_at_ms and now_ms >= self._missile_stop_at_ms:
            self._missile_stop_at_ms = 0
            if (
                not self._muted
                and self._ch_effects is not None
                and self._ch_effects.get_busy()
            ):
                self._ch_effects.fadeout(120)

        # Result sounds: play once per PVA round end (still mark consumed if muted).
        if mode == App.MODE_PVA and scenario_done and (not self._result_played_for_round):
            res_text = str(getattr(app, "pva_result_text", "") or "")
            win = ("ESCAPED" in res_text) or ("escaped" in res_text.lower())
            if not self._muted:
                snd = self._snd_win if win else self._snd_lose
                if snd is not None and self._ch_result is not None:
                    self._ch_result.play(snd, loops=0, fade_ms=40)
            self._result_played_for_round = True

        # Leaving PVA: ensure loops are stopped.
        if self._prev_mode == App.MODE_PVA and mode != App.MODE_PVA:
            self.stop_all()

        self._prev_mode = mode
        self._prev_phase = phase
        self._prev_tick = int(getattr(s, "tick", 0)) if s is not None else None
        self._prev_missile_count = missile_count


CELL_SIZE = 38
INFO_HEIGHT = 96
CTRL_HEIGHT = 44
PANEL_HEIGHT = INFO_HEIGHT + CTRL_HEIGHT
RENDER_FPS = 60
MAX_SIM_STEPS_PER_FRAME = 1
PAUSE_DURATION_MS = 1800
MAJOR_GRID = 4

COL_BG = (12, 12, 18)
COL_GRID_MINOR = (28, 28, 40)
COL_GRID_MAJOR = (55, 55, 80)
COL_AIRCRAFT = (60, 220, 80)
COL_AIRCRAFT_UNK = (120, 200, 120)
COL_TRUCK = (60, 120, 255)
COL_MISSILE = (255, 80, 50)
COL_TEXT = (220, 220, 220)
COL_DIM = (110, 110, 125)
COL_PERF_OK = (130, 200, 130)
COL_PERF_SLOW = (255, 140, 40)
COL_HIT = (255, 210, 0)
COL_FAIL = (220, 70, 70)
COL_SUCCESS = (80, 220, 120)
COL_PANEL_BG = (20, 20, 32)
COL_PANEL_SEP = (48, 48, 68)
COL_BTN = (50, 50, 72)
COL_BTN_HOVER = (75, 75, 108)
COL_BTN_ACTIVE = (90, 150, 255)
COL_BTN_TEXT = (215, 215, 215)
COL_EDGE_VALID = (70, 220, 90)
COL_EDGE_INVALID = (210, 70, 70)
COL_TILE_HOVER = (255, 255, 255)
COL_TILE_LOCK = (255, 220, 80)
COL_PREVIEW = (180, 190, 245, 75)
COL_PREVIEW_SUBTLE = (140, 150, 210, 60)
COL_TURN = (235, 235, 120, 72)
COL_TURN_HI = (255, 255, 200, 165)


class PictureManager:
    """Load UI/sprites once; missing files warn once and fall back to polygon drawing."""

    def __init__(self) -> None:
        self._warned: set[str] = set()
        self.win_img: pygame.Surface | None = None
        self.lose_img: pygame.Surface | None = None
        self.sam_loaded: pygame.Surface | None = None
        self.sam_unloaded: pygame.Surface | None = None
        self.missile: pygame.Surface | None = None
        self.aircraft_flying: pygame.Surface | None = None
        self.aircraft_crashed: pygame.Surface | None = None

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        print(msg)

    def _load_scaled(self, path: Path, name: str, max_side: int) -> pygame.Surface | None:
        try:
            if not path.exists():
                self._warn_once(f"missing:{name}", f"[picture] missing: {path} (fallback drawing)")
                return None
            surf = pygame.image.load(str(path)).convert_alpha()
            w, h = surf.get_size()
            if w <= 0 or h <= 0:
                return None
            scale = float(max_side) / float(max(w, h))
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            out = pygame.transform.smoothscale(surf, (nw, nh))
            return out.convert_alpha()
        except Exception as e:
            self._warn_once(f"fail:{name}", f"[picture] failed to load {path}: {e}")
            return None

    def load(self, *, grid_px_w: int, grid_px_h: int) -> None:
        base = Path("assets") / "pictures"
        # Larger icons (north-facing sprites; max dimension ~ cell multiples).
        px_air = max(18, int(CELL_SIZE * 1.925))  # ~+10% vs prior aircraft scale
        px_sam = max(19, int(CELL_SIZE * 2.365))  # ~+10% vs prior SAM scale
        px_ms = max(15, int(CELL_SIZE * 1.31))   # ~+7% vs prior missile scale
        self.sam_loaded = self._load_scaled(base / "sam_loaded.png", "sam_loaded.png", px_sam)
        self.sam_unloaded = self._load_scaled(base / "sam_unloaded.png", "sam_unloaded.png", px_sam)
        self.missile = self._load_scaled(base / "missile.png", "missile.png", px_ms)
        self.aircraft_flying = self._load_scaled(base / "aircraft_flying.png", "aircraft_flying.png", px_air)
        self.aircraft_crashed = self._load_scaled(base / "aircraft_crashed.png", "aircraft_crashed.png", px_air)
        overlay_max = max(48, min(grid_px_w, grid_px_h, int(min(grid_px_w, grid_px_h) * 0.65)))
        self.win_img = self._load_scaled(base / "win.png", "win.png", overlay_max)
        self.lose_img = self._load_scaled(base / "lose.png", "lose.png", overlay_max)


def sprite_rotation_degrees(direction: DirectionVector) -> float:
    angle_deg = math.degrees(math.atan2(direction.y, direction.x))
    return -(angle_deg + 90.0)


def _rotate_sprite_with_alpha(src: pygame.Surface, angle_deg: float) -> pygame.Surface:
    """Rotate without colorkey/layout artifacts: blit onto SRCALPHA before rotate."""
    w, h = src.get_width(), src.get_height()
    pad = pygame.Surface((w, h), pygame.SRCALPHA)
    pad.blit(src, (0, 0))
    return pygame.transform.rotate(pad, angle_deg)


def _blit_rotated_sprite(
    screen,
    base: pygame.Surface | None,
    cx: float,
    cy: float,
    direction: DirectionVector,
    *,
    fallback_draw,
) -> None:
    if base is None:
        fallback_draw()
        return
    rot = _rotate_sprite_with_alpha(base, sprite_rotation_degrees(direction))
    r = rot.get_rect(center=(int(cx), int(cy)))
    screen.blit(rot, r)


class Button:
    def __init__(self, rect: pygame.Rect, label: str, key_hint: str = ""):
        self.rect = rect
        self.label = label
        self.key_hint = key_hint
        self.hovered = False

    def draw(self, screen, font, active: bool = False) -> None:
        col = COL_BTN_ACTIVE if active else (COL_BTN_HOVER if self.hovered else COL_BTN)
        pygame.draw.rect(screen, col, self.rect, border_radius=4)
        pygame.draw.rect(screen, COL_PANEL_SEP, self.rect, 1, border_radius=4)
        text = self.label + (f" [{self.key_hint}]" if self.key_hint else "")
        surf = font.render(text, True, COL_BTN_TEXT)
        screen.blit(surf, surf.get_rect(center=self.rect.center))

    def check_hover(self, pos) -> None:
        self.hovered = self.rect.collidepoint(pos)

    def is_clicked(self, pos) -> bool:
        return self.rect.collidepoint(pos)


def draw_grid(screen, grid) -> None:
    for x in range(grid.width + 1):
        col, w = (COL_GRID_MAJOR, 2) if x % MAJOR_GRID == 0 else (COL_GRID_MINOR, 1)
        pygame.draw.line(screen, col, (x * CELL_SIZE, 0), (x * CELL_SIZE, grid.height * CELL_SIZE), w)
    for y in range(grid.height + 1):
        col, w = (COL_GRID_MAJOR, 2) if y % MAJOR_GRID == 0 else (COL_GRID_MINOR, 1)
        pygame.draw.line(screen, col, (0, y * CELL_SIZE), (grid.width * CELL_SIZE, y * CELL_SIZE), w)


def draw_pva_result_overlay(screen, app: App, pictures: PictureManager | None, grid) -> None:
    if pictures is None or app.mode != App.MODE_PVA or app.pva_phase != App.PVA_END:
        return
    rt = str(getattr(app, "pva_result_text", "") or "")
    s = app.state
    img = None
    if ("ESCAPED" in rt) or ("escaped" in rt.lower()) or getattr(s, "escaped", False):
        img = pictures.win_img
    elif (
        ("INTERCEPTED" in rt)
        or ("ILLEGAL" in rt.upper())
        or ("FAILED_ILLEGAL" in rt.upper())
        or getattr(s, "intercepted", False)
        or getattr(s, "failed", False)
    ):
        img = pictures.lose_img
    if img is None:
        return
    gw = int(grid.width * CELL_SIZE)
    gh = int(grid.height * CELL_SIZE)
    dim = pygame.Surface((gw, gh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 105))
    try:
        screen.blit(dim, (0, 0))
        x = (gw - img.get_width()) // 2
        y = (gh - img.get_height()) // 2
        screen.blit(img, (x, max(8, y)))
    except Exception:
        pass


def _rot(pts, cx, cy, a):
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + lx * ca - ly * sa, cy + lx * sa + ly * ca) for lx, ly in pts]


def draw_aircraft(
    screen,
    pos,
    direction,
    has_radar,
    *,
    pictures: PictureManager | None = None,
    crashed: bool = False,
) -> None:
    cx = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = pos.y * CELL_SIZE + CELL_SIZE / 2
    r = CELL_SIZE * 0.38

    def _fallback_unk() -> None:
        pts = [(0, -r), (r * 0.55, 0), (0, r), (-r * 0.55, 0)]
        pygame.draw.polygon(screen, COL_AIRCRAFT_UNK, [(cx + x, cy + y) for x, y in pts])
        pygame.draw.polygon(screen, COL_AIRCRAFT, [(cx + x, cy + y) for x, y in pts], 2)

    def _fallback_known() -> None:
        if direction is None:
            _fallback_unk()
            return
        angle = math.atan2(direction.y, direction.x)
        poly = _rot([(r, 0), (-r * 0.7, -r * 0.5), (-r * 0.4, 0), (-r * 0.7, r * 0.5)], cx, cy, angle)
        pygame.draw.polygon(screen, COL_AIRCRAFT, poly)
        pygame.draw.polygon(screen, (160, 255, 160), poly, 1)

    up_dir = nearest_direction(0.0, -1.0)
    rot_dir = direction if direction is not None else up_dir

    if crashed:
        _blit_rotated_sprite(
            screen,
            pictures.aircraft_crashed if pictures is not None else None,
            cx,
            cy,
            rot_dir,
            fallback_draw=_fallback_known,
        )
        return

    if not has_radar or direction is None:
        _blit_rotated_sprite(
            screen,
            pictures.aircraft_flying if pictures is not None else None,
            cx,
            cy,
            up_dir,
            fallback_draw=_fallback_unk,
        )
    else:
        _blit_rotated_sprite(
            screen,
            pictures.aircraft_flying if pictures is not None else None,
            cx,
            cy,
            direction,
            fallback_draw=_fallback_known,
        )


def draw_truck(
    screen,
    pos,
    direction,
    *,
    has_fired: bool,
    pictures: PictureManager | None = None,
) -> None:
    cx, cy = pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2

    def _fallback() -> None:
        angle = math.atan2(direction.y, direction.x)
        hw, hh = CELL_SIZE * 0.42, CELL_SIZE * 0.28
        poly = _rot([(hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)], cx, cy, angle)
        pygame.draw.polygon(screen, COL_TRUCK, poly)
        pygame.draw.polygon(screen, (140, 180, 255), poly, 2)

    base = None
    if pictures is not None:
        base = pictures.sam_unloaded if has_fired else pictures.sam_loaded
    _blit_rotated_sprite(screen, base, cx, cy, direction, fallback_draw=_fallback)


def draw_missile(screen, pos, direction, *, pictures: PictureManager | None = None) -> None:
    cx, cy = pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2
    rr = CELL_SIZE * 0.22

    def _fallback() -> None:
        angle = math.atan2(direction.y, direction.x)
        poly = _rot([(rr, 0), (-rr, -rr * 0.5), (-rr * 0.5, 0), (-rr, rr * 0.5)], cx, cy, angle)
        pygame.draw.polygon(screen, COL_MISSILE, poly)

    _blit_rotated_sprite(
        screen,
        pictures.missile if pictures is not None else None,
        cx,
        cy,
        direction,
        fallback_draw=_fallback,
    )


def world_mouse_vector(mouse_pos, anchor_world: Vector2D) -> tuple[float, float]:
    anchor_px = anchor_world.x * CELL_SIZE + CELL_SIZE / 2
    anchor_py = anchor_world.y * CELL_SIZE + CELL_SIZE / 2
    return mouse_pos[0] - anchor_px, mouse_pos[1] - anchor_py


def tile_rect(tile: tuple[int, int]) -> pygame.Rect:
    x, y = tile
    return pygame.Rect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE, CELL_SIZE)


def draw_tile_border(screen, tile: tuple[int, int], color, width: int = 3) -> None:
    pygame.draw.rect(screen, color, tile_rect(tile), width)


def draw_outer_edge_outline(screen, grid, tile: tuple[int, int], color, width: int = 4) -> None:
    x, y = tile
    rect = tile_rect(tile)
    if y == 0:
        pygame.draw.line(screen, color, rect.topleft, rect.topright, width)
    if y == grid.height - 1:
        pygame.draw.line(screen, color, rect.bottomleft, rect.bottomright, width)
    if x == 0:
        pygame.draw.line(screen, color, rect.topleft, rect.bottomleft, width)
    if x == grid.width - 1:
        pygame.draw.line(screen, color, rect.topright, rect.bottomright, width)


def draw_all_edge_outlines(screen, grid, valid_tiles: set[tuple[int, int]]) -> None:
    for tile in all_border_tiles(grid):
        color = COL_EDGE_VALID if tile in valid_tiles else COL_EDGE_INVALID
        draw_outer_edge_outline(screen, grid, tile, color)


def _dir_color(direction: DirectionVector | None) -> tuple[int, int, int]:
    if direction is None:
        return COL_TILE_LOCK
    angle = (math.degrees(math.atan2(direction.y, direction.x)) % 360.0) / 360.0
    r, g, b = colorsys.hsv_to_rgb(angle, 0.55, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_direction_fan(screen, origin_tile: tuple[int, int], directions: list[DirectionVector], highlight: DirectionVector | None) -> None:
    if origin_tile is None:
        return
    ox = origin_tile[0] * CELL_SIZE + CELL_SIZE / 2
    oy = origin_tile[1] * CELL_SIZE + CELL_SIZE / 2
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    for d in directions:
        end = (ox + d.x * CELL_SIZE * 3.2, oy + d.y * CELL_SIZE * 3.2)
        col = COL_PREVIEW_SUBTLE
        width = 2
        is_hi = highlight is not None and d.index == highlight.index
        if is_hi:
            hc = _dir_color(highlight)
            pygame.draw.line(overlay, (0, 0, 0, 125), (ox, oy), end, 6)
            pygame.draw.line(overlay, (*hc, 168), (ox, oy), end, 3)
            pygame.draw.line(overlay, (*hc, 115), (ox, oy), end, 2)
        else:
            pygame.draw.line(overlay, (0, 0, 0, 45), (ox, oy), end, width + 2)
            pygame.draw.line(overlay, col, (ox, oy), end, width)
    screen.blit(overlay, (0, 0))


def draw_preview_path(screen, grid, origin_tile: tuple[int, int], direction: DirectionVector | None, color) -> None:
    if origin_tile is None or direction is None:
        return
    # Display-only: draw ray from tile center and clip to grid pixel rect.
    x1 = float(origin_tile[0]) * CELL_SIZE + CELL_SIZE / 2
    y1 = float(origin_tile[1]) * CELL_SIZE + CELL_SIZE / 2
    # Large enough to reach any border after clipping.
    ray_len = float(max(grid.width, grid.height) * CELL_SIZE * 4)
    x2 = x1 + float(direction.x) * ray_len
    y2 = y1 + float(direction.y) * ray_len
    grid_rect = pygame.Rect(0, 0, int(grid.width * CELL_SIZE), int(grid.height * CELL_SIZE))
    clipped = grid_rect.clipline((x1, y1), (x2, y2))
    if not clipped:
        return
    (cx1, cy1), (cx2, cy2) = clipped
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    p1 = (float(cx1), float(cy1))
    p2 = (float(cx2), float(cy2))
    pygame.draw.line(overlay, (0, 0, 0, 132), p1, p2, 6)
    pygame.draw.line(overlay, (*color, 150), p1, p2, 3)
    pygame.draw.line(overlay, (255, 255, 255, 48), p1, p2, 2)
    screen.blit(overlay, (0, 0))


def _ray_box_positive_exit_length(
    px: float,
    py: float,
    ux: float,
    uy: float,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> float | None:
    """Smallest ``t`` > 0 so ``(px + t*ux, py + t*uy)`` lies on ``[x0,x1]×[y0,y1]`` boundary."""
    eps = 1e-9
    ts: list[float] = []
    if abs(ux) > eps:
        for xv in (x0, x1):
            t = (xv - px) / ux
            if t <= eps:
                continue
            yy = py + uy * t
            if y0 - 1e-3 <= yy <= y1 + 1e-3:
                ts.append(t)
    if abs(uy) > eps:
        for yv in (y0, y1):
            t = (yv - py) / uy
            if t <= eps:
                continue
            xx = px + ux * t
            if x0 - 1e-3 <= xx <= x1 + 1e-3:
                ts.append(t)
    return min(ts) if ts else None


def draw_future_paths(screen, paths, grid=None) -> None:
    if paths is None:
        return
    if len(paths) == 0:
        return
    try:
        ww, hh = screen.get_size()
        overlay = pygame.Surface((ww, hh), pygame.SRCALPHA)
    except Exception:
        return

    gw_p = ww
    gh_p = hh
    if grid is not None:
        try:
            gw_p = float(grid.width) * CELL_SIZE
            gh_p = float(grid.height) * CELL_SIZE
        except Exception:
            pass

    muted = (
        (120, 175, 255, 82),
        (165, 140, 240, 80),
        (130, 220, 205, 79),
        (235, 175, 130, 78),
        (175, 175, 240, 81),
    )
    main_w, outline_w = 2, 4

    for pi, raw_path in enumerate(paths[:5]):
        if raw_path is None or len(raw_path) < 2:
            continue
        pts: list[tuple[float, float]] = []
        for pos in raw_path:
            px = getattr(pos, "x", None)
            py = getattr(pos, "y", None)
            if px is None or py is None:
                pts = []
                break
            try:
                cx = float(px) * CELL_SIZE + CELL_SIZE / 2
                cy = float(py) * CELL_SIZE + CELL_SIZE / 2
            except Exception:
                pts = []
                break
            pts.append((cx, cy))

        if len(pts) < 2:
            continue
        col = muted[pi % len(muted)]
        try:
            pygame.draw.lines(overlay, (0, 0, 0, 92), False, pts, outline_w)
            pygame.draw.lines(overlay, col, False, pts, main_w)
        except Exception:
            pass

        xa, ya = pts[-2]
        xb, yb = pts[-1]
        dx = xb - xa
        dy = yb - ya
        ln = math.hypot(dx, dy)
        if ln >= 1e-6:
            uxu, uyu = dx / ln, dy / ln
            t_candidates: list[float] = []
            t_g = _ray_box_positive_exit_length(xb, yb, uxu, uyu, 0.0, 0.0, gw_p, gh_p)
            t_s = _ray_box_positive_exit_length(xb, yb, uxu, uyu, 0.0, 0.0, float(ww), float(hh))
            if t_g is not None:
                t_candidates.append(t_g)
            if t_s is not None:
                t_candidates.append(t_s)
            if t_candidates:
                t_ext = min(t_candidates)
                x2 = xb + uxu * t_ext
                y2 = yb + uyu * t_ext
                ext_col = (col[0], col[1], col[2], min(72, max(42, col[3] + 8)))
                try:
                    pygame.draw.line(overlay, (0, 0, 0, 52), (xb, yb), (x2, y2), 2)
                    pygame.draw.line(overlay, ext_col, (xb, yb), (x2, y2), 2)
                except Exception:
                    pass

    try:
        screen.blit(overlay, (0, 0))
    except Exception:
        pass


def draw_turn_wheel(screen, pos: Vector2D, directions: list[DirectionVector], highlight: DirectionVector | None) -> None:
    cx = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = pos.y * CELL_SIZE + CELL_SIZE / 2
    if not directions:
        return
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    for d in directions:
        end = (cx + d.x * CELL_SIZE * 1.6, cy + d.y * CELL_SIZE * 1.6)
        col = COL_TURN_HI if (highlight is not None and d.index == highlight.index) else COL_TURN
        width = 3 if (highlight is not None and d.index == highlight.index) else 1
        pygame.draw.line(overlay, col, (cx, cy), end, width)
    screen.blit(overlay, (0, 0))


def draw_menu(screen, font, font_small, screen_w, screen_h, buttons: list[Button]) -> None:
    title = font.render("AvoidSAM", True, COL_TEXT)
    subtitle = font_small.render("Choose a mode", True, COL_DIM)
    screen.blit(title, title.get_rect(center=(screen_w // 2, screen_h // 2 - 120)))
    screen.blit(subtitle, subtitle.get_rect(center=(screen_w // 2, screen_h // 2 - 88)))
    for btn in buttons:
        btn.draw(screen, font_small)


def draw_auto_panel(
    screen,
    app,
    font,
    font_small,
    info_y,
    ctrl_y,
    screen_w,
    ctrl_btns,
    scen_btns,
    menu_btn,
    mute_btn: Button | None,
    muted: bool,
    playing,
    pause_timer,
    render_fps,
) -> None:
    s = app.state
    scenario = app.current_scenario
    idx = app.scenario_index + 1
    total = len(app.scenarios)

    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 1)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)

    if s.intercepted:
        status, sc = "INTERCEPTED", COL_HIT
    elif getattr(s, "failed", False):
        status, sc = "FAILED", COL_FAIL
    elif s.tick >= C.MAX_STEPS:
        status, sc = "TIMEOUT", COL_DIM
    elif not playing:
        status, sc = "PAUSED", (200, 160, 60)
    else:
        status, sc = "running", COL_TEXT

    auto_str = (f" next in {pause_timer // 1000 + 1}s" if pause_timer > 0 and playing else "")
    truck = s.sam_truck
    diag = app.last_diag
    line1 = f"Sc {idx}/{total}: {scenario.name}   Tick {s.tick}/{C.MAX_STEPS}   {status}{auto_str}"
    line2 = (
        f"AC spd={'{:.1f}'.format(app.inferred_speed) if app.inferred_speed else '?'} "
        f"dir={_dir_str(app.inferred_direction)}  |  Truck dir={_dir_str(truck.direction)}"
        f"  |  {app.last_plan_type or '—'}  {app.last_action or '—'}"
    )
    m_parts = [f"M{i} {_dir_str(m.direction)}" for i, m in enumerate(s.missiles) if m.active]
    no_sol = f"  [{diag.no_solution_reason}]" if diag.no_solution_reason else ""
    line3 = ("  ".join(m_parts) if m_parts else "No missiles") + no_sol
    tick_budget = 1000.0 / C.TICK_RATE
    perf_col = COL_PERF_SLOW if app.last_planner_ms > tick_budget else COL_PERF_OK
    line4 = (
        f"plan={app.last_planner_ms:.1f}ms  step={app.last_step_ms:.1f}ms  rfps={render_fps:.0f}"
        f"  cands={diag.candidates_evaluated}  ver={diag.directions_verified}"
        f"  fb={'Y' if diag.fallback_used else 'N'}"
    )

    screen.blit(font.render(line1, True, sc), (10, info_y + 3))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 23))
    screen.blit(font_small.render(line3, True, COL_DIM), (10, info_y + 41))
    screen.blit(font_small.render(line4, True, perf_col), (10, info_y + 59))

    for btn in ctrl_btns:
        active = (btn.label == "Pause" and not playing) or (btn.label == "Play" and playing)
        btn.draw(screen, font_small, active=active)
    for i, sbtn in enumerate(scen_btns):
        sbtn.draw(screen, font_small, active=(i == app.scenario_index))
    menu_btn.draw(screen, font_small)
    if mute_btn is not None:
        mute_btn.label = "Unmute" if muted else "Mute"
        mute_btn.draw(screen, font_small)


def draw_pva_panel(
    screen,
    app,
    font,
    font_small,
    info_y,
    ctrl_y,
    screen_w,
    restart_btn,
    menu_btn,
    mute_btn: Button | None,
    muted: bool,
    render_fps,
) -> None:
    s = app.state
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 1)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)

    if s.intercepted:
        status, sc = "INTERCEPTED", COL_HIT
    elif s.escaped:
        status, sc = "ESCAPED", COL_SUCCESS
    elif s.failed:
        status, sc = "FAILED", COL_FAIL
    elif app.pva_phase == App.PVA_RUNNING:
        status, sc = "RUNNING", COL_TEXT
    else:
        status, sc = app.pva_phase.replace("_", " ").upper(), COL_TEXT

    preview = app.pva_preview
    line1 = f"Player vs Agent   Tick {s.tick}/{C.MAX_STEPS}   {status}"
    line2 = (
        f"Spawn={preview.tile if preview.tile is not None else '?'}  "
        f"Dir={_dir_str(preview.direction)}  "
        f"Turn={'USED' if app.pva_turn_used else 'READY'}  "
        f"Action={app.last_action or '—'}"
    )
    if app.pva_phase == App.PVA_RUNNING:
        line3 = (
            f"Valid exits: {len(app.pva_locked_exit_tiles)} tiles  "
            f"Turn UI={len(app.pva_player_turn_dirs)} dirs  SAM threat={len(app.pva_sam_threat_turn_dirs)}  "
            f"Truck plan={app.last_plan_type or '—'}  "
            f"Planner {app.last_planner_ms:.1f}ms"
        )
    else:
        line3 = (
            f"Valid launch dirs: {len(app.pva_valid_launch_dirs)}  "
            f"Valid exits: {len(preview.exit_tiles)}  "
            f"LMB confirm / RMB back"
        )

    diag = app.last_diag
    perf_bits = (
        f"stp={app.last_step_ms:.1f}ms rfps={render_fps:.0f} "
        f"cands={diag.candidates_evaluated} ver={diag.directions_verified}"
    )
    ai_raw = getattr(app, "ai_plan_summary", "") or ""
    ai_raw = ai_raw.strip() if isinstance(ai_raw, str) else ""
    avail = max(72, screen_w // 22)
    if ai_raw:
        short_ai = ai_raw[:avail].rstrip()
        line4 = f"{short_ai}  ·  {perf_bits}"
    else:
        line4 = perf_bits

    screen.blit(font.render(line1, True, sc), (10, info_y + 3))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 23))
    screen.blit(font_small.render(line3, True, COL_DIM), (10, info_y + 41))
    screen.blit(font_small.render(line4, True, COL_PERF_OK), (10, info_y + 59))

    restart_btn.draw(screen, font_small)
    menu_btn.draw(screen, font_small)
    if mute_btn is not None:
        mute_btn.label = "Unmute" if muted else "Mute"
        mute_btn.draw(screen, font_small)


def build_auto_buttons(ctrl_y: int, screen_w: int):
    bw, bh, gap, pad = 74, 28, 6, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    ctrl_btns = [
        Button(pygame.Rect(pad + i * (bw + gap), cy, bw, bh), lbl, hint)
        for i, (lbl, hint) in enumerate([("Play", "Spc"), ("Pause", "Spc"), ("Prev", "←"), ("Next", "→")])
    ]
    sw = 28
    sx = pad + 4 * (bw + gap) + 14
    scen_btns = [Button(pygame.Rect(sx + i * (sw + 4), cy, sw, bh), str(i + 1)) for i in range(12)]
    menu_btn = Button(pygame.Rect(sx + 12 * (sw + 4) + 18, cy, 88, bh), "Menu")
    mute_btn = Button(pygame.Rect(max(10, screen_w - 74 - 8), cy, 72, bh), "Mute")
    return ctrl_btns, scen_btns, menu_btn, mute_btn


def build_menu_buttons(screen_w: int, screen_h: int) -> list[Button]:
    bw, bh = 260, 44
    x = screen_w // 2 - bw // 2
    y = screen_h // 2 - 30
    return [
        Button(pygame.Rect(x, y, bw, bh), "Automatic System"),
        Button(pygame.Rect(x, y + 60, bw, bh), "Player vs Agent"),
    ]


def build_pva_buttons(ctrl_y: int, screen_w: int):
    bw, bh, gap, pad = 100, 28, 8, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    restart_btn = Button(pygame.Rect(pad, cy, bw, bh), "Restart")
    menu_btn = Button(pygame.Rect(pad + bw + gap, cy, bw, bh), "Menu")
    mute_btn = Button(pygame.Rect(max(10, screen_w - 78), cy, 72, bh), "Mute")
    return restart_btn, menu_btn, mute_btn


def _dir_str(d: DirectionVector | None) -> str:
    if d is None:
        return "UNK"
    return f"{math.degrees(math.atan2(d.y, d.x)) % 360:.0f}°"


def _run_app() -> None:
    pygame.init()
    app = App()
    s = app.state
    screen_w = s.grid.width * CELL_SIZE
    grid_h = s.grid.height * CELL_SIZE
    info_y = grid_h
    ctrl_y = grid_h + INFO_HEIGHT
    screen_h = grid_h + PANEL_HEIGHT

    screen = pygame.display.set_mode((screen_w, screen_h))
    pygame.display.set_caption("AvoidSAM")
    font = pygame.font.SysFont("monospace", 13)
    font_small = pygame.font.SysFont("monospace", 11)
    clock = pygame.time.Clock()
    sounds = SoundManager()
    sounds.init()
    pictures = PictureManager()
    pictures.load(grid_px_w=int(s.grid.width * CELL_SIZE), grid_px_h=int(s.grid.height * CELL_SIZE))

    menu_buttons = build_menu_buttons(screen_w, screen_h)
    auto_ctrl_btns, auto_scen_btns, auto_menu_btn, auto_mute_btn = build_auto_buttons(ctrl_y, screen_w)
    pva_restart_btn, pva_menu_btn, pva_mute_btn = build_pva_buttons(ctrl_y, screen_w)

    playing = True
    pause_timer = 0
    tick_ms = 1000.0 / C.TICK_RATE
    acc_ms = 0.0

    running = True
    while running:
        dt_ms = clock.tick(RENDER_FPS)
        render_fps = clock.get_fps()
        mouse_pos = pygame.mouse.get_pos()

        active_buttons = []
        if app.mode == App.MODE_MENU:
            active_buttons = menu_buttons
        elif app.mode == App.MODE_AUTOMATIC:
            active_buttons = auto_ctrl_btns + auto_scen_btns + [auto_menu_btn, auto_mute_btn]
        elif app.mode == App.MODE_PVA:
            active_buttons = [pva_restart_btn, pva_menu_btn, pva_mute_btn]
        for btn in active_buttons:
            btn.check_hover(mouse_pos)

        # Hover updates
        if app.mode == App.MODE_PVA:
            if app.pva_phase == App.PVA_TILE_SELECT and mouse_pos[1] < grid_h:
                tile = (mouse_pos[0] // CELL_SIZE, mouse_pos[1] // CELL_SIZE)
                app.set_pva_hover_tile(tile if is_border_tile(app.state.grid, tile) else None)
            elif app.pva_phase in (App.PVA_ANGLE_SELECT, App.PVA_CONFIRM) and app.pva_locked_tile is not None:
                anchor = Vector2D(float(app.pva_locked_tile[0]), float(app.pva_locked_tile[1]))
                dx, dy = world_mouse_vector(mouse_pos, anchor)
                app.update_pva_hover_direction(dx, dy)
            elif app.pva_phase == App.PVA_RUNNING and not app.pva_turn_used and app.has_aircraft:
                dx, dy = world_mouse_vector(mouse_pos, app.state.aircraft.position)
                app.update_pva_turn_hover(dx, dy)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if app.mode == App.MODE_MENU:
                        running = False
                    else:
                        sounds.stop_all(immediate=True)
                        app.back_to_menu()
                        pause_timer = 0
                        acc_ms = 0.0
                elif app.mode == App.MODE_AUTOMATIC:
                    if event.key == pygame.K_SPACE:
                        playing = not playing
                    elif event.key == pygame.K_RIGHT:
                        app.advance_to_next_scenario()
                        pause_timer = 0
                        acc_ms = 0.0
                    elif event.key == pygame.K_LEFT:
                        app.load_scenario(app.scenario_index - 1)
                        pause_timer = 0
                        acc_ms = 0.0
                    elif pygame.K_1 <= event.key <= pygame.K_9:
                        i = event.key - pygame.K_1
                        if i < len(app.scenarios):
                            app.load_scenario(i)
                            pause_timer = 0
                            acc_ms = 0.0
                elif app.mode == App.MODE_PVA:
                    if event.key == pygame.K_r:
                        sounds.stop_all(immediate=True)
                        app.restart_pva_round()
                        acc_ms = 0.0

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if app.mode == App.MODE_MENU and event.button == 1:
                    if menu_buttons[0].is_clicked(mouse_pos):
                        sounds.stop_all(immediate=True)
                        app.start_automatic_mode()
                        playing = True
                        pause_timer = 0
                        acc_ms = 0.0
                    elif menu_buttons[1].is_clicked(mouse_pos):
                        sounds.stop_all(immediate=True)
                        app.start_pva_mode()
                        pause_timer = 0
                        acc_ms = 0.0

                elif app.mode == App.MODE_AUTOMATIC and event.button == 1:
                    if auto_mute_btn.is_clicked(mouse_pos):
                        sounds.set_muted(not sounds.is_muted())
                    elif auto_ctrl_btns[0].is_clicked(mouse_pos):
                        playing = True
                    elif auto_ctrl_btns[1].is_clicked(mouse_pos):
                        playing = False
                    elif auto_ctrl_btns[2].is_clicked(mouse_pos):
                        app.load_scenario(app.scenario_index - 1)
                        pause_timer = 0
                        acc_ms = 0.0
                    elif auto_ctrl_btns[3].is_clicked(mouse_pos):
                        app.advance_to_next_scenario()
                        pause_timer = 0
                        acc_ms = 0.0
                    elif auto_menu_btn.is_clicked(mouse_pos):
                        sounds.stop_all(immediate=True)
                        app.back_to_menu()
                        pause_timer = 0
                        acc_ms = 0.0
                    else:
                        for i, sb in enumerate(auto_scen_btns):
                            if sb.is_clicked(mouse_pos) and i < len(app.scenarios):
                                app.load_scenario(i)
                                pause_timer = 0
                                acc_ms = 0.0
                                break

                elif app.mode == App.MODE_PVA:
                    if event.button == 1:
                        if pva_mute_btn.is_clicked(mouse_pos):
                            sounds.set_muted(not sounds.is_muted())
                        elif pva_restart_btn.is_clicked(mouse_pos):
                            sounds.stop_all(immediate=True)
                            app.restart_pva_round()
                            acc_ms = 0.0
                        elif pva_menu_btn.is_clicked(mouse_pos):
                            sounds.stop_all(immediate=True)
                            app.back_to_menu()
                            acc_ms = 0.0
                        else:
                            app.pva_left_click()
                    elif event.button == 3:
                        app.pva_right_click()

        if app.mode == App.MODE_AUTOMATIC:
            if playing:
                if pause_timer > 0:
                    pause_timer -= dt_ms
                    if pause_timer <= 0:
                        pause_timer = 0
                        app.advance_to_next_scenario()
                        acc_ms = 0.0
                elif app.scenario_finished():
                    pause_timer = PAUSE_DURATION_MS
                    acc_ms = 0.0
                else:
                    acc_ms += dt_ms
                    steps_this_frame = 0
                    while acc_ms >= tick_ms and steps_this_frame < MAX_SIM_STEPS_PER_FRAME:
                        acc_ms -= tick_ms
                        app.run_step()
                        steps_this_frame += 1
                        if app.scenario_finished():
                            pause_timer = PAUSE_DURATION_MS
                            acc_ms = 0.0
                            break
                    if acc_ms >= tick_ms:
                        acc_ms = 0.0
        elif app.mode == App.MODE_PVA:
            if app.pva_phase == App.PVA_RUNNING and not app.scenario_finished():
                acc_ms += dt_ms
                steps_this_frame = 0
                while acc_ms >= tick_ms and steps_this_frame < MAX_SIM_STEPS_PER_FRAME:
                    acc_ms -= tick_ms
                    app.run_step()
                    steps_this_frame += 1
                    if app.scenario_finished():
                        acc_ms = 0.0
                        break
                if acc_ms >= tick_ms:
                    acc_ms = 0.0

        # Cosmetic-only audio update (must not affect gameplay).
        sounds.update(app, tick_ms=tick_ms)

        screen.fill(COL_BG)
        draw_grid(screen, app.state.grid)

        if app.mode == App.MODE_PVA and app.pva_phase == App.PVA_RUNNING:
            fps = getattr(app, "ai_future_paths", [])
            draw_future_paths(screen, fps, app.state.grid)

        if app.mode != App.MODE_MENU:
            if app.mode == App.MODE_PVA:
                preview = app.pva_preview
                if preview.exit_tiles:
                    draw_all_edge_outlines(screen, app.state.grid, set(preview.exit_tiles))
                if app.pva_phase == App.PVA_TILE_SELECT and app.pva_hover_tile is not None:
                    draw_tile_border(screen, app.pva_hover_tile, COL_TILE_HOVER, 3)
                if preview.tile is not None:
                    draw_tile_border(screen, preview.tile, _dir_color(preview.direction), 4)
                if app.pva_phase in (App.PVA_ANGLE_SELECT, App.PVA_CONFIRM):
                    draw_direction_fan(screen, app.pva_locked_tile, app.pva_valid_launch_dirs, app.pva_hover_direction)
                    draw_preview_path(
                        screen,
                        app.state.grid,
                        app.pva_locked_tile,
                        preview.direction,
                        _dir_color(preview.direction),
                    )

            truck = app.state.sam_truck
            draw_truck(
                screen,
                truck.position,
                truck.direction,
                has_fired=truck.has_fired,
                pictures=pictures,
            )

            ac_s = app.state
            if app.mode == App.MODE_AUTOMATIC:
                draw_aircraft(
                    screen,
                    app.state.aircraft.position,
                    app.inferred_direction,
                    app.inferred_direction is not None,
                    pictures=pictures,
                    crashed=bool(ac_s.intercepted),
                )
            elif app.has_aircraft:
                draw_aircraft(
                    screen,
                    app.state.aircraft.position,
                    app.state.aircraft.direction,
                    True,
                    pictures=pictures,
                    crashed=bool(ac_s.intercepted),
                )

            for m in app.state.missiles:
                if m.active:
                    draw_missile(screen, m.position, m.direction, pictures=pictures)

            if (
                app.mode == App.MODE_PVA
                and app.pva_phase == App.PVA_RUNNING
                and not app.pva_turn_used
                and app.has_aircraft
            ):
                draw_turn_wheel(
                    screen,
                    app.state.aircraft.position,
                    app.pva_player_turn_dirs,
                    app.pva_turn_hover_direction,
                )

            if app.mode == App.MODE_PVA:
                draw_pva_result_overlay(screen, app, pictures, app.state.grid)

        if app.mode == App.MODE_MENU:
            draw_menu(screen, font, font_small, screen_w, screen_h, menu_buttons)
        elif app.mode == App.MODE_AUTOMATIC:
            draw_auto_panel(
                screen,
                app,
                font,
                font_small,
                info_y,
                ctrl_y,
                screen_w,
                auto_ctrl_btns,
                auto_scen_btns,
                auto_menu_btn,
                auto_mute_btn,
                sounds.is_muted(),
                playing,
                pause_timer,
                render_fps,
            )
        else:
            draw_pva_panel(
                screen,
                app,
                font,
                font_small,
                info_y,
                ctrl_y,
                screen_w,
                pva_restart_btn,
                pva_menu_btn,
                pva_mute_btn,
                sounds.is_muted(),
                render_fps,
            )

        pygame.display.flip()

    pygame.quit()


def main() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = logs_dir / f"run_{stamp}.txt"
    log_file = open(log_path, "w", encoding="utf-8")
    stdout_orig = sys.stdout
    stderr_orig = sys.stderr
    sys.stdout = Tee(stdout_orig, log_file)
    sys.stderr = Tee(stderr_orig, log_file)
    try:
        _run_app()
    finally:
        sys.stdout = stdout_orig
        sys.stderr = stderr_orig
        log_file.close()


if __name__ == "__main__":
    main()