import pygame
import math
from game.app import App
from game import constants as C
from core.directions import DirectionVector

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

CELL_SIZE         = 38
INFO_HEIGHT       = 64
CTRL_HEIGHT       = 44
PANEL_HEIGHT      = INFO_HEIGHT + CTRL_HEIGHT
PAUSE_DURATION_MS = 1800
MAJOR_GRID        = 4       # major grid line every N tiles

COL_BG           = (12,  12,  18)
COL_GRID_MINOR   = (28,  28,  40)
COL_GRID_MAJOR   = (55,  55,  80)
COL_AIRCRAFT     = (60,  220, 80)
COL_AIRCRAFT_UNK = (120, 200, 120)
COL_TRUCK        = (60,  120, 255)
COL_MISSILE      = (255, 80,  50)
COL_TEXT         = (220, 220, 220)
COL_DIM          = (110, 110, 125)
COL_HIT          = (255, 210, 0)
COL_FAIL         = (220, 70,  70)
COL_PANEL_BG     = (20,  20,  32)
COL_PANEL_SEP    = (48,  48,  68)
COL_BTN          = (50,  50,  72)
COL_BTN_HOVER    = (75,  75, 108)
COL_BTN_ACTIVE   = (90,  150, 255)
COL_BTN_TEXT     = (215, 215, 215)


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------

class Button:
    def __init__(self, rect: pygame.Rect, label: str, key_hint: str = ""):
        self.rect     = rect
        self.label    = label
        self.key_hint = key_hint
        self.hovered  = False

    def draw(self, screen, font, active: bool = False) -> None:
        col = COL_BTN_ACTIVE if active else (COL_BTN_HOVER if self.hovered else COL_BTN)
        pygame.draw.rect(screen, col,          self.rect, border_radius=4)
        pygame.draw.rect(screen, COL_PANEL_SEP, self.rect, 1, border_radius=4)
        text = self.label + (f" [{self.key_hint}]" if self.key_hint else "")
        surf = font.render(text, True, COL_BTN_TEXT)
        screen.blit(surf, surf.get_rect(center=self.rect.center))

    def check_hover(self, pos) -> None:
        self.hovered = self.rect.collidepoint(pos)

    def is_clicked(self, pos) -> bool:
        return self.rect.collidepoint(pos)


# ---------------------------------------------------------------------------
# Grid drawing
# ---------------------------------------------------------------------------

def draw_grid(screen, grid) -> None:
    for x in range(grid.width + 1):
        col   = COL_GRID_MAJOR if x % MAJOR_GRID == 0 else COL_GRID_MINOR
        width = 2 if x % MAJOR_GRID == 0 else 1
        pygame.draw.line(screen, col,
                         (x * CELL_SIZE, 0),
                         (x * CELL_SIZE, grid.height * CELL_SIZE), width)
    for y in range(grid.height + 1):
        col   = COL_GRID_MAJOR if y % MAJOR_GRID == 0 else COL_GRID_MINOR
        width = 2 if y % MAJOR_GRID == 0 else 1
        pygame.draw.line(screen, col,
                         (0,                    y * CELL_SIZE),
                         (grid.width * CELL_SIZE, y * CELL_SIZE), width)


# ---------------------------------------------------------------------------
# Polygon helpers
# ---------------------------------------------------------------------------

def _rotated_polygon(points_local, cx, cy, angle_rad):
    """Rotate local points around origin then translate to (cx, cy)."""
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    result = []
    for lx, ly in points_local:
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a
        result.append((cx + rx, cy + ry))
    return result


def draw_aircraft(screen, pos, direction: DirectionVector | None, has_radar: bool) -> None:
    cx = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = pos.y * CELL_SIZE + CELL_SIZE / 2
    r  = CELL_SIZE * 0.38

    if not has_radar or direction is None:
        # Draw as diamond (unknown direction)
        pts = [(0, -r), (r * 0.55, 0), (0, r), (-r * 0.55, 0)]
        pygame.draw.polygon(screen, COL_AIRCRAFT_UNK, [(cx + x, cy + y) for x, y in pts])
        pygame.draw.polygon(screen, COL_AIRCRAFT,     [(cx + x, cy + y) for x, y in pts], 2)
    else:
        angle = math.atan2(direction.y, direction.x)
        pts   = [(r, 0), (-r * 0.7, -r * 0.5), (-r * 0.4, 0), (-r * 0.7, r * 0.5)]
        poly  = _rotated_polygon(pts, cx, cy, angle)
        pygame.draw.polygon(screen, COL_AIRCRAFT, poly)
        pygame.draw.polygon(screen, (160, 255, 160), poly, 1)


