import math
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pygame

from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from core.vector import Vector2D
from game.app import App, PVAReplayFrame
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
REPLAY_MAP_PAD_L = 6
REPLAY_GAP = 8
REPLAY_SIDE_PANEL_W = 348
REPLAY_TITLE_H = 24
REPLAY_AUTO_MS = 420
RENDER_FPS = 60
MAX_SIM_STEPS_PER_FRAME = 1
PAUSE_DURATION_MS = 1800
MAJOR_GRID = 4

COL_BG = (14, 22, 14)
COL_GRID_MINOR = (38, 48, 38)
COL_GRID_MAJOR = (58, 72, 54)
COL_AIRCRAFT = (72, 210, 120)
COL_AIRCRAFT_UNK = (110, 188, 130)
COL_TRUCK = (120, 150, 168)
COL_MISSILE = (235, 120, 68)
COL_TEXT = (232, 228, 210)
COL_DIM = (154, 148, 128)
COL_PERF_OK = (130, 195, 120)
COL_PERF_SLOW = (255, 170, 72)
COL_HIT = (255, 148, 64)
COL_FAIL = (220, 86, 64)
COL_SUCCESS = (120, 220, 110)
COL_PANEL_BG = (32, 40, 30)
COL_PANEL_SEP = (88, 98, 72)
COL_BTN = (44, 68, 44)
COL_BTN_HOVER = (62, 88, 52)
COL_BTN_ACTIVE = (255, 150, 48)
COL_BTN_TEXT = (245, 240, 220)
COL_EDGE_VALID = (110, 240, 120)
COL_EDGE_INVALID = (220, 100, 64)
COL_BORDER_TILE_HOVER = (255, 215, 72)
COL_TILE_HOVER_GLOW = (255, 200, 90, 72)
COL_TILE_LOCK = (230, 185, 92)
COL_FAN_RAY_MUTED = (165, 145, 78, 120)
COL_FAN_RAY_MUTED_OUTLINE = (35, 38, 28, 55)
COL_FAN_RAY_HI = (255, 215, 95, 200)
COL_FAN_RAY_HI_OUTLINE = (40, 36, 22, 140)
COL_PREVIEW_RAY_CORE = (245, 200, 110)
COL_PREVIEW_RAY_GLOW = (255, 210, 140)
COL_TURN_MUTED = (140, 125, 72, 90)
COL_TURN_MUTED_HI = (210, 175, 78, 185)
COL_TAC_LABEL = (200, 190, 150)
COL_TAC_TERM = (80, 255, 140)
COL_TAC_TERM_DIM = (140, 220, 100)


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

    def draw(self, screen, font, active: bool = False, *, disabled: bool = False) -> None:
        if disabled:
            col = (40, 48, 36)
            tx = COL_DIM
        elif active:
            col = COL_BTN_ACTIVE
            tx = COL_BTN_TEXT
        elif self.hovered:
            col = COL_BTN_HOVER
            tx = COL_BTN_TEXT
        else:
            col = COL_BTN
            tx = COL_BTN_TEXT
        pygame.draw.rect(screen, col, self.rect)
        pygame.draw.rect(screen, COL_PANEL_SEP, self.rect, 1)
        text = self.label + (f" [{self.key_hint}]" if self.key_hint else "")
        surf = font.render(text, True, tx)
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


def draw_replay_result_overlay(
    screen: pygame.Surface, frame: PVAReplayFrame, pictures: PictureManager | None, grid
) -> None:
    """Win/lose art from recorded result text (replay-only; uses snapshot fields)."""
    if pictures is None:
        return
    rt = str(getattr(frame, "pva_result_text", "") or "")
    img = None
    if getattr(frame, "escaped", False) or ("escaped" in rt.lower()) or ("ESCAPED" in rt.upper()):
        img = pictures.win_img
    elif (
        getattr(frame, "intercepted", False)
        or getattr(frame, "failed", False)
        or ("INTERCEPTED" in rt.upper())
        or ("ILLEGAL" in rt.upper())
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
        pygame.draw.polygon(screen, (150, 165, 178), poly, 2)

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


def draw_direction_fan(screen, origin_tile: tuple[int, int], directions: list[DirectionVector], highlight: DirectionVector | None) -> None:
    if origin_tile is None:
        return
    ox = origin_tile[0] * CELL_SIZE + CELL_SIZE / 2
    oy = origin_tile[1] * CELL_SIZE + CELL_SIZE / 2
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    for d in directions:
        end = (ox + d.x * CELL_SIZE * 3.2, oy + d.y * CELL_SIZE * 3.2)
        is_hi = highlight is not None and d.index == highlight.index
        if is_hi:
            pygame.draw.line(overlay, COL_FAN_RAY_HI_OUTLINE, (ox, oy), end, 6)
            pygame.draw.line(overlay, COL_FAN_RAY_HI, (ox, oy), end, 4)
            pygame.draw.line(overlay, (255, 230, 150, 150), (ox, oy), end, 2)
        else:
            pygame.draw.line(overlay, COL_FAN_RAY_MUTED_OUTLINE, (ox, oy), end, 4)
            pygame.draw.line(overlay, COL_FAN_RAY_MUTED, (ox, oy), end, 2)
    screen.blit(overlay, (0, 0))


def draw_preview_path(screen, grid, origin_tile: tuple[int, int], direction: DirectionVector | None, _color) -> None:
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
    core = COL_PREVIEW_RAY_CORE
    glow = COL_PREVIEW_RAY_GLOW
    pygame.draw.line(overlay, (32, 28, 18, 110), p1, p2, 5)
    pygame.draw.line(overlay, (*core, 135), p1, p2, 3)
    pygame.draw.line(overlay, (*glow, 72), p1, p2, 2)
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
        (95, 118, 92, 42),
        (110, 105, 82, 38),
        (88, 112, 96, 40),
        (105, 102, 85, 38),
        (92, 108, 94, 39),
    )
    main_w, outline_w = 2, 3

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
            pygame.draw.lines(overlay, (22, 26, 18, 72), False, pts, outline_w)
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
                    pygame.draw.line(overlay, (22, 24, 18, 42), (xb, yb), (x2, y2), 2)
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
        is_hi = highlight is not None and d.index == highlight.index
        col = COL_TURN_MUTED_HI if is_hi else COL_TURN_MUTED
        width = 4 if is_hi else 2
        pygame.draw.line(overlay, col, (cx, cy), end, width)
    screen.blit(overlay, (0, 0))


