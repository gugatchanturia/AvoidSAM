import colorsys
import math
import pygame

from core.directions import DirectionVector, DIRECTIONS
from core.vector import Vector2D
from game.app import App
from game import constants as C
from game.pva_rules import all_border_tiles, is_border_tile

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
COL_PREVIEW = (160, 160, 220, 70)
COL_TURN = (220, 220, 80, 80)
COL_TURN_HI = (255, 255, 180, 180)
COL_JAMMER = (255, 60, 60)


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

def draw_movement_trails(screen, segments) -> None:
    if not segments:
        return
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    for seg in segments:
        if seg.kind == "aircraft":
            base = COL_AIRCRAFT
            w = 4
            a = 235
        elif seg.kind == "sam":
            base = COL_TRUCK
            w = 5
            a = 245
        else:
            base = COL_MISSILE
            w = 3
            a = 235
        col = (int(base[0]), int(base[1]), int(base[2]), int(a))
        x0 = int(seg.start.x * CELL_SIZE + CELL_SIZE / 2)
        y0 = int(seg.start.y * CELL_SIZE + CELL_SIZE / 2)
        x1 = int(seg.end.x * CELL_SIZE + CELL_SIZE / 2)
        y1 = int(seg.end.y * CELL_SIZE + CELL_SIZE / 2)
        pygame.draw.line(overlay, col, (x0, y0), (x1, y1), w)
        if seg.kind == "sam":
            pygame.draw.circle(overlay, col, (x1, y1), 3)
    screen.blit(overlay, (0, 0))



def _rot(pts, cx, cy, a):
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + lx * ca - ly * sa, cy + lx * sa + ly * ca) for lx, ly in pts]


def draw_aircraft(screen, pos, direction, has_radar) -> None:
    cx = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = pos.y * CELL_SIZE + CELL_SIZE / 2
    r = CELL_SIZE * 0.38
    if not has_radar or direction is None:
        pts = [(0, -r), (r * 0.55, 0), (0, r), (-r * 0.55, 0)]
        pygame.draw.polygon(screen, COL_AIRCRAFT_UNK, [(cx + x, cy + y) for x, y in pts])
        pygame.draw.polygon(screen, COL_AIRCRAFT, [(cx + x, cy + y) for x, y in pts], 2)
    else:
        angle = math.atan2(direction.y, direction.x)
        poly = _rot([(r, 0), (-r * 0.7, -r * 0.5), (-r * 0.4, 0), (-r * 0.7, r * 0.5)], cx, cy, angle)
        pygame.draw.polygon(screen, COL_AIRCRAFT, poly)
        pygame.draw.polygon(screen, (160, 255, 160), poly, 1)


def draw_truck(screen, pos, direction) -> None:
    cx, cy = pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2
    angle = math.atan2(direction.y, direction.x)
    hw, hh = CELL_SIZE * 0.42, CELL_SIZE * 0.28
    poly = _rot([(hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)], cx, cy, angle)
    pygame.draw.polygon(screen, COL_TRUCK, poly)
    pygame.draw.polygon(screen, (140, 180, 255), poly, 2)


def draw_missile(screen, pos, direction) -> None:
    cx, cy = pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2
    r = CELL_SIZE * 0.22
    angle = math.atan2(direction.y, direction.x)
    poly = _rot([(r, 0), (-r, -r * 0.5), (-r * 0.5, 0), (-r, r * 0.5)], cx, cy, angle)
    pygame.draw.polygon(screen, COL_MISSILE, poly)


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
        col = COL_PREVIEW
        width = 2
        if highlight is not None and d.index == highlight.index:
            hc = _dir_color(highlight)
            col = (*hc, 220)
            width = 4
        pygame.draw.line(overlay, col, (ox, oy), end, width)
    screen.blit(overlay, (0, 0))


