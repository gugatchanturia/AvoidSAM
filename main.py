"""
main.py — pygame viewer

Render at 60 FPS. Simulation advances at C.TICK_RATE via fixed-step accumulator.
Entities drawn at latest simulation float positions (no interpolation).
"""
import pygame
import math
from game.app import App
from game import constants as C
from core.directions import DirectionVector

CELL_SIZE         = 38
INFO_HEIGHT       = 82
CTRL_HEIGHT       = 44
PANEL_HEIGHT      = INFO_HEIGHT + CTRL_HEIGHT
RENDER_FPS        = 60
MAX_SIM_STEPS_PER_FRAME = 1
PAUSE_DURATION_MS = 1800
MAJOR_GRID        = 4

COL_BG           = (12,  12,  18)
COL_GRID_MINOR   = (28,  28,  40)
COL_GRID_MAJOR   = (55,  55,  80)
COL_AIRCRAFT     = (60,  220, 80)
COL_AIRCRAFT_UNK = (120, 200, 120)
COL_TRUCK        = (60,  120, 255)
COL_MISSILE      = (255, 80,  50)
COL_TEXT         = (220, 220, 220)
COL_DIM          = (110, 110, 125)
COL_PERF_OK      = (130, 200, 130)
COL_PERF_SLOW    = (255, 140,  40)
COL_HIT          = (255, 210,   0)
COL_FAIL         = (220,  70,  70)
COL_PANEL_BG     = (20,  20,  32)
COL_PANEL_SEP    = (48,  48,  68)
COL_BTN          = (50,  50,  72)
COL_BTN_HOVER    = (75,  75, 108)
COL_BTN_ACTIVE   = (90, 150, 255)
COL_BTN_TEXT     = (215, 215, 215)


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------

class Button:
    def __init__(self, rect: pygame.Rect, label: str, key_hint: str = ""):
        self.rect, self.label, self.key_hint = rect, label, key_hint
        self.hovered = False

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
# Grid
# ---------------------------------------------------------------------------

def draw_grid(screen, grid) -> None:
    for x in range(grid.width + 1):
        col, w = (COL_GRID_MAJOR, 2) if x % MAJOR_GRID == 0 else (COL_GRID_MINOR, 1)
        pygame.draw.line(screen, col, (x*CELL_SIZE, 0), (x*CELL_SIZE, grid.height*CELL_SIZE), w)
    for y in range(grid.height + 1):
        col, w = (COL_GRID_MAJOR, 2) if y % MAJOR_GRID == 0 else (COL_GRID_MINOR, 1)
        pygame.draw.line(screen, col, (0, y*CELL_SIZE), (grid.width*CELL_SIZE, y*CELL_SIZE), w)


# ---------------------------------------------------------------------------
# Entity shapes
# ---------------------------------------------------------------------------

def _rot(pts, cx, cy, a):
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + lx*ca - ly*sa, cy + lx*sa + ly*ca) for lx, ly in pts]


def draw_aircraft(screen, pos, direction, has_radar) -> None:
    cx = pos.x * CELL_SIZE + CELL_SIZE / 2
    cy = pos.y * CELL_SIZE + CELL_SIZE / 2
    r  = CELL_SIZE * 0.38
    if not has_radar or direction is None:
        pts = [(0,-r),(r*.55,0),(0,r),(-r*.55,0)]
        pygame.draw.polygon(screen, COL_AIRCRAFT_UNK, [(cx+x,cy+y) for x,y in pts])
        pygame.draw.polygon(screen, COL_AIRCRAFT,     [(cx+x,cy+y) for x,y in pts], 2)
    else:
        angle = math.atan2(direction.y, direction.x)
        poly  = _rot([(r,0),(-r*.7,-r*.5),(-r*.4,0),(-r*.7,r*.5)], cx, cy, angle)
        pygame.draw.polygon(screen, COL_AIRCRAFT, poly)
        pygame.draw.polygon(screen, (160,255,160), poly, 1)


def draw_truck(screen, pos, direction) -> None:
    cx, cy = pos.x*CELL_SIZE + CELL_SIZE/2, pos.y*CELL_SIZE + CELL_SIZE/2
    angle  = math.atan2(direction.y, direction.x)
    hw, hh = CELL_SIZE*.42, CELL_SIZE*.28
    poly   = _rot([(hw,-hh),(hw,hh),(-hw,hh),(-hw,-hh)], cx, cy, angle)
    pygame.draw.polygon(screen, COL_TRUCK, poly)
    pygame.draw.polygon(screen, (140,180,255), poly, 2)


def draw_missile(screen, pos, direction) -> None:
    cx, cy = pos.x*CELL_SIZE + CELL_SIZE/2, pos.y*CELL_SIZE + CELL_SIZE/2
    r      = CELL_SIZE * 0.22
    angle  = math.atan2(direction.y, direction.x)
    poly   = _rot([(r,0),(-r,-r*.5),(-r*.5,0),(-r,r*.5)], cx, cy, angle)
    pygame.draw.polygon(screen, COL_MISSILE, poly)


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def _dir_str(d) -> str:
    if d is None:
        return "UNK"
    return f"{math.degrees(math.atan2(d.y, d.x)) % 360:.0f}°"