def _confidence_voice(label: str) -> str:
    u = (label or "").strip().upper()
    if any(x in u for x in ("HIGH", "STRONG", "GOOD", "SOLID")):
        return "HIGH"
    if any(x in u for x in ("LOW", "WEAK", "POOR", "BAD", "THIN")):
        return "LOW"
    if any(x in u for x in ("MID", "MED", "MOD")):
        return "MEDIUM"
    return "MODERATE"


def _replay_group_key(fr: PVAReplayFrame) -> tuple[str, str, str]:
    """Group ticks with identical policy + engine action + plan type."""
    return (
        (fr.action_policy or "").strip(),
        (fr.last_action or "").strip(),
        (fr.last_plan_type or "").strip(),
    )


def _replay_group_range(frames: list[PVAReplayFrame], idx: int) -> tuple[int, int]:
    if not frames or idx < 0 or idx >= len(frames):
        return (0, 0)
    k = _replay_group_key(frames[idx])
    a = idx
    while a > 0 and _replay_group_key(frames[a - 1]) == k:
        a -= 1
    b = idx
    while b + 1 < len(frames) and _replay_group_key(frames[b + 1]) == k:
        b += 1
    return (a, b)


def _replay_options_lines() -> list[str]:
    return [
        "I considered firing now, waiting a tick, repositioning the SAM, or holding fire entirely.",
        "(Branch scores are not stored per tick in replay data.)",
    ]