def draw_preview_path(screen, grid, origin_tile: tuple[int, int], direction: DirectionVector | None, color) -> None:
    if origin_tile is None or direction is None:
        return
    start = Vector2D(float(origin_tile[0]), float(origin_tile[1]))
    pos = Vector2D(start.x, start.y)
    pts = [(pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2)]
    for _ in range(200):
        next_pos = Vector2D(pos.x + direction.x * 0.25, pos.y + direction.y * 0.25)
        if not grid.in_bounds(next_pos):
            break
        pos = next_pos
        pts.append((pos.x * CELL_SIZE + CELL_SIZE / 2, pos.y * CELL_SIZE + CELL_SIZE / 2))
    if len(pts) >= 2:
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        pygame.draw.lines(overlay, (*color, 140), False, pts, 3)
        screen.blit(overlay, (0, 0))


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


def draw_jammer_radius(screen, truck_pos: Vector2D, radius_tiles: float) -> None:
    if radius_tiles <= 0:
        return
    cx = truck_pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = truck_pos.y * CELL_SIZE + CELL_SIZE / 2
    rpx = int(round(radius_tiles * CELL_SIZE))
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    pygame.draw.circle(overlay, (*COL_JAMMER, 28), (int(cx), int(cy)), rpx)
    pygame.draw.circle(overlay, (*COL_JAMMER, 120), (int(cx), int(cy)), rpx, 2)
    screen.blit(overlay, (0, 0))


