from __future__ import annotations
import math
import time

from core.grid import Grid
from core.vector import Vector2D
from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from entities.aircraft import Aircraft
from entities.sam_truck import SAMTruck
from game.state import GameState
from game.scenarios import Scenario, SCENARIOS
from game import constants as C
from systems.movement_system import move_entity
from systems.target_predictor import ConstantVelocityPredictor
from systems.launch_system import (
    TruckPlan,
    PlannerDiagnostic,
    find_best_truck_plan,
    validate_plan,
    launch_missile_in_direction,
)
from systems.collision_system import check_interception

_STALL_LIMIT     = 16
# Ticks between forced full replans.
# During move/wait execution the plan is kept alive across ticks;
# a full replan is only forced when this interval expires or the plan
# is invalidated.
_REPLAN_INTERVAL = 4


def _fmt_plan(label: str, plan: TruckPlan | None) -> str:
    if plan is None:
        return f"  {label:<28s}: no solution"
    md = (f"({plan.move_direction.x:.2f},{plan.move_direction.y:.2f})"
          if plan.move_direction else "NONE")
    fd = f"({plan.fire_direction.x:.2f},{plan.fire_direction.y:.2f})"
    return (f"  {label:<28s}: "
            f"move={md}×{plan.move_steps}  wait={plan.wait_steps}  "
            f"fire={fd}  ft={plan.fire_tick}  it={plan.intercept_tick}")