def _replay_text_column_width(font, width_px: int) -> int:
    cw = max(1, int(font.size("M")[0]))
    return max(12, width_px // cw)


def _count_wrapped_lines(text: str, col_w: int) -> int:
    if not (text or "").strip():
        return 0
    n = 0
    for para in text.split("\n"):
        n += len(textwrap.wrap(para, width=col_w) or [""])
    return n


def _sentences_join(sents: list[str]) -> str:
    out: list[str] = []
    for raw in sents:
        s = (raw or "").strip()
        if not s:
            continue
        if not s.endswith((".", "!", "?")):
            s += "."
        out.append(s)
    return " ".join(out)


def _fit_commander_body(sents: list[str], font, width_px: int, max_lines: int) -> str:
    col_w = _replay_text_column_width(font, width_px)
    parts = [s for s in sents if (s or "").strip()]
    while len(parts) > 1:
        blob = _sentences_join(parts)
        if _count_wrapped_lines(blob, col_w) <= max_lines:
            return blob
        parts = parts[:-1]
    blob = _sentences_join(parts) if parts else ""
    while blob and _count_wrapped_lines(blob, col_w) > max_lines:
        blob = blob[: max(28, len(blob) - 20)].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return blob


def _replay_reason_is_vague(raw: str) -> bool:
    u = raw.lower()
    needles = ("planner told", "according to planner", "executing planner plan", "planner plan", "told me to", "following planner")
    return any(n in u for n in needles)


def _replay_policy_reason_display(fr: PVAReplayFrame) -> str:
    p = (fr.action_policy or "").strip().upper()
    raw = (fr.action_policy_reason or "").strip()

    concrete = {
        "VERIFIED_PLAN": "This plan covered most predicted futures.",
        "WEAK_STAGING": "No clean shot was available, so I repositioned.",
        "WEAK_FIRE_HELD": "The shot covered too few futures, so I held fire.",
        "NO_PLAN_HOLD": "No verified intercept was available.",
        "HEAVY_REUSE": "I reused one staging step after an expensive planning tick.",
        "FIRED": "The shot was good enough to commit.",
        "INTERCEPTED": "The missile reached the aircraft.",
        "ESCAPED": "The aircraft escaped through the valid corridor.",
        "OBSERVING": "I tracked outbound ordnance while the picture refreshed each tick.",
        "WAITING_RADAR": "The track was still soft; I waited instead of forcing a launch.",
        "ILLEGAL_EXIT": "The pilot broke the corridor rule.",
    }

    if p == "HEAVY_REUSE":
        return concrete["HEAVY_REUSE"]
    if fr.risky_unvalidated and p not in ("INTERCEPTED", "ESCAPED", "ILLEGAL_EXIT"):
        return "The plan was possible, but validation did not fully confirm it."
    if p in concrete:
        return concrete[p]
    if raw and (not _replay_reason_is_vague(raw)):
        return raw
    return "I mirrored the safest verified posture encoded in this frame."


def _replay_volley_count(frames: list[PVAReplayFrame]) -> int:
    n = 0
    prev = False
    for fr in frames:
        cur = bool(fr.truck_has_fired)
        if cur and (not prev):
            n += 1
        prev = cur
    return n


def _replay_round_summary_lines(frames: list[PVAReplayFrame]) -> list[str]:
    if not frames:
        return ["Summary: —"]
    last = frames[-1]
    rt = (last.pva_result_text or "").strip().upper()
    volleys = _replay_volley_count(frames)
    conf = _confidence_voice(last.ai_confidence_label)

    if "INTERCEPTED" in rt or last.intercepted:
        if conf == "HIGH":
            return ["Summary: SAM intercepted after staging and firing with strong confidence."]
        if conf == "LOW":
            return ["Summary: SAM intercepted despite thin coverage."]
        return ["Summary: SAM intercepted during an engaged window."]

    if "ESCAPED" in rt or last.escaped:
        if volleys == 0:
            return ["Summary: Player escaped; SAM withheld fire."]
        if volleys == 1:
            return [
                "Summary: Player escaped.",
                "SAM fired once, but the futures bundle missed the eventual path.",
            ]
        return ["Summary: Player escaped.", f"SAM fired {volleys} volleys without sealing the corridor."]

    if "ILLEGAL" in rt or "ILLEGAL_EXIT" in rt or (
        getattr(last, "failed", False) and (not getattr(last, "intercepted", False))
    ):
        return ["Summary: Illegal exit — duel lost on geometry."]

    if volleys == 0:
        return ["Summary: SAM held fire because no verified plan was available."]
    remn = (last.pva_result_text or "Round unresolved in replay snapshot.").strip()
    return ["Summary: " + remn[:88] + ("…" if len(remn) > 88 else "")]


def _build_commander_story_sentences(rep: PVAReplayFrame, frames: list[PVAReplayFrame], a: int, b: int) -> list[str]:
    """At most three short sentences derived from snapshots only."""
    p = (rep.action_policy or "").strip().upper()
    rt = (rep.pva_result_text or "").strip()
    la = (rep.last_action or "").lower()
    aps = (rep.ai_plan_summary or "").lower()
    fh = int(rep.futures_hit)
    ft = max(1, int(rep.futures_total))
    conf = _confidence_voice(rep.ai_confidence_label).lower()
    t0, t1 = frames[a].tick, frames[b].tick
    span = f"Ticks {t0}–{t1}" if b > a else f"Tick {t0}"

    rtu = rt.upper()
    if "INTERCEPTED" in rtu or p == "INTERCEPTED":
        return [span, "The missile reached the aircraft.", "Intercept closed the sortie."]

    if "ESCAPED" in rtu or p == "ESCAPED" or getattr(rep, "escaped", False):
        s3 = (
            "Validation lagged earlier, so the posture never felt ironclad."
            if rep.risky_unvalidated
            else f"Outlook stayed {conf} with futures at {fh}/{ft}."
        )
        return [span, "The aircraft escaped through the valid corridor.", s3]

    if "ILLEGAL" in rtu or p == "ILLEGAL_EXIT" or (
        getattr(rep, "failed", False) and not getattr(rep, "intercepted", False)
    ):
        return [span, "The fighter left through a red border tile.", "That violates the duel contract."]

    if p == "FIRED" or ">>> fired" in la or " fired" in la:
        return [span, "Futures coverage justified committing the missile.", f"Confidence read {conf} before launch."]

    if p == "WEAK_FIRE_HELD":
        return [span, "The speculative shot skimmed too few futures.", "I held fire to avoid wasting the round."]

    if p == "NO_PLAN_HOLD":
        return [span, "No intercept plan crossed the verified bar.", f"Confidence remained {conf} at {fh}/{ft}."]

    if p == "WEAK_STAGING":
        return [span, "No clean shot existed yet, so I nudged the launcher.", f"Coverage hovered near {fh}/{ft}."]

    if p == "HEAVY_REUSE":
        return [span, "Planning tick was expensive.", "I reused one staging stride to maintain tempo."]

    if p == "VERIFIED_PLAN":
        waitish = any(k in la for k in ("wait", "dwell", "idle")) or ("wait" in aps)
        moveish = any(k in la for k in ("move", "stage", "truck", "reposition", "advance"))
        mid = "I executed validated planner steps without improvisation."
        if waitish and not moveish:
            mid = "I waited until the funnel improved instead of twitch-firing."
        elif moveish or "move" in aps or "stage" in aps:
            mid = "I advanced the launcher to widen the firing solution."
        return [span, "Verified futures stayed aligned with SAM posture.", mid]

    if p == "OBSERVING":
        return [span, "Inbound ordnance still dominated the radar return.", "I refreshed geometry before reacting."]

    if p == "WAITING_RADAR":
        return [span, "Sensor returns were jittery.", f"I refused to spoof a launch cue; confidence stayed {conf}."]

    summ = (rep.ai_plan_summary or "").strip()
    if summ:
        short = summ if len(summ) <= 110 else summ[:109] + "…"
        return [span, short]
    return [span, "I mirrored the hardened posture baked into this snapshot."]


def _replay_commander_story_sentences_grouped(frames: list[PVAReplayFrame], idx: int) -> list[str]:
    if not frames or idx < 0 or idx >= len(frames):
        return []
    ga, gb = _replay_group_range(frames, idx)
    rep = frames[ga]
    return _build_commander_story_sentences(rep, frames, ga, gb)[:3]


def _replay_status_section_lines(
    view_fr: PVAReplayFrame, group_rep: PVAReplayFrame, tick_span_lo: int, tick_span_hi: int
) -> list[str]:
    lines = [f"View tick         T+{view_fr.tick:04d}  (cap {C.MAX_STEPS})"]
    if tick_span_hi > tick_span_lo:
        lines.append(f"Same decision      T+{tick_span_lo:04d} – T+{tick_span_hi:04d}")
    rt = (view_fr.pva_result_text or "").strip()
    if rt:
        lines.append(f"Result             {rt}")
    lines.append(f"Confidence         {group_rep.ai_confidence_label or '—'}")
    cov = (
        group_rep.coverage
        if (group_rep.coverage and group_rep.coverage != "—")
        else f"{group_rep.futures_hit}/{group_rep.futures_total}"
    )
    lines.append(f"Coverage           {cov}")
    lines.append(f"Plan type          {group_rep.last_plan_type or '—'}")
    return lines


def _replay_aircraft_heading_word(fr: PVAReplayFrame) -> str:
    di = int(fr.aircraft_dir_index) % len(DIRECTIONS)
    ad = DIRECTIONS[di]
    ang = math.degrees(math.atan2(ad.y, ad.x)) % 360.0
    labels = [
        "east",
        "northeast",
        "north",
        "northwest",
        "west",
        "southwest",
        "south",
        "southeast",
    ]
    return labels[int((ang + 22.5) / 45.0) % 8]


def _replay_percepts_story_lines(fr: PVAReplayFrame) -> list[str]:
    cardinal = _replay_aircraft_heading_word(fr)
    turn_txt = "Turn: available" if not getattr(fr, "pva_turn_used", True) else "Turn: used"
    nmiss = sum(1 for m in fr.missiles if m[3])
    if nmiss:
        mtxt = "Missile: in flight"
    else:
        mtxt = "Missile: not launched"
    esc = len(fr.locked_exit_tiles)
    npath = sum(1 for p in fr.ai_future_paths if p)
    return [
        f"Aircraft: {cardinal}",
        turn_txt,
        mtxt,
        f"Futures: {npath}",
        f"Escape tiles: {esc}",
    ]


def _replay_decision_label(fr: PVAReplayFrame) -> str:
    p = (fr.action_policy or "").upper()
    rt = (fr.pva_result_text or "").upper()
    la = (fr.last_action or "").lower()
    aps = (fr.ai_plan_summary or "").lower()
    if "INTERCEPTED" in rt or p == "INTERCEPTED":
        return "OUTCOME — INTERCEPT"
    if "ESCAPED" in rt or p == "ESCAPED" or getattr(fr, "escaped", False):
        return "OUTCOME — ESCAPE (legal corridor)"
    if "ILLEGAL" in rt or p == "ILLEGAL_EXIT" or (
        getattr(fr, "failed", False) and not getattr(fr, "intercepted", False)
    ):
        return "OUTCOME — ILLEGAL EXIT"
    if p == "FIRED":
        return "FIRE — COMMIT MISSILE"
    if p == "NO_PLAN_HOLD":
        return "HOLD — NO VERIFIED INTERCEPT"
    if p == "WEAK_FIRE_HELD":
        return "HOLD FIRE — SPECULATIVE SHOT"
    if p == "WEAK_STAGING":
        return "MOVE — WEAK STAGING"
    if p == "HEAVY_REUSE":
        return "REUSE — STAGING (PERF BUDGET)"
    if p == "OBSERVING":
        return "OBSERVE — MISSILE IN FLIGHT"
    if p == "WAITING_RADAR":
        return "WAIT — SENSOR LOCK"
    if p == "VERIFIED_PLAN":
        if any(k in la for k in ("wait", "dwell", "idle")) or "wait" in aps:
            return "WAIT — VERIFIED PLAN"
        if any(k in la for k in ("move", "stage", "truck", "reposition")) or "move" in aps:
            return "MOVE — VERIFIED PLAN"
        return "EXECUTE — VERIFIED PLAN"
    return (fr.last_plan_type or "CONTINUE").upper()[:32]


def _draw_tac_section_title(screen: pygame.Surface, x: int, y: int, label: str, font) -> int:
    t = font.render(label.upper(), True, COL_TAC_LABEL)
    screen.blit(t, (x, y))
    return y + t.get_height() + 2


def _draw_tac_multiline(
    screen: pygame.Surface,
    x: int,
    y: int,
    lines: list[str],
    font,
    color,
    max_y: int,
    line_gap: int = 2,
    *,
    max_lines: int | None = None,
    ellipsis: bool = True,
) -> int:
    yy = y
    used = 0
    for i, ln in enumerate(lines):
        if max_lines is not None and used >= max_lines:
            if ellipsis and yy + font.get_height() <= max_y:
                screen.blit(font.render("...", True, color), (x, yy))
            break
        if yy >= max_y:
            break
        surf = font.render(ln, True, color)
        screen.blit(surf, (x, yy))
        yy += surf.get_height() + line_gap
        used += 1
    return yy


def _draw_tac_title_strip_menu(screen: pygame.Surface, span_w: int, font_small) -> int:
    h = 34
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, 0, span_w, h))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, h - 1), (span_w, h - 1), 1)
    t = font_small.render("AVOIDSAM // ARCADE TACTICAL ARENA", True, COL_TAC_TERM_DIM)
    screen.blit(t, (14, (h - t.get_height()) // 2))
    return h


def _draw_tac_title_strip_replay(screen: pygame.Surface, span_w: int, font_small) -> None:
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, 0, span_w, REPLAY_TITLE_H))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, REPLAY_TITLE_H - 1), (span_w, REPLAY_TITLE_H - 1), 1)
    t1 = font_small.render("BATTLE ANALYSIS", True, COL_TAC_TERM_DIM)
    t2 = font_small.render("TACTICAL REPLAY", True, COL_DIM)
    screen.blit(t1, (10, (REPLAY_TITLE_H - t1.get_height()) // 2))
    tr2 = t2.get_rect()
    tr2.right = span_w - 10
    tr2.centery = REPLAY_TITLE_H // 2
    screen.blit(t2, tr2)


def draw_tac_replay_side_panel(
    screen: pygame.Surface,
    rect: pygame.Rect,
    frames: list[PVAReplayFrame],
    frame_index: int,
    font_hdr: pygame.font.Font,
    font_norm: pygame.font.Font,
    font_cmd: pygame.font.Font,
) -> None:
    pygame.draw.rect(screen, COL_PANEL_BG, rect)
    pygame.draw.rect(screen, COL_PANEL_SEP, rect, 1)
    pad = 8
    x0 = rect.left + pad
    y = rect.top + pad
    max_y = rect.bottom - pad
    rw = max(24, rect.width - 2 * pad - 4)
    col_w = _replay_text_column_width(font_norm, rw)

    ga, gb = _replay_group_range(frames, frame_index)
    group_rep = frames[ga]
    view_fr = frames[frame_index]

    panel_inner = pygame.Rect(rect.left + 1, rect.top + 1, rect.width - 2, rect.height - 2)

    hdr = font_hdr.render("COMMANDER", True, COL_TAC_TERM_DIM)
    screen.blit(hdr, (x0, y))
    y += hdr.get_height() + 2
    sub = font_norm.render("analysis feed", True, COL_DIM)
    screen.blit(sub, (x0, y))
    y += sub.get_height() + 10

    y = _draw_tac_section_title(screen, x0, y, "STATUS", font_hdr)
    y = _draw_tac_multiline(
        screen,
        x0 + 2,
        y,
        _replay_status_section_lines(view_fr, group_rep, frames[ga].tick, frames[gb].tick),
        font_norm,
        COL_TEXT,
        max_y,
        max_lines=6,
    )
    y += 10

    if y >= max_y:
        return
    y = _draw_tac_section_title(screen, x0, y, "PERCEPTS", font_hdr)
    y = _draw_tac_multiline(
        screen,
        x0 + 2,
        y,
        _replay_percepts_story_lines(view_fr),
        font_norm,
        COL_TEXT,
        max_y,
        max_lines=6,
    )
    y += 10

    if y >= max_y:
        return
    y = _draw_tac_section_title(screen, x0, y, "ACTION", font_hdr)
    y = _draw_tac_multiline(
        screen,
        x0 + 2,
        y,
        [_replay_decision_label(group_rep), f"Engine: {(group_rep.last_action or '—')[:44]}"],
        font_norm,
        COL_TAC_TERM_DIM,
        max_y,
        max_lines=2,
    )
    y += 10

    if y >= max_y:
        return
    y = _draw_tac_section_title(screen, x0, y, "REASON", font_hdr)
    reason = _replay_policy_reason_display(group_rep)
    wrapped: list[str] = []
    for para in reason.split("\n"):
        wrapped.extend(textwrap.wrap(para, width=col_w) or [""])
    y = _draw_tac_multiline(screen, x0 + 2, y, wrapped, font_norm, COL_DIM, max_y, max_lines=3)
    y += 12

    if y >= max_y:
        return
    y = _draw_tac_section_title(screen, x0, y, "COMMANDER LOG", font_hdr)
    cmd_sents = _replay_commander_story_sentences_grouped(frames, frame_index)
    line_h = font_cmd.get_height() + 2
    max_cmd_lines = max(1, (max_y - y - 2) // line_h)
    log_full = _fit_commander_body(cmd_sents, font_cmd, rw, max_cmd_lines)
    cmd_rect = pygame.Rect(x0 + 2, y, rw, max(0, max_y - y)).clip(panel_inner)
    _draw_wrapped_panel_text(screen, cmd_rect, log_full, font_cmd, COL_TAC_TERM)


def draw_replay_board(surface: pygame.Surface, frame: PVAReplayFrame, pictures: "PictureManager", grid) -> None:
    surface.fill(COL_BG)
    draw_grid(surface, grid)
    vpaths: list[list[Vector2D]] = []
    for seg in frame.ai_future_paths:
        if not seg:
            continue
        vpaths.append([Vector2D(px, py) for px, py in seg])
    if vpaths:
        draw_future_paths(surface, vpaths, grid)

    exits = set(tuple(t) for t in frame.locked_exit_tiles)
    if exits:
        draw_all_edge_outlines(surface, grid, exits)

    tdir = DIRECTIONS[int(frame.truck_dir_index) % len(DIRECTIONS)]
    truck_pos = Vector2D(float(frame.truck_x), float(frame.truck_y))
    draw_truck(surface, truck_pos, tdir, has_fired=bool(frame.truck_has_fired), pictures=pictures)

    adir = DIRECTIONS[int(frame.aircraft_dir_index) % len(DIRECTIONS)]
    ac_pos = Vector2D(float(frame.aircraft_x), float(frame.aircraft_y))
    draw_aircraft(
        surface,
        ac_pos,
        adir,
        True,
        pictures=pictures,
        crashed=bool(frame.intercepted),
    )

    for mx, my, md_i, mactive in frame.missiles:
        if not mactive:
            continue
        mdir = DIRECTIONS[int(md_i) % len(DIRECTIONS)]
        draw_missile(surface, Vector2D(float(mx), float(my)), mdir, pictures=pictures)


def _draw_wrapped_panel_text(
    screen: pygame.Surface,
    clip_rect: pygame.Rect,
    text: str,
    font,
    color,
) -> None:
    prev = screen.get_clip()
    try:
        screen.set_clip(clip_rect)
        col_w = _replay_text_column_width(font, clip_rect.width)
        yy = clip_rect.top
        for para in (text or "").split("\n"):
            if not para.strip():
                yy += font.get_height() // 2
                continue
            for ln in textwrap.wrap(para, width=col_w) or [""]:
                if yy + font.get_height() > clip_rect.bottom:
                    screen.blit(font.render("…", True, color), (clip_rect.left, clip_rect.bottom - font.get_height()))
                    return
                screen.blit(font.render(ln, True, color), (clip_rect.left, yy))
                yy += font.get_height() + 2
    finally:
        screen.set_clip(prev)


def draw_menu(screen, font, font_small, screen_w, screen_h, buttons: list[Button]) -> None:
    hb = _draw_tac_title_strip_menu(screen, screen_w, font_small)
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, hb, screen_w, screen_h - hb))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, hb), (screen_w, hb), 1)
    title = font.render("COMMAND MENU", True, COL_TAC_TERM_DIM)
    subtitle = font_small.render(
        "CLASSIFIED OPS — PILOT VS SAM BATTERY | MANUAL ENGAGEMENT TERMINAL", True, COL_DIM
    )
    cx = screen_w // 2
    screen.blit(title, title.get_rect(center=(cx, hb + 96)))
    screen.blit(subtitle, subtitle.get_rect(center=(cx, hb + 134)))
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
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 2)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)
    bar = font_small.render("AUTO-RUN // SCENARIO TELEMETRY", True, COL_TAC_LABEL)
    screen.blit(bar, (10, info_y + 2))

    if s.intercepted:
        status, sc = "INTERCEPTED", COL_HIT
    elif getattr(s, "failed", False):
        status, sc = "FAILED", COL_FAIL
    elif s.tick >= C.MAX_STEPS:
        status, sc = "TIMEOUT", COL_DIM
    elif not playing:
        status, sc = "PAUSED", (190, 150, 80)
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

    screen.blit(font.render(line1, True, sc), (10, info_y + 18))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 38))
    screen.blit(font_small.render(line3, True, COL_DIM), (10, info_y + 56))
    screen.blit(font_small.render(line4, True, perf_col), (10, info_y + 74))

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
    ctrl_row: tuple[Button, ...],
    mute_btn: Button | None,
    muted: bool,
    render_fps,
    *,
    replay_snapshot_count: int = 0,
    replay_last_game_disabled: bool = False,
) -> None:
    s = app.state
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 2)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)
    bar = font_small.render("PVA ENGAGEMENT // SAM COMMAND CHANNEL", True, COL_TAC_LABEL)
    screen.blit(bar, (10, info_y + 2))

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
    line1 = f">> T+{s.tick:04d}/{C.MAX_STEPS}    {status}    PLAYER_VS_SAM"
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
    elif app.pva_phase == App.PVA_END:
        line3 = f"Result: {app.pva_result_text or '—'}  |  Saved replay frames: {replay_snapshot_count}"
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
    line4_perf = perf_bits
    line4_ai = ai_raw[:avail].rstrip() if ai_raw else ""
    y_plan = info_y + 74

    screen.blit(font.render(line1, True, sc), (10, info_y + 18))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 38))
    screen.blit(font_small.render(line3, True, COL_DIM), (10, info_y + 56))
    if line4_ai:
        s_ai = font_small.render(line4_ai + "   ", True, COL_TAC_TERM)
        screen.blit(s_ai, (10, y_plan))
        s_pf = font_small.render(line4_perf, True, COL_DIM)
        screen.blit(s_pf, (10 + s_ai.get_width(), y_plan))
    else:
        screen.blit(font_small.render(line4_perf, True, COL_PERF_OK), (10, y_plan))

    for btn in ctrl_row:
        dis = replay_last_game_disabled and btn.label == "Replay Last Game"
        btn.draw(screen, font_small, disabled=dis)
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
        Button(pygame.Rect(x, y, bw, bh), "Play"),
        Button(pygame.Rect(x, y + 60, bw, bh), "How To Play"),
    ]