def draw_menu(screen, font, font_small, screen_w, screen_h, buttons: list[Button]) -> None:
    title = font.render("AvoidSAM", True, COL_TEXT)
    subtitle = font_small.render("Choose a mode", True, COL_DIM)
    screen.blit(title, title.get_rect(center=(screen_w // 2, screen_h // 2 - 120)))
    screen.blit(subtitle, subtitle.get_rect(center=(screen_w // 2, screen_h // 2 - 88)))
    for btn in buttons:
        btn.draw(screen, font_small)


def draw_auto_panel(screen, app, font, font_small, info_y, ctrl_y, screen_w,
                    ctrl_btns, scen_btns, menu_btn, playing, pause_timer, render_fps) -> None:
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


def draw_pva_panel(screen, app, font, font_small, info_y, ctrl_y, screen_w,
                   restart_btn, menu_btn, render_fps) -> None:
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
        j_on = "ON" if app.jammer_active() else "OFF"
        j_yes = "YES" if app.aircraft_jammed() else "NO"
        j_dist = app.jammer_distance_tiles()
        j_dist_str = f"{j_dist:.2f}/{float(C.JAMMER_RADIUS):.1f}" if j_dist is not None else "—"
        line3 = (
            f"Valid exits: {len(app.pva_locked_exit_tiles)} tiles  "
            f"Turn UI={len(app.pva_player_turn_dirs)} dirs  SAM threat={len(app.pva_sam_threat_turn_dirs)}  "
            f"JAMMER={j_on}  JAMMED={j_yes}  JDIST={j_dist_str}  "
            f"Truck plan={app.last_plan_type or '—'}  "
            f"Planner {app.last_planner_ms:.1f}ms"
        )
    else:
        line3 = (
            f"Valid launch dirs: {len(app.pva_valid_launch_dirs)}  "
            f"Valid exits: {len(preview.exit_tiles)}  "
            f"LMB confirm / RMB back"
        )
    line4 = (
        f"step={app.last_step_ms:.1f}ms  rfps={render_fps:.0f}  "
        f"cands={app.last_diag.candidates_evaluated}  ver={app.last_diag.directions_verified}"
    )

    screen.blit(font.render(line1, True, sc), (10, info_y + 3))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 23))
    screen.blit(font_small.render(line3, True, COL_DIM), (10, info_y + 41))
    screen.blit(font_small.render(line4, True, COL_PERF_OK), (10, info_y + 59))

    restart_btn.draw(screen, font_small)
    menu_btn.draw(screen, font_small)


def build_auto_buttons(ctrl_y: int):
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
    return ctrl_btns, scen_btns, menu_btn


def build_menu_buttons(screen_w: int, screen_h: int) -> list[Button]:
    bw, bh = 260, 44
    x = screen_w // 2 - bw // 2
    y = screen_h // 2 - 30
    return [
        Button(pygame.Rect(x, y, bw, bh), "Automatic System"),
        Button(pygame.Rect(x, y + 60, bw, bh), "Player vs Agent"),
    ]


def build_pva_buttons(ctrl_y: int):
    bw, bh, gap, pad = 100, 28, 8, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    restart_btn = Button(pygame.Rect(pad, cy, bw, bh), "Restart")
    menu_btn = Button(pygame.Rect(pad + bw + gap, cy, bw, bh), "Menu")
    return restart_btn, menu_btn


def _dir_str(d: DirectionVector | None) -> str:
    if d is None:
        return "UNK"
    return f"{math.degrees(math.atan2(d.y, d.x)) % 360:.0f}°"


def main() -> None:
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

    menu_buttons = build_menu_buttons(screen_w, screen_h)
    auto_ctrl_btns, auto_scen_btns, auto_menu_btn = build_auto_buttons(ctrl_y)
    pva_restart_btn, pva_menu_btn = build_pva_buttons(ctrl_y)

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
            active_buttons = auto_ctrl_btns + auto_scen_btns + [auto_menu_btn]
        elif app.mode == App.MODE_PVA:
            active_buttons = [pva_restart_btn, pva_menu_btn]
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
                        app.restart_pva_round()
                        acc_ms = 0.0

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if app.mode == App.MODE_MENU and event.button == 1:
                    if menu_buttons[0].is_clicked(mouse_pos):
                        app.start_automatic_mode()
                        playing = True
                        pause_timer = 0
                        acc_ms = 0.0
                    elif menu_buttons[1].is_clicked(mouse_pos):
                        app.start_pva_mode()
                        pause_timer = 0
                        acc_ms = 0.0

                elif app.mode == App.MODE_AUTOMATIC and event.button == 1:
                    if auto_ctrl_btns[0].is_clicked(mouse_pos):
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
                        if pva_restart_btn.is_clicked(mouse_pos):
                            app.restart_pva_round()
                            acc_ms = 0.0
                        elif pva_menu_btn.is_clicked(mouse_pos):
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

        screen.fill(COL_BG)
        draw_grid(screen, app.state.grid)

        if app.mode != App.MODE_MENU:
            truck = app.state.sam_truck
            if app.mode == App.MODE_PVA:
                draw_jammer_radius(screen, truck.position, float(C.JAMMER_RADIUS))
            draw_movement_trails(screen, app.movement_trails)
            draw_truck(screen, truck.position, truck.direction)

            if app.mode == App.MODE_AUTOMATIC:
                draw_aircraft(screen, app.state.aircraft.position, app.inferred_direction, app.inferred_direction is not None)
            elif app.has_aircraft:
                draw_aircraft(screen, app.state.aircraft.position, app.state.aircraft.direction, True)

            for m in app.state.missiles:
                if m.active:
                    draw_missile(screen, m.position, m.direction)

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
                    draw_preview_path(screen, app.state.grid, app.pva_locked_tile, preview.direction, _dir_color(preview.direction))
                if (
                    app.mode == App.MODE_PVA
                    and app.pva_phase == App.PVA_RUNNING
                    and not app.pva_turn_used
                    and app.has_aircraft
                    and app._pva_in_turn_window()
                    and not app.aircraft_jammed()
                ):
                    draw_turn_wheel(
                        screen,
                        app.state.aircraft.position,
                        app.pva_player_turn_dirs if app.pva_player_turn_dirs else list(DIRECTIONS),
                        app.pva_turn_hover_direction,
                    )

        if app.mode == App.MODE_MENU:
            draw_menu(screen, font, font_small, screen_w, screen_h, menu_buttons)
        elif app.mode == App.MODE_AUTOMATIC:
            draw_auto_panel(
                screen, app, font, font_small,
                info_y, ctrl_y, screen_w,
                auto_ctrl_btns, auto_scen_btns, auto_menu_btn,
                playing, pause_timer, render_fps,
            )
        else:
            draw_pva_panel(
                screen, app, font, font_small,
                info_y, ctrl_y, screen_w,
                pva_restart_btn, pva_menu_btn, render_fps,
            )

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()