class App:
    def __init__(self):
        self.scenarios:      list[Scenario] = SCENARIOS
        self.scenario_index: int            = 0
        self.last_action:    str            = ""
        self.last_plan_type: str            = ""
        self._stall_ticks:   int            = 0

        # Radar inference
        self.inferred_direction: DirectionVector | None = None
        self.inferred_speed:     float | None           = None

        # Plan persistence
        self.active_plan:          TruckPlan | None     = None
        self._ticks_since_replan:  int                  = 0

        # Performance + diagnostics (exposed to main.py)
        self.last_planner_ms: float             = 0.0
        self.last_step_ms:    float             = 0.0
        self.last_diag:       PlannerDiagnostic = PlannerDiagnostic()

        # Predictor — swap to SingleTurnPredictor here when player logic arrives
        self._predictor = ConstantVelocityPredictor()

        self.state: GameState = self._build_state(self.scenarios[0])

    # ------------------------------------------------------------------
    # Scenario management
    # ------------------------------------------------------------------

    def _build_state(self, scenario: Scenario) -> GameState:
        grid   = Grid(width=C.GRID_WIDTH, height=C.GRID_HEIGHT)
        ac_dir = nearest_direction(
            math.cos(scenario.aircraft_dir_angle),
            math.sin(scenario.aircraft_dir_angle),
        )
        aircraft  = Aircraft(
            position=Vector2D(scenario.aircraft_pos.x, scenario.aircraft_pos.y),
            speed=C.AIRCRAFT_SPEED,
            direction=ac_dir,
        )
        sam_truck = SAMTruck(
            position=Vector2D(scenario.truck_pos.x, scenario.truck_pos.y),
            speed=C.TRUCK_SPEED,
            direction=DIRECTIONS[0],
        )
        return GameState(grid=grid, aircraft=aircraft, sam_truck=sam_truck)

    def load_scenario(self, index: int) -> None:
        self.scenario_index       = index % len(self.scenarios)
        self.state                = self._build_state(self.scenarios[self.scenario_index])
        self.last_action          = ""
        self.last_plan_type       = ""
        self._stall_ticks         = 0
        self.inferred_direction   = None
        self.inferred_speed       = None
        self.active_plan          = None
        self._ticks_since_replan  = 0
        self.last_planner_ms      = 0.0
        self.last_step_ms         = 0.0
        self.last_diag            = PlannerDiagnostic()
        print(f"\n{'='*64}")
        print(f"  Scenario {self.scenario_index+1}/{len(self.scenarios)}: "
              f"{self.scenarios[self.scenario_index].name}")
        print(f"{'='*64}")

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

    # ------------------------------------------------------------------
    # Plan persistence logic
    # ------------------------------------------------------------------

    def _plan_is_stale(self) -> bool:
        """
        Return True only when there is a genuine reason to throw away the
        active plan and compute a new one.

        Reasons to replan:
          1. No active plan exists.
          2. The replan interval has elapsed (periodic refresh).
          3. The active plan is fire_now and cheap validation says the shot
             is no longer valid from the current truck position.

        For move/wait plans we do NOT validate cheaply every tick — the plan
        was computed with awareness of future truck positions, so minor
        aircraft position drift between replan intervals is acceptable.
        A forced replan at _REPLAN_INTERVAL catches any real drift.
        """
        if self.active_plan is None:
            return True

        if self._ticks_since_replan >= _REPLAN_INTERVAL:
            return True

        # Cheap per-tick check only for fire_now
        if (self.active_plan.plan_type == "fire_now"
                and self.inferred_direction is not None
                and self.inferred_speed is not None):
            still_valid = validate_plan(
                self.active_plan,
                self.state.sam_truck,
                Vector2D(self.state.aircraft.position.x,
                         self.state.aircraft.position.y),
                self.inferred_direction,
                self.inferred_speed,
                C.MISSILE_SPEED,
                C.DT,
                self.state.grid,
                C.PLANNING_HORIZON,
                self._predictor,
            )
            if not still_valid:
                return True

        return False

    # ------------------------------------------------------------------
    # Full replan
    # ------------------------------------------------------------------

    def _replan(self) -> TruckPlan | None:
        s     = self.state
        truck = s.sam_truck

        t0 = time.perf_counter()

        fire_now, wait_plan, move_plan, mwf_plan, best, diag = find_best_truck_plan(
            truck=truck,
            aircraft_pos=Vector2D(s.aircraft.position.x, s.aircraft.position.y),
            aircraft_dir=self.inferred_direction,
            aircraft_speed=self.inferred_speed,
            truck_speed=C.TRUCK_SPEED,
            missile_speed=C.MISSILE_SPEED,
            dt=C.DT,
            grid=s.grid,
            max_future_steps=C.PLANNING_HORIZON,
            predictor=self._predictor,
        )

        self.last_planner_ms     = (time.perf_counter() - t0) * 1000.0
        self.last_diag           = diag
        self._ticks_since_replan = 0

        print("  --- Planner ---")
        print(_fmt_plan("fire_now",                fire_now))
        print(_fmt_plan("wait_then_fire",           wait_plan))
        print(_fmt_plan("move_then_fire",           move_plan))
        print(_fmt_plan("move_then_wait_then_fire", mwf_plan))

        if best is None:
            print(f"  selected: NONE  [{diag.no_solution_reason}]")
            self.last_plan_type = "none"
        else:
            print(f"  selected: [{best.plan_type}]  "
                  f"ft={best.fire_tick}  it={best.intercept_tick}")
            self.last_plan_type = best.plan_type

        print(f"  diag: cands={diag.candidates_evaluated}  "
              f"verified={diag.directions_verified}  "
              f"fallback={diag.fallback_used}  "
              f"planner={self.last_planner_ms:.1f}ms")

        return best

    # ------------------------------------------------------------------
    # Stall detection
    # ------------------------------------------------------------------

    def _update_stall(self, had_plan: bool) -> None:
        s   = self.state
        pos = s.aircraft.position
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
    # Single simulation tick
    # ------------------------------------------------------------------

    def run_step(self) -> None:
        t_step = time.perf_counter()
        s      = self.state
        truck  = s.sam_truck

        s.tick += 1

        # 1. Radar
        self._update_radar()

        # 2. Truck decision
        best: TruckPlan | None = None

        if not truck.has_fired:
            if self.inferred_direction is None:
                self.last_action = "waiting for radar lock"
                print(f"  ACTION: waiting for radar lock (tick {s.tick})")
            else:
                # Replan only when genuinely stale
                if self._plan_is_stale():
                    self.active_plan = self._replan()
                else:
                    self._ticks_since_replan += 1
                    ap = self.active_plan
                    print(f"  [plan reuse {self._ticks_since_replan}/{_REPLAN_INTERVAL} "
                          f"type={ap.plan_type if ap else 'none'} "
                          f"ft={ap.fire_tick if ap else '?'}]")

                best = self.active_plan

                if best is not None:
                    if best.move_steps > 0:
                        truck.direction = best.move_direction
                        move_entity(truck, C.DT, s.grid, state=s)
                        self.last_action = (
                            f"move ({best.move_direction.x:.2f},"
                            f"{best.move_direction.y:.2f})"
                        )
                        # Decrement move_steps; preserve rest of plan
                        self.active_plan = TruckPlan(
                            best.plan_type,
                            best.move_direction,
                            best.move_steps - 1,
                            best.wait_steps,
                            best.fire_direction,
                            best.missile_steps,
                            max(0, best.fire_tick - 1),
                            max(0, best.intercept_tick - 1),
                        )
                        print(f"  ACTION: move  ({best.move_steps} steps left)")

                    elif best.wait_steps > 0:
                        self.last_action = "wait"
                        self.active_plan = TruckPlan(
                            best.plan_type,
                            best.move_direction,
                            0,
                            best.wait_steps - 1,
                            best.fire_direction,
                            best.missile_steps,
                            max(0, best.fire_tick - 1),
                            max(0, best.intercept_tick - 1),
                        )
                        print(f"  ACTION: wait  ({best.wait_steps} steps left)")

                    else:
                        missile = launch_missile_in_direction(
                            truck, C.MISSILE_SPEED, best.fire_direction,
                        )
                        if missile is not None:
                            s.missiles.append(missile)
                            self.last_action = (
                                f"FIRED ({best.fire_direction.x:.2f},"
                                f"{best.fire_direction.y:.2f})"
                            )
                            print(f"  ACTION: >>> FIRED  tick={s.tick}")
                        self.active_plan = None
                else:
                    self.last_action = "idle"
                    print("  ACTION: idle (no plan)")

        # 3. Move aircraft
        move_entity(s.aircraft, C.DT, s.grid, state=s)

        # 4. Move missiles
        for missile in s.missiles:
            move_entity(missile, C.DT, s.grid, state=s)

        # 5. Collision check
        if check_interception(s.aircraft, s.missiles, s.grid):
            s.intercepted = True

        # 6. Stall check
        self._update_stall(had_plan=(best is not None))

        self.last_step_ms = (time.perf_counter() - t_step) * 1000.0

        idir = (f"({self.inferred_direction.x:.2f},{self.inferred_direction.y:.2f})"
                if self.inferred_direction else "UNK")
        print(f"[Tick {s.tick:>3}]  AC={s.aircraft.position}  idir={idir}"
              f"  truck={truck.position}  fired={truck.has_fired}"
              f"  step={self.last_step_ms:.1f}ms  plan={self.last_planner_ms:.1f}ms")
        for i, m in enumerate(s.missiles):
            print(f"  M{i}: {m.position}  active={m.active}")
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