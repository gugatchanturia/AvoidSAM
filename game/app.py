from __future__ import annotations
import math

from core.grid import Grid
from core.vector import Vector2D
from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from entities.aircraft import Aircraft
from entities.sam_truck import SAMTruck
from game.state import GameState
from game.scenarios import Scenario, SCENARIOS
from game import constants as C
from systems.movement_system import move_entity
from systems.launch_system import (
    TruckPlan,
    find_best_truck_plan,
    launch_missile_in_direction,
)
from systems.collision_system import check_interception

_STALL_LIMIT = 16


def _fmt_plan(label: str, plan: TruckPlan | None) -> str:
    if plan is None:
        return f"  {label:<26s}: no solution"
    md = f"({plan.move_direction.x:.2f},{plan.move_direction.y:.2f})" if plan.move_direction else "NONE"
    fd = f"({plan.fire_direction.x:.2f},{plan.fire_direction.y:.2f})"
    return (
        f"  {label:<26s}: "
        f"move={md} x{plan.move_steps}  "
        f"wait={plan.wait_steps}  "
        f"fire_dir={fd}  "
        f"fire_tick={plan.fire_tick:<3d}  "
        f"intercept_tick={plan.intercept_tick}"
    )


class App:
    def __init__(self):
        self.scenarios:       list[Scenario] = SCENARIOS
        self.scenario_index:  int            = 0
        self.last_action:     str            = ""
        self.last_plan_type:  str            = ""
        self._stall_ticks:    int            = 0

        # Radar-inferred values (updated each tick, exposed for display)
        self.inferred_direction: DirectionVector | None = None
        self.inferred_speed:     float | None           = None

        self.state: GameState = self._build_state(self.scenarios[0])

    # ------------------------------------------------------------------
    # Scenario management
    # ------------------------------------------------------------------

    def _build_state(self, scenario: Scenario) -> GameState:
        grid = Grid(width=C.GRID_WIDTH, height=C.GRID_HEIGHT)

        ac_dir = nearest_direction(
            math.cos(scenario.aircraft_dir_angle),
            math.sin(scenario.aircraft_dir_angle),
        )
        aircraft = Aircraft(
            position=Vector2D(scenario.aircraft_pos.x, scenario.aircraft_pos.y),
            speed=C.AIRCRAFT_SPEED,
            direction=ac_dir,
        )

        truck_dir = DIRECTIONS[0]
        sam_truck = SAMTruck(
            position=Vector2D(scenario.truck_pos.x, scenario.truck_pos.y),
            speed=C.TRUCK_SPEED,
            direction=truck_dir,
        )
        return GameState(grid=grid, aircraft=aircraft, sam_truck=sam_truck)

    def load_scenario(self, index: int) -> None:
        self.scenario_index  = index % len(self.scenarios)
        self.state           = self._build_state(self.scenarios[self.scenario_index])
        self.last_action     = ""
        self.last_plan_type  = ""
        self._stall_ticks    = 0
        self.inferred_direction = None
        self.inferred_speed     = None
        print(f"\n{'='*60}")
        print(f"  Scenario {self.scenario_index+1}/{len(self.scenarios)}: "
              f"{self.scenarios[self.scenario_index].name}")
        print(f"{'='*60}")

    def advance_to_next_scenario(self) -> None:
        self.load_scenario(self.scenario_index + 1)

    def scenario_finished(self) -> bool:
        s = self.state
        return s.intercepted or s.tick >= C.MAX_STEPS or s.failed

    @property
    def current_scenario(self) -> Scenario:
        return self.scenarios[self.scenario_index]

    # ------------------------------------------------------------------
    # Radar inference
    # ------------------------------------------------------------------

    def _update_radar(self) -> None:
        s   = self.state
        pos = Vector2D(s.aircraft.position.x, s.aircraft.position.y)
        s.aircraft_history.append(pos)

        if len(s.aircraft_history) >= 2:
            p0    = s.aircraft_history[-2]
            p1    = s.aircraft_history[-1]
            delta = p1 - p0
            spd   = delta.length() / C.DT
            if spd > 1e-6:
                norm                    = delta.normalized()
                self.inferred_direction = nearest_direction(norm.x, norm.y)
                self.inferred_speed     = spd
            # if aircraft is stationary keep previous estimate

    # ------------------------------------------------------------------
    # Planner
    # ------------------------------------------------------------------

    def _replan(self) -> TruckPlan | None:
        s     = self.state
        truck = s.sam_truck

        fire_now, wait_plan, move_plan, mwf_plan, best = find_best_truck_plan(
            truck=truck,
            aircraft_pos=Vector2D(s.aircraft.position.x, s.aircraft.position.y),
            aircraft_dir=self.inferred_direction,
            aircraft_speed=self.inferred_speed,
            truck_speed=C.TRUCK_SPEED,
            missile_speed=C.MISSILE_SPEED,
            dt=C.DT,
            grid=s.grid,
            max_future_steps=C.PLANNING_HORIZON,
        )

        print("  --- Planner ---")
        print(_fmt_plan("fire_now",                fire_now))
        print(_fmt_plan("wait_then_fire",           wait_plan))
        print(_fmt_plan("move_then_fire",           move_plan))
        print(_fmt_plan("move_then_wait_then_fire", mwf_plan))

        if best is None:
            print("  selected: NONE")
            self.last_plan_type = "none"
        else:
            print(f"  selected: [{best.plan_type}]  "
                  f"fire_tick={best.fire_tick}  intercept_tick={best.intercept_tick}")
            self.last_plan_type = best.plan_type

        return best

    # ------------------------------------------------------------------
    # Stall detection
    # ------------------------------------------------------------------

    def _update_stall(self, had_plan: bool) -> None:
        s    = self.state
        pos  = s.aircraft.position
        at_border = (
            pos.x <= 0.05 or pos.y <= 0.05 or
            pos.x >= s.grid.width  - 0.05 or
            pos.y >= s.grid.height - 0.05
        )
        if at_border and not had_plan:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0

        if self._stall_ticks >= _STALL_LIMIT:
            s.failed = True
            print("  *** SCENARIO FAILED — border stall, no solution ***")

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    def run_step(self) -> None:
        s     = self.state
        truck = s.sam_truck

        s.tick += 1

        # 1. Update radar (observe current aircraft position)
        self._update_radar()

        # 2. Truck decision (only if radar has at least 2 observations)
        best: TruckPlan | None = None

        if not truck.has_fired:
            if self.inferred_direction is not None:
                best = self._replan()

                if best is not None:
                    if best.move_steps > 0:
                        truck.direction = best.move_direction
                        move_entity(truck, C.DT, s.grid, state=s)
                        self.last_action = f"move ({best.move_direction.x:.2f},{best.move_direction.y:.2f})"
                        print(f"  ACTION: move  (plan needs {best.move_steps} steps total)")
                    elif best.wait_steps > 0:
                        self.last_action = "wait"
                        print(f"  ACTION: wait  (plan needs {best.wait_steps} wait steps total)")
                    else:
                        missile = launch_missile_in_direction(truck, C.MISSILE_SPEED, best.fire_direction)
                        if missile is not None:
                            s.missiles.append(missile)
                            self.last_action = f"FIRED ({best.fire_direction.x:.2f},{best.fire_direction.y:.2f})"
                            print(f"  ACTION: >>> FIRED  tick={s.tick}")
                else:
                    self.last_action = "idle"
                    print("  ACTION: idle (no plan found)")
            else:
                self.last_action = "waiting for radar lock"
                print("  ACTION: waiting for radar lock (tick 1)")

        # 3. Move aircraft
        move_entity(s.aircraft, C.DT, s.grid, state=s)

        # 4. Move missiles
        for missile in s.missiles:
            move_entity(missile, C.DT, s.grid, state=s)

        # 5. Collision
        if check_interception(s.aircraft, s.missiles, s.grid):
            s.intercepted = True

        # 6. Stall detection
        self._update_stall(had_plan=(best is not None))

        # 7. Console debug
        print(f"[Tick {s.tick:>3}]  "
              f"AC={s.aircraft.position}  "
              f"inferred_dir={'({:.2f},{:.2f})'.format(self.inferred_direction.x, self.inferred_direction.y) if self.inferred_direction else 'UNKNOWN'}  "
              f"inferred_spd={'{:.2f}'.format(self.inferred_speed) if self.inferred_speed else 'UNKNOWN'}  "
              f"truck={truck.position}  fired={truck.has_fired}")
        for i, m in enumerate(s.missiles):
            print(f"  M{i}: pos={m.position}  active={m.active}")
        if s.intercepted:
            print(f"\n*** INTERCEPTED at tick {s.tick}! ***\n")

    # ------------------------------------------------------------------
    # Console runner
    # ------------------------------------------------------------------

    def run(self) -> None:
        for idx in range(len(self.scenarios)):
            self.load_scenario(idx)
            for _ in range(C.MAX_STEPS):
                self.run_step()
                if self.scenario_finished():
                    break