def draw_truck(screen, pos, direction: DirectionVector) -> None:
    cx    = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy    = pos.y * CELL_SIZE + CELL_SIZE / 2
    angle = math.atan2(direction.y, direction.x)
    hw, hh = CELL_SIZE * 0.42, CELL_SIZE * 0.28
    pts   = [( hw, -hh), ( hw,  hh), (-hw,  hh), (-hw, -hh)]
    poly  = _rotated_polygon(pts, cx, cy, angle)
    pygame.draw.polygon(screen, COL_TRUCK, poly)
    pygame.draw.polygon(screen, (140, 180, 255), poly, 2)


def draw_missile(screen, pos, direction: DirectionVector) -> None:
    cx    = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy    = pos.y * CELL_SIZE + CELL_SIZE / 2
    angle = math.atan2(direction.y, direction.x)
    r     = CELL_SIZE * 0.22
    pts   = [(r, 0), (-r, -r * 0.5), (-r * 0.5, 0), (-r, r * 0.5)]
    poly  = _rotated_polygon(pts, cx, cy, angle)
    pygame.draw.polygon(screen, COL_MISSILE, poly)


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def _dir_str(d: DirectionVector | None) -> str:
    if d is None:
        return "UNKNOWN"
    angle_deg = math.degrees(math.atan2(d.y, d.x)) % 360
    return f"{angle_deg:.1f}°"


def draw_panel(
    screen, app, font, font_small,
    info_y, ctrl_y, screen_w,
    ctrl_btns, scen_btns,
    playing, pause_timer,
) -> None:
    s        = app.state
    scenario = app.current_scenario
    total    = len(app.scenarios)
    idx      = app.scenario_index + 1

    pygame.draw.rect(screen, COL_PANEL_BG,
                     pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y),  (screen_w, info_y),  1)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y),  (screen_w, ctrl_y),  1)

    if s.intercepted:
        status, sc = "INTERCEPTED",    COL_HIT
    elif getattr(s, "failed", False):
        status, sc = "FAILED (stall)", COL_FAIL
    elif s.tick >= C.MAX_STEPS:
        status, sc = "TIMEOUT",        COL_DIM
    elif not playing:
        status, sc = "PAUSED",         (200, 160, 60)
    else:
        status, sc = "running",        COL_TEXT

    auto_str = (f"  |  next in {pause_timer // 1000 + 1}s"
                if pause_timer > 0 and playing else "")

    ac  = s.aircraft
    truck = s.sam_truck
    missiles = s.missiles

    ac_dir_str   = _dir_str(app.inferred_direction)
    ac_spd_str   = f"{app.inferred_speed:.2f}" if app.inferred_speed is not None else "UNKNOWN"
    truck_dir_str = _dir_str(truck.direction)

    line1 = (f"Scenario {idx}/{total}: {scenario.name}"
             f"   |   Tick {s.tick}/{C.MAX_STEPS}   |   {status}{auto_str}")
    line2 = (f"AC spd={ac_spd_str}  dir={ac_dir_str}"
             f"   |   Truck dir={truck_dir_str}"
             f"   |   Plan: {app.last_plan_type or '—'}"
             f"   |   Action: {app.last_action or '—'}")
    # missile info
    m_parts = []
    for i, m in enumerate(missiles):
        if m.active:
            m_parts.append(f"M{i} dir={_dir_str(m.direction)} spd={m.speed:.1f}")
    line3 = "  ".join(m_parts) if m_parts else "No active missiles"

    screen.blit(font.render(line1, True, sc),         (10, info_y + 4))
    screen.blit(font_small.render(line2, True, COL_TEXT), (10, info_y + 24))
    screen.blit(font_small.render(line3, True, COL_DIM),  (10, info_y + 42))

    for btn in ctrl_btns:
        active = (btn.label == "Pause" and not playing) or \
                 (btn.label == "Play"  and playing)
        btn.draw(screen, font_small, active=active)

    for i, sbtn in enumerate(scen_btns):
        sbtn.draw(screen, font_small, active=(i == app.scenario_index))


