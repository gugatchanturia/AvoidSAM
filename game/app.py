from core.grid import Grid
from core.vector import Vector2D
from core.directions import Direction
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

# How many ticks the aircraft must be stationary-at-border with no plan
# before we declare the scenario a dead end.
_STALL_LIMIT = 12


def _fmt_plan(label: str, plan: TruckPlan | None) -> str:
    if plan is None:
        return f"  {label:<26s}: no solution"
    return (
        f"  {label:<26s}: "
        f"move={plan.move_direction.name if plan.move_direction else 'NONE'} x{plan.move_steps}  "
        f"wait={plan.wait_steps}  "
        f"fire_dir={plan.fire_direction.name:<2s}  "
        f"fire_tick={plan.fire_tick:<3d}  "
        f"intercept_tick={plan.intercept_tick}"
    )


def _plan_sort_key(plan: TruckPlan, current_dir: Direction) -> tuple:
    """
    Primary priority  : earliest intercept_tick
    Secondary         : earlier fire_tick
    Tertiary          : fewer move_steps
    Stability penalty : penalise direction change with a weight of 0.5 ticks.
                        A new direction is only chosen when it beats the current
                        direction by MORE than 0.5 effective ticks on the primary
                        criterion — implemented by adding 1 to intercept_tick when
                        the first-move direction differs from the truck's current
                        direction.  This is strong enough to suppress jitter but
                        never overrides a genuine 1-tick improvement.
    """
    direction_penalty = (
        0
        if (plan.move_direction is None or plan.move_direction == current_dir)
        else 1
    )
    return (
        plan.intercept_tick + direction_penalty,
        plan.fire_tick,
        plan.move_steps,
        direction_penalty,
    )


class App:
    def __init__(self):
        self.scenarios: list[Scenario] = SCENARIOS
        self.scenario_index: int = 0
        self.last_action: str = ""
        self.last_plan_type: str = ""
        # stall detection
        self._stall_ticks: int = 0
        self.state: GameState = self._build_state(self.scenarios[0])

    # ------------------------------------------------------------------
    # Scenario management
    # ------------------------------------------------------------------

    def _build_state(self, scenario: Scenario) -> GameState:
        grid = Grid(width=C.GRID_WIDTH, height=C.GRID_HEIGHT)
        aircraft = Aircraft(
            position=Vector2D(scenario.aircraft_pos.x, scenario.aircraft_pos.y),
            speed=C.AIRCRAFT_SPEED,
            direction=scenario.aircraft_dir,
        )
        sam_truck = SAMTruck(
            position=Vector2D(scenario.truck_pos.x, scenario.truck_pos.y),
            speed=C.TRUCK_SPEED,
            direction=scenario.truck_dir,
        )
        return GameState(grid=grid, aircraft=aircraft, sam_truck=sam_truck)

    def load_scenario(self, index: int) -> None:
        self.scenario_index = index % len(self.scenarios)
        self.state = self._build_state(self.scenarios[self.scenario_index])
        self.last_action = ""
        self.last_plan_type = ""
        self._stall_ticks = 0
        print(f"\n{'='*60}")
        print(f"  Scenario {self.scenario_index + 1}/{len(self.scenarios)}: "
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
    # Planner
    # ------------------------------------------------------------------

    def _replan(self) -> TruckPlan | None:
        s     = self.state
        truck = s.sam_truck

        fire_now, wait_plan, move_plan, mwf_plan, _ = find_best_truck_plan(
            truck=truck,
            aircraft=s.aircraft,
            truck_speed=C.TRUCK_SPEED,
            missile_speed=C.MISSILE_SPEED,
            dt=C.DT,
            grid=s.grid,
            max_future_steps=C.PLANNING_HORIZON,
        )

        candidates = [p for p in (fire_now, wait_plan, move_plan, mwf_plan) if p is not None]
        best = (
            min(candidates, key=lambda p: _plan_sort_key(p, truck.direction))
            if candidates else None
        )

        print("  --- Planner ---")
        print(_fmt_plan("fire_now",                fire_now))
        print(_fmt_plan("wait_then_fire",           wait_plan))
        print(_fmt_plan("move_then_fire",           move_plan))
        print(_fmt_plan("move_then_wait_then_fire", mwf_plan))

        if best is None:
            print("  selected                  : NONE")
            self.last_plan_type = "none"
        else:
            print(
                f"  selected                  : [{best.plan_type}]  "
                f"fire_tick={best.fire_tick}  intercept_tick={best.intercept_tick}"
            )
            self.last_plan_type = best.plan_type

        return best

    # ------------------------------------------------------------------
    # Stall detection
    # ------------------------------------------------------------------

    def _update_stall(self, had_plan: bool) -> None:
        """
        Increment the stall counter when the aircraft is at the grid border
        AND the planner found no solution.  Reset it otherwise.
        """
        s  = self.state
        ac = s.aircraft
        tx, ty = s.grid.to_tile(ac.position)
        at_border = (
            tx <= 0 or ty <= 0 or
            tx >= s.grid.width  - 1 or
            ty >= s.grid.height - 1
        )

        if at_border and not had_plan:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0

        if self._stall_ticks >= _STALL_LIMIT:
            s.failed = True
            print(f"  *** SCENARIO FAILED — aircraft border-stall, no solution ***")

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    def run_step(self) -> None:
        s     = self.state
        truck = s.sam_truck

        s.tick += 1

        best: TruckPlan | None = None

        if not truck.has_fired:
            best = self._replan()

            if best is not None:
                if best.move_steps > 0:
                    truck.direction = best.move_direction
                    move_entity(truck, C.DT, s.grid)
                    self.last_action = f"move {best.move_direction.name}"
                    print(
                        f"  ACTION: move {best.move_direction.name}  "
                        f"(plan needs {best.move_steps} move steps total)"
                    )
                elif best.wait_steps > 0:
                    self.last_action = "wait"
                    print(f"  ACTION: wait  (plan needs {best.wait_steps} wait steps total)")
                else:
                    missile = launch_missile_in_direction(
                        truck, C.MISSILE_SPEED, best.fire_direction,
                    )
                    if missile is not None:
                        s.missiles.append(missile)
                        self.last_action = f"FIRED {best.fire_direction.name}"
                        print(f"  ACTION: >>> FIRED  dir={best.fire_direction.name}  tick={s.tick}")
            else:
                self.last_action = "idle"
                print("  ACTION: idle (no plan found)")

        move_entity(s.aircraft, C.DT, s.grid)
        for missile in s.missiles:
            move_entity(missile, C.DT, s.grid)

        if check_interception(s.aircraft, s.missiles, s.grid):
            s.intercepted = True

        self._update_stall(had_plan=(best is not None))

        ac_tile    = s.aircraft.tile(s.grid)
        truck_tile = truck.tile(s.grid)
        print(f"[Tick {s.tick:>3}]")
        print(f"  Aircraft : pos={s.aircraft.position}  tile={ac_tile}")
        print(f"  Truck    : pos={truck.position}  tile={truck_tile}  fired={truck.has_fired}")
        for i, m in enumerate(s.missiles):
            print(f"  M{i}       : pos={m.position}  tile={m.tile(s.grid)}  active={m.active}")
        if s.intercepted:
            print(f"\n*** INTERCEPTED at tick {s.tick}! ***\n")

    # ------------------------------------------------------------------
    # Console-only runner
    # ------------------------------------------------------------------

    def run(self) -> None:
        for idx in range(len(self.scenarios)):
            self.load_scenario(idx)
            for _ in range(C.MAX_STEPS):
                self.run_step()
                if self.scenario_finished():
                    break