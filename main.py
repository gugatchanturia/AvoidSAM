import pygame
from game.app import App
from game import constants as C

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

CELL_SIZE         = 38
INFO_HEIGHT       = 48
CTRL_HEIGHT       = 44
PANEL_HEIGHT      = INFO_HEIGHT + CTRL_HEIGHT
PAUSE_DURATION_MS = 1800

COL_BG         = (15,  15,  20)
COL_GRID       = (35,  35,  45)
COL_AIRCRAFT   = (60,  220, 80)
COL_TRUCK      = (60,  120, 255)
COL_MISSILE    = (255, 60,  60)
COL_TEXT       = (220, 220, 220)
COL_DIM        = (120, 120, 130)
COL_HIT        = (255, 200, 0)
COL_FAIL       = (220, 80,  80)
COL_PANEL_BG   = (25,  25,  35)
COL_PANEL_SEP  = (50,  50,  65)
COL_BTN        = (55,  55,  75)
COL_BTN_HOVER  = (80,  80, 110)
COL_BTN_ACTIVE = (100, 160, 255)
COL_BTN_TEXT   = (220, 220, 220)


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
        pygame.draw.rect(screen, col,     self.rect, border_radius=4)
        pygame.draw.rect(screen, COL_DIM, self.rect, 1, border_radius=4)
        text = self.label + (f" [{self.key_hint}]" if self.key_hint else "")
        surf = font.render(text, True, COL_BTN_TEXT)
        screen.blit(surf, surf.get_rect(center=self.rect.center))

    def check_hover(self, pos) -> None:
        self.hovered = self.rect.collidepoint(pos)

    def is_clicked(self, pos) -> bool:
        return self.rect.collidepoint(pos)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def draw_grid(screen, grid) -> None:
    for x in range(grid.width):
        for y in range(grid.height):
            rect = pygame.Rect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(screen, COL_GRID, rect, 1)


def draw_entity(screen, pos, color, label: str | None = None, font=None) -> None:
    """Draw at float position — no tile snapping."""
    px   = int(pos.x * CELL_SIZE)
    py   = int(pos.y * CELL_SIZE)
    rect = pygame.Rect(px + 2, py + 2, CELL_SIZE - 4, CELL_SIZE - 4)
    pygame.draw.rect(screen, color, rect)
    if label and font:
        surf = font.render(label, True, (0, 0, 0))
        screen.blit(surf, surf.get_rect(center=rect.center))


def draw_panel(
    screen, app, font, font_small,
    info_y: int, ctrl_y: int, screen_w: int,
    ctrl_btns: list[Button],
    scen_btns:  list[Button],
    playing: bool,
    pause_timer: int,
) -> None:
    s        = app.state
    scenario = app.current_scenario
    total    = len(app.scenarios)
    idx      = app.scenario_index + 1

    pygame.draw.rect(screen, COL_PANEL_BG,
                     pygame.Rect(0, info_y, screen_w, INFO_HEIGHT + CTRL_HEIGHT))
    pygame.draw.line(screen, COL_PANEL_SEP, (0, info_y), (screen_w, info_y), 1)
    pygame.draw.line(screen, COL_PANEL_SEP, (0, ctrl_y), (screen_w, ctrl_y), 1)

    if s.intercepted:
        status, status_col = "INTERCEPTED", COL_HIT
    elif getattr(s, "failed", False):
        status, status_col = "FAILED (stall)", COL_FAIL
    elif s.tick >= C.MAX_STEPS:
        status, status_col = "TIMEOUT", COL_DIM
    elif not playing:
        status, status_col = "PAUSED", (200, 160, 60)
    else:
        status, status_col = "running", COL_TEXT

    auto_str = (f"  |  next in {pause_timer // 1000 + 1}s"
                if pause_timer > 0 and playing else "")

    line1 = (f"Scenario {idx}/{total}:  {scenario.name}"
             f"   |   Tick {s.tick}/{C.MAX_STEPS}"
             f"   |   {status}{auto_str}")
    line2 = (f"Last action: {app.last_action or '—'}"
             f"   |   Plan: {app.last_plan_type or '—'}"
             f"   |   Missiles: {len(s.missiles)}")

    screen.blit(font.render(line1, True, status_col),    (10, info_y + 6))
    screen.blit(font_small.render(line2, True, COL_DIM), (10, info_y + 26))

    for btn in ctrl_btns:
        active = (btn.label == "Pause" and not playing) or \
                 (btn.label == "Play"  and playing)
        btn.draw(screen, font_small, active=active)

    for i, sbtn in enumerate(scen_btns):
        sbtn.draw(screen, font_small, active=(i == app.scenario_index))


def build_buttons(ctrl_y: int) -> tuple[list[Button], list[Button]]:
    bw, bh = 74, 28
    gap    = 6
    pad    = 10
    cy     = ctrl_y + (CTRL_HEIGHT - bh) // 2

    ctrl_btns = [
        Button(pygame.Rect(pad + 0*(bw+gap), cy, bw, bh), "Play",  "Spc"),
        Button(pygame.Rect(pad + 1*(bw+gap), cy, bw, bh), "Pause", "Spc"),
        Button(pygame.Rect(pad + 2*(bw+gap), cy, bw, bh), "Prev",  "←"),
        Button(pygame.Rect(pad + 3*(bw+gap), cy, bw, bh), "Next",  "→"),
    ]
    sw        = 28
    sx        = pad + 4*(bw+gap) + 14
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
    font_small = pygame.font.SysFont("monospace", 12)
    font_label = pygame.font.SysFont("monospace", 11, bold=True)

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

        # ---- events --------------------------------------------------------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    playing = not playing
                elif event.key == pygame.K_RIGHT:
                    app.advance_to_next_scenario()
                    pause_timer = 0
                elif event.key == pygame.K_LEFT:
                    app.load_scenario(app.scenario_index - 1)
                    pause_timer = 0
                elif pygame.K_1 <= event.key <= pygame.K_9:
                    i = event.key - pygame.K_1
                    if i < len(app.scenarios):
                        app.load_scenario(i)
                        pause_timer = 0

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if ctrl_btns[0].is_clicked(mouse_pos):
                    playing = True
                elif ctrl_btns[1].is_clicked(mouse_pos):
                    playing = False
                elif ctrl_btns[2].is_clicked(mouse_pos):
                    app.load_scenario(app.scenario_index - 1)
                    pause_timer = 0
                elif ctrl_btns[3].is_clicked(mouse_pos):
                    app.advance_to_next_scenario()
                    pause_timer = 0
                else:
                    for i, sbtn in enumerate(scen_btns):
                        if sbtn.is_clicked(mouse_pos) and i < len(app.scenarios):
                            app.load_scenario(i)
                            pause_timer = 0
                            break

        # ---- simulation + auto-advance -------------------------------------
        if playing:
            if pause_timer > 0:
                # ms-accurate countdown using clock's reported frame time
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

        draw_entity(screen, app.state.aircraft.position,  COL_AIRCRAFT, "A", font_label)
        draw_entity(screen, app.state.sam_truck.position, COL_TRUCK,    "T", font_label)

        for m in app.state.missiles:
            if m.active:
                draw_entity(screen, m.position, COL_MISSILE, "M", font_label)

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