def build_buttons(ctrl_y: int) -> tuple[list[Button], list[Button]]:
    bw, bh = 74, 28
    gap, pad = 6, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    ctrl_btns = [
        Button(pygame.Rect(pad + 0*(bw+gap), cy, bw, bh), "Play",  "Spc"),
        Button(pygame.Rect(pad + 1*(bw+gap), cy, bw, bh), "Pause", "Spc"),
        Button(pygame.Rect(pad + 2*(bw+gap), cy, bw, bh), "Prev",  "←"),
        Button(pygame.Rect(pad + 3*(bw+gap), cy, bw, bh), "Next",  "→"),
    ]
    sw = 28
    sx = pad + 4*(bw+gap) + 14
    scen_btns = [
        Button(pygame.Rect(sx + i*(sw+4), cy, sw, bh), str(i+1))
        for i in range(12)
    ]
    return ctrl_btns, scen_btns


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    pygame.init()

    app      = App()
    s        = app.state
    screen_w = s.grid.width  * CELL_SIZE
    grid_h   = s.grid.height * CELL_SIZE
    info_y   = grid_h
    ctrl_y   = grid_h + INFO_HEIGHT
    screen_h = grid_h + PANEL_HEIGHT

    screen = pygame.display.set_mode((screen_w, screen_h))
    pygame.display.set_caption("AvoidSAM — scenario viewer")

    font       = pygame.font.SysFont("monospace", 13)
    font_small = pygame.font.SysFont("monospace", 11)
    font_label = pygame.font.SysFont("monospace", 10, bold=True)

    clock = pygame.time.Clock()
    ctrl_btns, scen_btns = build_buttons(ctrl_y)

    playing     : bool = True
    pause_timer : int  = 0

    app.load_scenario(0)

    running = True
    while running:
        clock.tick(C.TICK_RATE)
        mouse_pos = pygame.mouse.get_pos()

        for btn in ctrl_btns + scen_btns:
            btn.check_hover(mouse_pos)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    playing = not playing
                elif event.key == pygame.K_RIGHT:
                    app.advance_to_next_scenario(); pause_timer = 0
                elif event.key == pygame.K_LEFT:
                    app.load_scenario(app.scenario_index - 1); pause_timer = 0
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    i = event.key - pygame.K_1
                    if i < len(app.scenarios):
                        app.load_scenario(i); pause_timer = 0
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if ctrl_btns[0].is_clicked(mouse_pos):
                    playing = True
                elif ctrl_btns[1].is_clicked(mouse_pos):
                    playing = False
                elif ctrl_btns[2].is_clicked(mouse_pos):
                    app.load_scenario(app.scenario_index - 1); pause_timer = 0
                elif ctrl_btns[3].is_clicked(mouse_pos):
                    app.advance_to_next_scenario(); pause_timer = 0
                else:
                    for i, sbtn in enumerate(scen_btns):
                        if sbtn.is_clicked(mouse_pos) and i < len(app.scenarios):
                            app.load_scenario(i); pause_timer = 0; break

        if playing:
            if pause_timer > 0:
                pause_timer -= clock.get_time()
                if pause_timer <= 0:
                    pause_timer = 0
                    app.advance_to_next_scenario()
            elif app.scenario_finished():
                pause_timer = PAUSE_DURATION_MS
            else:
                app.run_step()

        # ---- draw ----------------------------------------------------------
        screen.fill(COL_BG)
        draw_grid(screen, app.state.grid)

        s     = app.state
        truck = s.sam_truck
        has_radar = app.inferred_direction is not None

        draw_aircraft(screen, s.aircraft.position, app.inferred_direction, has_radar)
        draw_truck(screen, truck.position, truck.direction)
        for m in s.missiles:
            if m.active:
                draw_missile(screen, m.position, m.direction)

        draw_panel(
            screen, app, font, font_small,
            info_y=info_y, ctrl_y=ctrl_y, screen_w=screen_w,
            ctrl_btns=ctrl_btns,
            scen_btns=scen_btns[:len(app.scenarios)],
            playing=playing,
            pause_timer=pause_timer,
        )

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()