def draw_panel(screen, app, font, font_small,
               info_y, ctrl_y, screen_w,
               ctrl_btns, scen_btns,
               playing, pause_timer, render_fps) -> None:
    s        = app.state
    scenario = app.current_scenario
    idx      = app.scenario_index + 1
    total    = len(app.scenarios)

    pygame.draw.rect(screen, COL_PANEL_BG,
                     pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 1)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)

    if s.intercepted:
        status, sc = "INTERCEPTED",    COL_HIT
    elif getattr(s, "failed", False):
        status, sc = "FAILED",         COL_FAIL
    elif s.tick >= C.MAX_STEPS:
        status, sc = "TIMEOUT",        COL_DIM
    elif not playing:
        status, sc = "PAUSED",         (200,160,60)
    else:
        status, sc = "running",        COL_TEXT

    auto_str = (f"  next in {pause_timer//1000+1}s" if pause_timer > 0 and playing else "")

    truck = s.sam_truck
    diag  = app.last_diag

    line1 = (f"Sc {idx}/{total}: {scenario.name}"
             f"   Tick {s.tick}/{C.MAX_STEPS}   {status}{auto_str}")
    line2 = (f"AC spd={'{:.1f}'.format(app.inferred_speed) if app.inferred_speed else '?'}"
             f" dir={_dir_str(app.inferred_direction)}"
             f"  |  Truck dir={_dir_str(truck.direction)}"
             f"  |  {app.last_plan_type or '—'}  {app.last_action or '—'}")

    m_parts = [f"M{i} {_dir_str(m.direction)}" for i,m in enumerate(s.missiles) if m.active]
    no_sol  = f"  [{diag.no_solution_reason}]" if diag.no_solution_reason else ""
    line3   = ("  ".join(m_parts) if m_parts else "No missiles") + no_sol

    tick_budget = 1000.0 / C.TICK_RATE
    perf_col = COL_PERF_SLOW if app.last_planner_ms > tick_budget else COL_PERF_OK
    line4 = (f"plan={app.last_planner_ms:.1f}ms"
             f"  step={app.last_step_ms:.1f}ms"
             f"  rfps={render_fps:.0f}"
             f"  cands={diag.candidates_evaluated}"
             f"  ver={diag.directions_verified}"
             f"  fb={'Y' if diag.fallback_used else 'N'}"
             f"  pred={diag.predictor_name}")

    screen.blit(font.render(line1, True, sc),              (10, info_y + 3))
    screen.blit(font_small.render(line2, True, COL_TEXT),  (10, info_y + 21))
    screen.blit(font_small.render(line3, True, COL_DIM),   (10, info_y + 39))
    screen.blit(font_small.render(line4, True, perf_col),  (10, info_y + 57))

    for btn in ctrl_btns:
        active = (btn.label == "Pause" and not playing) or (btn.label == "Play" and playing)
        btn.draw(screen, font_small, active=active)
    for i, sbtn in enumerate(scen_btns):
        sbtn.draw(screen, font_small, active=(i == app.scenario_index))


def build_buttons(ctrl_y: int) -> tuple[list[Button], list[Button]]:
    bw, bh, gap, pad = 74, 28, 6, 10
    cy = ctrl_y + (CTRL_HEIGHT - bh) // 2
    ctrl_btns = [
        Button(pygame.Rect(pad + i*(bw+gap), cy, bw, bh), lbl, hint)
        for i, (lbl, hint) in enumerate([("Play","Spc"),("Pause","Spc"),("Prev","←"),("Next","→")])
    ]
    sw = 28
    sx = pad + 4*(bw+gap) + 14
    scen_btns = [Button(pygame.Rect(sx+i*(sw+4), cy, sw, bh), str(i+1)) for i in range(12)]
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

    screen     = pygame.display.set_mode((screen_w, screen_h))
    pygame.display.set_caption("AvoidSAM")
    font       = pygame.font.SysFont("monospace", 13)
    font_small = pygame.font.SysFont("monospace", 11)
    clock      = pygame.time.Clock()

    ctrl_btns, scen_btns = build_buttons(ctrl_y)
    playing     : bool  = True
    pause_timer : int   = 0
    tick_ms     : float = 1000.0 / C.TICK_RATE
    acc_ms      : float = 0.0

    app.load_scenario(0)

    running = True
    while running:
        dt_ms      = clock.tick(RENDER_FPS)
        render_fps = clock.get_fps()
        mouse_pos  = pygame.mouse.get_pos()

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
                    app.advance_to_next_scenario(); pause_timer = 0; acc_ms = 0.0
                elif event.key == pygame.K_LEFT:
                    app.load_scenario(app.scenario_index - 1); pause_timer = 0; acc_ms = 0.0
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    i = event.key - pygame.K_1
                    if i < len(app.scenarios):
                        app.load_scenario(i); pause_timer = 0; acc_ms = 0.0
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if   ctrl_btns[0].is_clicked(mouse_pos): playing = True
                elif ctrl_btns[1].is_clicked(mouse_pos): playing = False
                elif ctrl_btns[2].is_clicked(mouse_pos):
                    app.load_scenario(app.scenario_index - 1); pause_timer = 0; acc_ms = 0.0
                elif ctrl_btns[3].is_clicked(mouse_pos):
                    app.advance_to_next_scenario(); pause_timer = 0; acc_ms = 0.0
                else:
                    for i, sb in enumerate(scen_btns):
                        if sb.is_clicked(mouse_pos) and i < len(app.scenarios):
                            app.load_scenario(i); pause_timer = 0; acc_ms = 0.0; break

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

        screen.fill(COL_BG)
        draw_grid(screen, app.state.grid)

        s     = app.state
        truck = s.sam_truck
        draw_aircraft(screen, s.aircraft.position, app.inferred_direction,
                      app.inferred_direction is not None)
        draw_truck(screen, truck.position, truck.direction)
        for m in s.missiles:
            if m.active:
                draw_missile(screen, m.position, m.direction)

        draw_panel(screen, app, font, font_small,
                   info_y=info_y, ctrl_y=ctrl_y, screen_w=screen_w,
                   ctrl_btns=ctrl_btns,
                   scen_btns=scen_btns[:len(app.scenarios)],
                   playing=playing, pause_timer=pause_timer,
                   render_fps=render_fps)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()