def build_howto_back_button(screen_w: int, screen_h: int) -> Button:
    return Button(pygame.Rect(screen_w // 2 - 50, screen_h - 52, 100, 32), "Back")


def load_howto_images() -> list[pygame.Surface]:
    out: list[pygame.Surface] = []
    base = Path("assets") / "pictures"
    if not base.is_dir():
        return out
    for name in ("how_to_play_1.png", "how_to_play_2.png", "how_to_play_3.png"):
        p = base / name
        try:
            if p.exists():
                out.append(pygame.image.load(str(p)).convert_alpha())
        except Exception:
            continue
    if out:
        return out
    for p in sorted(base.glob("how_to_play_*.png")):
        try:
            out.append(pygame.image.load(str(p)).convert_alpha())
        except Exception:
            continue
    return out


def draw_howto_screen(
    screen,
    font,
    font_small,
    screen_w: int,
    screen_h: int,
    back_btn: Button,
    howto_imgs: list[pygame.Surface],
) -> None:
    hb = _draw_tac_title_strip_menu(screen, screen_w, font_small)
    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, hb, screen_w, screen_h - hb))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, hb), (screen_w, hb), 1)
    title = font.render("BRIEFING // HOW TO OPERATE", True, COL_TAC_TERM_DIM)
    screen.blit(title, title.get_rect(center=(screen_w // 2, hb + 52)))
    lines = [
        "• Pick a border tile.",
        "• Choose launch direction.",
        "• Green edge tiles are valid escape exits.",
        "• You get one turn after the turn window opens.",
        "• Escape through green to win.",
        "• SAM predicts futures and tries to intercept.",
        "• AI explains confidence, weak plans, and mistakes (cosmetic text only).",
    ]
    y = hb + 88
    for ln in lines:
        screen.blit(font_small.render(ln, True, COL_TEXT), (24, y))
        y += font_small.get_height() + 6
    img_y = y + 8
    max_img_w = screen_w - 48
    for surf in howto_imgs[:6]:
        try:
            iw, ih = surf.get_size()
            if iw <= 0 or ih <= 0:
                continue
            scale = min(1.0, float(max_img_w) / float(iw))
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            thumb = pygame.transform.smoothscale(surf, (nw, nh))
            if img_y + nh > screen_h - 56:
                break
            screen.blit(thumb, ((screen_w - nw) // 2, img_y))
            img_y += nh + 10
        except Exception:
            continue
    back_btn.draw(screen, font_small)


def build_pva_buttons(ctrl_y: int, screen_w: int):
    bw, bh, gap, pad = 100, 28, 8, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    restart_btn = Button(pygame.Rect(pad, cy, bw, bh), "Restart")
    menu_btn = Button(pygame.Rect(pad + bw + gap, cy, bw, bh), "Menu")
    mute_btn = Button(pygame.Rect(max(10, screen_w - 78), cy, 72, bh), "Mute")
    return restart_btn, menu_btn, mute_btn


def build_pva_end_buttons(ctrl_y: int, screen_w: int) -> tuple[Button, Button, Button]:
    bw, bh, gap, pad = 118, 28, 6, 8
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    new_g = Button(pygame.Rect(pad, cy, bw, bh), "Start New Game")
    menu_b = Button(pygame.Rect(pad + bw + gap, cy, 72, bh), "Menu")
    replay_b = Button(pygame.Rect(pad + bw + gap + 72 + gap, cy, 124, bh), "Replay Last Game")
    return new_g, menu_b, replay_b


def build_replay_transport_buttons(ctrl_y: int, grid_w: int, total_w: int) -> tuple[Button, Button, Button, Button, Button]:
    bw, bh, gap, pad = 72, 28, 4, 8
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    prev_b = Button(pygame.Rect(pad, cy, bw, bh), "Prev Tick")
    next_b = Button(pygame.Rect(pad + bw + gap, cy, bw, bh), "Next Tick")
    play_b = Button(pygame.Rect(pad + 2 * (bw + gap), cy, 68, bh), "Play")
    new_b = Button(pygame.Rect(pad + 2 * (bw + gap) + 68 + gap, cy, 108, bh), "Start New Game")
    menu_b = Button(pygame.Rect(max(pad, total_w - 72 - 8), cy, 72, bh), "Menu")
    return prev_b, next_b, play_b, new_b, menu_b


def _dir_str(d: DirectionVector | None) -> str:
    if d is None:
        return "UNK"
    return f"{math.degrees(math.atan2(d.y, d.x)) % 360:.0f}°"


def _run_app() -> None:
    pygame.init()
    app = App()
    s = app.state
    grid_px_w = int(s.grid.width * CELL_SIZE)
    grid_h = int(s.grid.height * CELL_SIZE)
    info_y = grid_h
    ctrl_y = grid_h + INFO_HEIGHT
    screen_h = grid_h + PANEL_HEIGHT
    layout_span_w = grid_px_w

    menu_page = "main"
    howto_surfaces = load_howto_images()
    howto_back_btn = build_howto_back_button(grid_px_w, screen_h)

    pva_replay_active = False
    replay_index = 0
    replay_auto = False
    replay_accum_ms = 0.0

    screen = pygame.display.set_mode((layout_span_w, screen_h))
    pygame.display.set_caption("AvoidSAM — Tactical Command")
    font = pygame.font.SysFont("monospace", 13)
    font_small = pygame.font.SysFont("monospace", 11)
    replay_hdr_font = pygame.font.SysFont("monospace", 14, bold=True)
    replay_norm_font = pygame.font.SysFont("monospace", 13)
    replay_cmd_font = pygame.font.SysFont("monospace", 13)
    clock = pygame.time.Clock()
    sounds = SoundManager()
    sounds.init()
    pictures = PictureManager()
    pictures.load(grid_px_w=grid_px_w, grid_px_h=grid_h)

    menu_buttons = build_menu_buttons(layout_span_w, screen_h)
    auto_ctrl_btns, auto_scen_btns, auto_menu_btn, auto_mute_btn = build_auto_buttons(ctrl_y, layout_span_w)
    pva_restart_btn, pva_menu_btn, pva_mute_btn = build_pva_buttons(ctrl_y, layout_span_w)
    pva_end_new, pva_end_menu, pva_end_replay = build_pva_end_buttons(ctrl_y, layout_span_w)
    replay_prev_b, replay_next_b, replay_play_b, replay_new_b, replay_menu_b = build_replay_transport_buttons(
        ctrl_y, grid_px_w, layout_span_w
    )

    playing = True
    pause_timer = 0
    tick_ms = 1000.0 / C.TICK_RATE
    acc_ms = 0.0

    running = True
    while running:
        dt_ms = clock.tick(RENDER_FPS)
        render_fps = clock.get_fps()

        mouse_pos = pygame.mouse.get_pos()

        active_buttons: list[Button] = []
        if menu_page == "howto":
            active_buttons = [howto_back_btn]
        elif pva_replay_active:
            active_buttons = [replay_prev_b, replay_next_b, replay_play_b, replay_new_b, replay_menu_b, pva_mute_btn]
        elif app.mode == App.MODE_MENU:
            active_buttons = menu_buttons
        elif app.mode == App.MODE_AUTOMATIC:
            active_buttons = auto_ctrl_btns + auto_scen_btns + [auto_menu_btn, auto_mute_btn]
        elif app.mode == App.MODE_PVA:
            if app.pva_phase == App.PVA_END:
                active_buttons = [pva_end_new, pva_end_menu, pva_end_replay, pva_mute_btn]
            else:
                active_buttons = [pva_restart_btn, pva_menu_btn, pva_mute_btn]
        for btn in active_buttons:
            btn.check_hover(mouse_pos)

        # Hover updates
        if app.mode == App.MODE_PVA and (not pva_replay_active):
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
                    if pva_replay_active:
                        pva_replay_active = False
                        replay_auto = False
                        replay_accum_ms = 0.0
                        continue
                    if menu_page == "howto":
                        menu_page = "main"
                    elif app.mode == App.MODE_MENU:
                        running = False
                    else:
                        sounds.stop_all(immediate=True)
                        app.back_to_menu()
                        menu_page = "main"
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
                elif app.mode == App.MODE_PVA and not pva_replay_active:
                    if event.key == pygame.K_r:
                        sounds.stop_all(immediate=True)
                        app.restart_pva_round()
                        acc_ms = 0.0

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if menu_page == "howto" and event.button == 1:
                    if howto_back_btn.is_clicked(mouse_pos):
                        menu_page = "main"
                elif pva_replay_active and event.button == 1:
                    frs = getattr(app, "pva_replay_frames", []) or []
                    n = len(frs)
                    if n <= 0:
                        pass
                    elif replay_prev_b.is_clicked(mouse_pos):
                        replay_index = max(0, replay_index - 1)
                        replay_auto = False
                    elif replay_next_b.is_clicked(mouse_pos):
                        replay_index = min(n - 1, replay_index + 1)
                    elif replay_play_b.is_clicked(mouse_pos):
                        replay_auto = not replay_auto
                        replay_accum_ms = 0.0
                    elif replay_new_b.is_clicked(mouse_pos):
                        pva_replay_active = False
                        replay_auto = False
                        sounds.stop_all(immediate=True)
                        app.restart_pva_round()
                        acc_ms = 0.0
                    elif replay_menu_b.is_clicked(mouse_pos):
                        pva_replay_active = False
                        replay_auto = False
                        sounds.stop_all(immediate=True)
                        app.back_to_menu()
                        menu_page = "main"
                    elif pva_mute_btn.is_clicked(mouse_pos):
                        sounds.set_muted(not sounds.is_muted())

                elif app.mode == App.MODE_MENU and menu_page == "main" and event.button == 1:
                    if menu_buttons[0].is_clicked(mouse_pos):
                        sounds.stop_all(immediate=True)
                        app.start_pva_mode()
                        pause_timer = 0
                        acc_ms = 0.0
                    elif menu_buttons[1].is_clicked(mouse_pos):
                        menu_page = "howto"

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

                elif app.mode == App.MODE_PVA and not pva_replay_active:
                    if event.button == 1:
                        if pva_mute_btn.is_clicked(mouse_pos):
                            sounds.set_muted(not sounds.is_muted())
                        elif app.pva_phase == App.PVA_END:
                            if pva_end_new.is_clicked(mouse_pos):
                                sounds.stop_all(immediate=True)
                                app.restart_pva_round()
                                acc_ms = 0.0
                            elif pva_end_menu.is_clicked(mouse_pos):
                                sounds.stop_all(immediate=True)
                                app.back_to_menu()
                                menu_page = "main"
                                acc_ms = 0.0
                            elif pva_end_replay.is_clicked(mouse_pos) and len(
                                getattr(app, "pva_replay_frames", []) or []
                            ) > 0:
                                sounds.stop_all(immediate=True)
                                pva_replay_active = True
                                replay_index = 0
                                replay_auto = False
                                replay_accum_ms = 0.0
                        elif pva_restart_btn.is_clicked(mouse_pos):
                            sounds.stop_all(immediate=True)
                            app.restart_pva_round()
                            acc_ms = 0.0
                        elif pva_menu_btn.is_clicked(mouse_pos):
                            sounds.stop_all(immediate=True)
                            app.back_to_menu()
                            menu_page = "main"
                            acc_ms = 0.0
                        else:
                            app.pva_left_click()
                    elif event.button == 3:
                        app.pva_right_click()

        if pva_replay_active:
            frames = getattr(app, "pva_replay_frames", []) or []
            nfr = len(frames)
            if nfr <= 0:
                pva_replay_active = False
                replay_auto = False
            elif replay_index >= nfr:
                replay_index = max(0, nfr - 1)
            if replay_auto and nfr > 0:
                replay_accum_ms += dt_ms
                while replay_accum_ms >= REPLAY_AUTO_MS and replay_index < nfr - 1:
                    replay_accum_ms -= REPLAY_AUTO_MS
                    replay_index += 1
                if replay_index >= nfr - 1:
                    replay_auto = False

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
        elif app.mode == App.MODE_PVA and not pva_replay_active:
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
        if not pva_replay_active:
            sounds.update(app, tick_ms=tick_ms)

        replay_play_b.label = "Pause" if replay_auto else "Play"

        screen.fill(COL_BG)
        if not pva_replay_active:
            draw_grid(screen, app.state.grid)

        if app.mode == App.MODE_PVA and (not pva_replay_active) and app.pva_phase == App.PVA_RUNNING:
            fps = getattr(app, "ai_future_paths", [])
            draw_future_paths(screen, fps, app.state.grid)

        if app.mode != App.MODE_MENU or pva_replay_active:
            if pva_replay_active:
                frames_d = getattr(app, "pva_replay_frames", []) or []
                if frames_d:
                    rf = frames_d[replay_index]
                    _draw_tac_title_strip_replay(screen, layout_span_w, font_small)

                    band_top = REPLAY_TITLE_H
                    band_h = grid_h - band_top
                    # Fixed window: allocate ~64% map / ~36% panel.
                    panel_w = max(330, min(460, int(grid_px_w * 0.36)))
                    map_slot_w = max(120, grid_px_w - REPLAY_MAP_PAD_L - REPLAY_GAP - panel_w)
                    replay_map_dw = map_slot_w
                    replay_map_dh = max(1, int(round(grid_h * replay_map_dw / float(grid_px_w))))
                    if replay_map_dh > band_h:
                        replay_map_dh = band_h
                        replay_map_dw = max(120, int(round(grid_px_w * replay_map_dh / float(grid_h))))

                    buf = pygame.Surface((grid_px_w, grid_h))
                    draw_replay_board(buf, rf, pictures, app.state.grid)
                    draw_replay_result_overlay(buf, rf, pictures, app.state.grid)
                    tick_lbl = font_small.render(f"TICK {rf.tick}", True, COL_TAC_TERM_DIM)
                    buf.blit(tick_lbl, (8, 6))
                    scaled = pygame.transform.smoothscale(buf, (replay_map_dw, replay_map_dh))

                    mx = REPLAY_MAP_PAD_L
                    my = band_top + max(0, (band_h - replay_map_dh) // 2)
                    map_frame = pygame.Rect(mx - 2, my - 2, replay_map_dw + 4, replay_map_dh + 4)
                    pygame.draw.rect(screen, COL_PANEL_BG, map_frame)
                    pygame.draw.rect(screen, COL_PANEL_SEP, map_frame, 1)
                    screen.blit(scaled, (mx, my))

                    rp_left = mx + replay_map_dw + REPLAY_GAP
                    rp_rect = pygame.Rect(rp_left, band_top, max(260, grid_px_w - rp_left), band_h)

                    draw_tac_replay_side_panel(
                        screen,
                        rp_rect,
                        frames_d,
                        replay_index,
                        replay_hdr_font,
                        replay_norm_font,
                        replay_cmd_font,
                    )

                    pygame.draw.rect(screen, COL_PANEL_BG, pygame.Rect(0, info_y, layout_span_w, INFO_HEIGHT + CTRL_HEIGHT))
                    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (layout_span_w, info_y), 2)
                    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (layout_span_w, ctrl_y), 1)
                    screen.blit(
                        font_small.render("REPLAY TRANSPORT // BOTTOM CONSOLE", True, COL_TAC_LABEL),
                        (10, info_y + 2),
                    )
                    rline = (
                        f">> FRAME {replay_index + 1}/{len(frames_d)}    "
                        f"TACTICAL MAP (SCALED)    ESC ABORT ANALYSIS"
                    )
                    screen.blit(font.render(rline, True, COL_TEXT), (10, info_y + 14))

                    sum_lines = _replay_round_summary_lines(frames_d)[:2]
                    sh = font_small.get_height() + 3
                    sum_y = ctrl_y - 10 - len(sum_lines) * sh
                    if sum_y < info_y + 30:
                        sum_lines = sum_lines[:1]
                        sum_y = ctrl_y - 10 - sh
                    cw_px = layout_span_w - 20
                    for ln in sum_lines:
                        line = ln
                        while line and font_small.size(line + "…")[0] > cw_px:
                            line = line[:-1]
                        if ln and line != ln:
                            line = line.rstrip() + "…"
                        screen.blit(font_small.render(line, True, COL_TAC_LABEL), (10, sum_y))
                        sum_y += sh
                    for btn in (replay_prev_b, replay_next_b, replay_play_b, replay_new_b, replay_menu_b):
                        active = replay_auto and btn is replay_play_b
                        btn.draw(screen, font_small, active=active)
                    pva_mute_btn.label = "Unmute" if sounds.is_muted() else "Mute"
                    pva_mute_btn.draw(screen, font_small)

            elif app.mode == App.MODE_PVA:
                preview = app.pva_preview
                if preview.exit_tiles:
                    draw_all_edge_outlines(screen, app.state.grid, set(preview.exit_tiles))
                if app.pva_phase == App.PVA_TILE_SELECT and app.pva_hover_tile is not None:
                    ht = app.pva_hover_tile
                    glow = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
                    gr = tile_rect(ht).inflate(-4, -4)
                    pygame.draw.rect(glow, COL_TILE_HOVER_GLOW, gr)
                    screen.blit(glow, (0, 0))
                    draw_tile_border(screen, ht, COL_BORDER_TILE_HOVER, 5)
                if preview.tile is not None:
                    draw_tile_border(screen, preview.tile, COL_TILE_LOCK, 4)
                if app.pva_phase in (App.PVA_ANGLE_SELECT, App.PVA_CONFIRM):
                    draw_direction_fan(screen, app.pva_locked_tile, app.pva_valid_launch_dirs, app.pva_hover_direction)
                    draw_preview_path(
                        screen,
                        app.state.grid,
                        app.pva_locked_tile,
                        preview.direction,
                        COL_PREVIEW_RAY_CORE,
                    )

            if not pva_replay_active and app.mode != App.MODE_MENU:
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
            if menu_page == "howto":
                draw_howto_screen(screen, font, font_small, layout_span_w, screen_h, howto_back_btn, howto_surfaces)
            else:
                draw_menu(screen, font, font_small, layout_span_w, screen_h, menu_buttons)
        elif app.mode == App.MODE_AUTOMATIC:
            draw_auto_panel(
                screen,
                app,
                font,
                font_small,
                info_y,
                ctrl_y,
                layout_span_w,
                auto_ctrl_btns,
                auto_scen_btns,
                auto_menu_btn,
                auto_mute_btn,
                sounds.is_muted(),
                playing,
                pause_timer,
                render_fps,
            )
        elif not pva_replay_active:
            snap_n = len(getattr(app, "pva_replay_frames", []) or [])
            if app.pva_phase == App.PVA_END:
                draw_pva_panel(
                    screen,
                    app,
                    font,
                    font_small,
                    info_y,
                    ctrl_y,
                    layout_span_w,
                    (pva_end_new, pva_end_menu, pva_end_replay),
                    pva_mute_btn,
                    sounds.is_muted(),
                    render_fps,
                    replay_snapshot_count=snap_n,
                    replay_last_game_disabled=snap_n <= 0,
                )
            else:
                draw_pva_panel(
                    screen,
                    app,
                    font,
                    font_small,
                    info_y,
                    ctrl_y,
                    layout_span_w,
                    (pva_restart_btn, pva_menu_btn),
                    pva_mute_btn,
                    sounds.is_muted(),
                    render_fps,
                    replay_snapshot_count=snap_n,
                )

        pygame.display.flip()

    pygame.quit()


def main() -> None:
    """Run the game.

    Quiet by default for smoother Pygame loops (stdout discarded).
    Use ``AVOIDSAM_VERBOSE_LOGS=1`` for full Tee logging to ``logs/run_*.txt``.
    """
    stdout_orig = sys.stdout
    stderr_orig = sys.stderr
    verbose = os.environ.get("AVOIDSAM_VERBOSE_LOGS") == "1"
    log_file = None
    devnull_fp = None
    try:
        if verbose:
            logs_dir = Path("logs")
            logs_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            log_path = logs_dir / f"run_{stamp}.txt"
            log_file = open(log_path, "w", encoding="utf-8")
            sys.stdout = Tee(stdout_orig, log_file)
            sys.stderr = Tee(stderr_orig, log_file)
        else:
            devnull_fp = open(os.devnull, "w", encoding="utf-8")
            sys.stdout = devnull_fp
        _run_app()
    finally:
        sys.stdout = stdout_orig
        sys.stderr = stderr_orig
        if log_file is not None:
            log_file.close()
        if devnull_fp is not None:
            devnull_fp.close()


if __name__ == "__main__":
    main()