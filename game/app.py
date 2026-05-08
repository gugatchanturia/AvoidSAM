from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, replace

from core.grid import Grid
from core.vector import Vector2D
from core.directions import DirectionVector, DIRECTIONS, nearest_direction
from entities.aircraft import Aircraft
from entities.sam_truck import SAMTruck
from game.state import GameState
from game.scenarios import Scenario, SCENARIOS
from game import constants as C
from game.pva_rules import (
    ESCAPE_DEBUG_LAST,
    all_border_tiles,
    crossed_exit_tile,
    exit_stripe_half_for_pva,
    explain_legal_turn_empty,
    is_border_tile,
    legal_launch_directions,
    legal_turn_directions,
    predictor_post_turn_candidates,
    project_valid_exit_tiles,
    tile_pos,
)
from systems.movement_system import move_entity
from systems.target_predictor import ConstantVelocityPredictor, TurnAwarePredictor
from systems.launch_system import (
    TruckPlan,
    PlannerDiagnostic,
    find_best_truck_plan,
    validate_plan,
    launch_missile_in_direction,
)
from systems.collision_system import check_interception

_STALL_LIMIT = 16
_REPLAN_INTERVAL = 4


@dataclass(frozen=True)
class PVAPreview:
    tile: tuple[int, int] | None
    direction: DirectionVector | None
    exit_tiles: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class TrailSegment:
    kind: str
    start: Vector2D
    end: Vector2D
    tick: int


class App:
    MODE_MENU = "menu"
    MODE_AUTOMATIC = "automatic"
    MODE_PVA = "pva"

    PVA_TILE_SELECT = "tile_select"
    PVA_ANGLE_SELECT = "angle_select"
    PVA_CONFIRM = "confirm"
    PVA_RUNNING = "running"
    PVA_END = "end"

    def __init__(self):
        self.scenarios: list[Scenario] = SCENARIOS
        self.scenario_index: int = 0

        self.mode: str = self.MODE_MENU
        self.last_action: str = ""
        self.last_plan_type: str = ""
        self._stall_ticks: int = 0

        self.inferred_direction: DirectionVector | None = None
        self.inferred_speed: float | None = None

        self.active_plan: TruckPlan | None = None
        self._ticks_since_replan: int = 0

        self.last_planner_ms: float = 0.0
        self.last_step_ms: float = 0.0
        self.last_diag: PlannerDiagnostic = PlannerDiagnostic()
        self._predictor = ConstantVelocityPredictor()

        self.state: GameState = self._build_auto_state(self.scenarios[0])

        # PVA-specific state
        self.pva_phase: str = self.PVA_TILE_SELECT
        self.pva_hover_tile: tuple[int, int] | None = None
        self.pva_locked_tile: tuple[int, int] | None = None
        self.pva_valid_launch_dirs: list[DirectionVector] = []
        self.pva_hover_direction: DirectionVector | None = None
        self.pva_locked_direction: DirectionVector | None = None
        self.pva_hover_exit_tiles: set[tuple[int, int]] = set()
        self.pva_locked_exit_tiles: set[tuple[int, int]] = set()
        self.pva_exit_stripe_half: int = 0
        # Player UI: discrete headings offered on the turn wheel (often all DIRECTIONS).
        self.pva_player_turn_dirs: list[DirectionVector] = []
        # SAM / predictor: only directions that can still exit via locked tiles (winning futures).
        self.pva_sam_threat_turn_dirs: list[DirectionVector] = []
        self._dbg_turn_fan_key: object | None = None
        self._pva_logged_outside_turn_fan: bool = False
        self._pva_turn_ptr: tuple[float, float] = (1.0, 0.0)
        self._pva_last_sam_turn_dirs_count: int = 0
        self.pva_turn_used: bool = False
        self.pva_turn_hover_direction: DirectionVector | None = None
        self.pva_result_text: str = ""

        # Visual-only: persistent movement trail segments (cleared per run)
        self.movement_trails: list[TrailSegment] = []

    # ------------------------------------------------------------------
    # Builders / mode entry
    # ------------------------------------------------------------------

    def _build_auto_state(self, scenario: Scenario) -> GameState:
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
        sam_truck = SAMTruck(
            position=Vector2D(scenario.truck_pos.x, scenario.truck_pos.y),
            speed=C.TRUCK_SPEED,
            direction=DIRECTIONS[0],
        )
        return GameState(grid=grid, aircraft=aircraft, sam_truck=sam_truck)

    def _build_pva_state(self) -> GameState:
        grid = Grid(width=C.GRID_WIDTH, height=C.GRID_HEIGHT)
        placeholder_aircraft = Aircraft(
            position=Vector2D(-99.0, -99.0),
            speed=C.AIRCRAFT_SPEED,
            direction=DIRECTIONS[0],
        )
        cx, cy = grid.width // 2, grid.height // 2
        if C.RANDOMIZE_SAM_SPAWN:
            trx = random.randint(cx - C.SAM_SPAWN_HALF, cx + C.SAM_SPAWN_HALF)
            tr_y = random.randint(cy - C.SAM_SPAWN_HALF, cy + C.SAM_SPAWN_HALF)
            trx = max(0, min(grid.width - 1, trx))
            tr_y = max(0, min(grid.height - 1, tr_y))
            tp = Vector2D(float(trx), float(tr_y))
        else:
            tp = Vector2D(float(cx), float(cy))
        sam_truck = SAMTruck(position=tp, speed=C.TRUCK_SPEED, direction=DIRECTIONS[0])
        return GameState(grid=grid, aircraft=placeholder_aircraft, sam_truck=sam_truck)

    def _advance_plan_tick_fields(self, best: TruckPlan) -> TruckPlan:
        pit = (
            best.primary_intercept_tick
            if best.primary_intercept_tick >= 10**9
            else max(0, best.primary_intercept_tick - 1)
        )
        return replace(
            best,
            fire_tick=max(0, best.fire_tick - 1),
            intercept_tick=max(0, best.intercept_tick - 1),
            primary_intercept_tick=pit,
        )

    def _refresh_predictor(self) -> None:
        """Automatic: CV. PVA while turn unused + within window: turn-aware vs valid exits."""
        self._predictor = ConstantVelocityPredictor()
        if self.mode != self.MODE_PVA or self.pva_phase != self.PVA_RUNNING:
            return

        if self.pva_turn_used:
            self._predictor = ConstantVelocityPredictor()
            return

        s = self.state
        turn_max_remaining = max(0, C.TURN_WINDOW_MAX - s.tick)
        if s.tick > C.TURN_WINDOW_MAX or turn_max_remaining <= 0:
            self._predictor = ConstantVelocityPredictor()
            return

        spd = float(self.inferred_speed) if self.inferred_speed is not None else float(C.AIRCRAFT_SPEED)

        vx = frozenset(self.pva_locked_exit_tiles)
        ac = s.aircraft
        pos_v = Vector2D(ac.position.x, ac.position.y)
        plan_heading = self.inferred_direction if self.inferred_direction is not None else ac.direction

        if C.PVA_SAM_CONSIDER_ONLY_WINNING_TURNS:
            dirs = predictor_post_turn_candidates(
                grid=s.grid,
                position=pos_v,
                current_heading=plan_heading,
                valid_exit_tiles=vx,
                speed=spd,
                dt=float(C.DT),
                max_primary=12,
                max_total=18,
                lookahead_steps=32,
                exit_stripe_half=self.pva_exit_stripe_half,
            )
        else:
            dirs = list(DIRECTIONS)
        self._pva_last_sam_turn_dirs_count = len(dirs)
        turn_min_remaining = max(0, C.TURN_WINDOW_MIN - s.tick)

        self._predictor = TurnAwarePredictor(
            turn_min_remaining=turn_min_remaining,
            turn_max_remaining=turn_max_remaining,
            valid_exit_tiles=vx,
            sam_post_turn_dirs=dirs,
        )

    def back_to_menu(self) -> None:
        self.mode = self.MODE_MENU
        self.last_action = ""
        self.last_plan_type = ""
        self.last_diag = PlannerDiagnostic()
        self.last_planner_ms = 0.0
        self.last_step_ms = 0.0

    def start_automatic_mode(self) -> None:
        self.mode = self.MODE_AUTOMATIC
        self.load_scenario(0)

    def start_pva_mode(self) -> None:
        self.mode = self.MODE_PVA
        self.restart_pva_round()

    def restart_pva_round(self) -> None:
        self.state = self._build_pva_state()
        self.last_action = "Select a border tile"
        self.last_plan_type = ""
        self._stall_ticks = 0
        self.inferred_direction = None
        self.inferred_speed = None
        self.active_plan = None
        self._ticks_since_replan = 0
        self.last_planner_ms = 0.0
        self.last_step_ms = 0.0
        self.last_diag = PlannerDiagnostic()
        self.pva_phase = self.PVA_TILE_SELECT
        self.pva_hover_tile = None
        self.pva_locked_tile = None
        self.pva_valid_launch_dirs = []
        self.pva_hover_direction = None
        self.pva_locked_direction = None
        self.pva_hover_exit_tiles = set()
        self.pva_locked_exit_tiles = set()
        self.pva_exit_stripe_half = 0
        self.pva_player_turn_dirs = []
        self.pva_sam_threat_turn_dirs = []
        self._dbg_turn_fan_key = None
        self._pva_logged_outside_turn_fan = False
        self._pva_turn_ptr = (1.0, 0.0)
        self._pva_last_sam_turn_dirs_count = 0
        self.pva_turn_used = False
        self.pva_turn_hover_direction = None
        self.pva_result_text = ""
        self.movement_trails = []

    # ------------------------------------------------------------------
    # Automatic scenario helpers
    # ------------------------------------------------------------------

    def load_scenario(self, index: int) -> None:
        self.scenario_index = index % len(self.scenarios)
        self.state = self._build_auto_state(self.scenarios[self.scenario_index])
        self.last_action = ""
        self.last_plan_type = ""
        self._stall_ticks = 0
        self.inferred_direction = None
        self.inferred_speed = None
        self.active_plan = None
        self._ticks_since_replan = 0
        self.last_planner_ms = 0.0
        self.last_step_ms = 0.0
        self.last_diag = PlannerDiagnostic()
        self._predictor = ConstantVelocityPredictor()
        self.movement_trails = []
        print(f"\n{'='*64}")
        print(
            f"  Scenario {self.scenario_index + 1}/{len(self.scenarios)}: "
            f"{self.scenarios[self.scenario_index].name}"
        )
        print(f"{'='*64}")

    def advance_to_next_scenario(self) -> None:
        self.load_scenario(self.scenario_index + 1)

    @property
    def current_scenario(self) -> Scenario:
        return self.scenarios[self.scenario_index]

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def scenario_finished(self) -> bool:
        s = self.state
        return s.intercepted or s.tick >= C.MAX_STEPS or s.failed or s.escaped

    @property
    def has_aircraft(self) -> bool:
        if self.mode != self.MODE_PVA:
            return True
        return self.pva_phase in (self.PVA_RUNNING, self.PVA_END)

    # ------------------------------------------------------------------
    # Jammer (PVA only)
    # ------------------------------------------------------------------

    def jammer_active(self) -> bool:
        if self.mode != self.MODE_PVA:
            return False
        if float(C.JAMMER_RADIUS) <= 0.0:
            return False
        if bool(C.JAMMER_ACTIVE_AFTER_FIRE):
            return True
        return not bool(self.state.sam_truck.has_fired)

    def jammer_distance_tiles(self) -> float | None:
        if self.mode != self.MODE_PVA:
            return None
        if self.pva_phase not in (self.PVA_RUNNING, self.PVA_END):
            return None
        ac_pos = self.state.aircraft.position
        truck_pos = self.state.sam_truck.position
        return ac_pos.distance_to(truck_pos)

    def aircraft_jammed(self) -> bool:
        if not self.jammer_active():
            return False
        dist = self.jammer_distance_tiles()
        if dist is None:
            return False
        return dist <= float(C.JAMMER_RADIUS) + 1e-9

    @property
    def pva_border_tiles(self) -> list[tuple[int, int]]:
        return all_border_tiles(self.state.grid)

    @property
    def pva_preview(self) -> PVAPreview:
        if self.pva_phase == self.PVA_ANGLE_SELECT:
            return PVAPreview(
                tile=self.pva_locked_tile,
                direction=self.pva_hover_direction,
                exit_tiles=frozenset(self.pva_hover_exit_tiles),
            )
        if self.pva_phase in (self.PVA_CONFIRM, self.PVA_RUNNING, self.PVA_END):
            return PVAPreview(
                tile=self.pva_locked_tile,
                direction=self.pva_locked_direction,
                exit_tiles=frozenset(self.pva_locked_exit_tiles),
            )
        return PVAPreview(tile=self.pva_hover_tile, direction=None, exit_tiles=frozenset())

    def _direction_name(self, d: DirectionVector | None) -> str:
        if d is None:
            return "UNK"
        return f"({d.x:.2f},{d.y:.2f})"

    def _pva_in_turn_window(self) -> bool:
        """Player may execute the one-turn only while tick is inside [TURN_WINDOW_MIN, TURN_WINDOW_MAX]."""
        return (
            self.mode == self.MODE_PVA
            and self.pva_phase == self.PVA_RUNNING
            and int(C.TURN_WINDOW_MIN)
            <= self.state.tick
            <= int(C.TURN_WINDOW_MAX)
        )

    def _recompute_pva_turn_fan(self) -> None:
        """Rebuild player turn choices vs SAM threat directions (call each tick while turn available)."""
        if self.mode != self.MODE_PVA or self.pva_phase != self.PVA_RUNNING:
            return
        if self.pva_turn_used:
            self.pva_player_turn_dirs = []
            self.pva_sam_threat_turn_dirs = []
            self.pva_turn_hover_direction = None
            return

        pdx, pdy = self._pva_turn_ptr

        if not self._pva_in_turn_window():
            self.pva_player_turn_dirs = []
            self.pva_sam_threat_turn_dirs = []
            self.pva_turn_hover_direction = None
            return

    def _fmt_plan(self, label: str, plan: TruckPlan | None) -> str:
        if plan is None:
            return f"  {label:<28s}: no solution"
        md = self._direction_name(plan.move_direction) if plan.move_direction else "NONE"
        fd = self._direction_name(plan.fire_direction)
        return (
            f"  {label:<28s}: move={md} x{plan.move_steps}  "
            f"wait={plan.wait_steps}  fire={fd}  "
            f"ft={plan.fire_tick}  it={plan.intercept_tick}"
        )

    def _update_radar(self) -> None:
        s = self.state
        pos = Vector2D(s.aircraft.position.x, s.aircraft.position.y)
        s.aircraft_history.append(pos)
        if len(s.aircraft_history) >= 2:
            p0 = s.aircraft_history[-2]
            p1 = s.aircraft_history[-1]
            delta = p1 - p0
            spd = delta.length() / C.DT
            if spd > 1e-6:
                norm = delta.normalized()
                self.inferred_direction = nearest_direction(norm.x, norm.y)
                self.inferred_speed = spd

    def _plan_is_stale(self) -> bool:
        if self.active_plan is None:
            return True
        if self._ticks_since_replan >= _REPLAN_INTERVAL:
            return True
        if (
            self.active_plan.plan_type == "fire_now"
            and self.inferred_direction is not None
            and self.inferred_speed is not None
        ):
            still_valid = validate_plan(
                self.active_plan,
                self.state.sam_truck,
                Vector2D(self.state.aircraft.position.x, self.state.aircraft.position.y),
                self.inferred_direction,
                self.inferred_speed,
                C.MISSILE_SPEED,
                C.DT,
                self.state.grid,
                C.PLANNING_HORIZON,
                self._predictor,
                missile_verify_cap=C.MISSILE_MAX_STEPS,
            )
            if not still_valid:
                return True
        return False

    def _estimate_truck_pos_after_move_steps(self, plan: TruckPlan) -> Vector2D:
        """Estimate post-move truck position without mutating state (clamped to bounds)."""
        s = self.state
        truck = s.sam_truck
        pos = Vector2D(truck.position.x, truck.position.y)

        if plan.move_direction is None or plan.move_steps <= 0:
            return pos

        d = plan.move_direction
        for _ in range(int(plan.move_steps)):
            nxt = Vector2D(pos.x + d.x * truck.speed * C.DT, pos.y + d.y * truck.speed * C.DT)
            pos = nxt if s.grid.in_bounds(nxt) else pos
        return pos

    def _select_pva_jammer_aware_plan(
        self,
        *,
        fire_now: TruckPlan | None,
        wait_plan: TruckPlan | None,
        move_plan: TruckPlan | None,
        mwf_plan: TruckPlan | None,
        best: TruckPlan,
    ) -> TruckPlan:
        if self.mode != self.MODE_PVA:
            return best
        if self.pva_phase != self.PVA_RUNNING:
            return best
        if self.pva_turn_used:
            return best
        if not self.jammer_active():
            return best
        if not self.has_aircraft:
            return best

        s = self.state
        if self.inferred_direction is None or self.inferred_speed is None:
            return best

        ac_pos = Vector2D(s.aircraft.position.x, s.aircraft.position.y)
        truck_pos = Vector2D(s.sam_truck.position.x, s.sam_truck.position.y)
        cur_dist = truck_pos.distance_to(ac_pos)

        def qualifies(candidate: TruckPlan | None) -> tuple[float, int, int] | None:
            if candidate is None:
                return None
            if candidate.move_steps <= 0:
                return None
            if best.primary_hit and not candidate.primary_hit:
                return None
            if int(candidate.intercept_tick) > int(best.intercept_tick) + 4:
                return None
            ok = validate_plan(
                candidate,
                s.sam_truck,
                ac_pos,
                self.inferred_direction,
                float(self.inferred_speed),
                C.MISSILE_SPEED,
                C.DT,
                s.grid,
                C.PLANNING_HORIZON,
                self._predictor,
                missile_verify_cap=C.MISSILE_MAX_STEPS,
            )
            if not ok:
                return None

            est_pos = self._estimate_truck_pos_after_move_steps(candidate)
            final_dist = est_pos.distance_to(ac_pos)
            improvement = cur_dist - final_dist
            if improvement < 0.25 and final_dist > float(C.JAMMER_RADIUS) + 1e-9:
                return None

            return (final_dist, int(candidate.intercept_tick), int(candidate.fire_tick))

        best_key: tuple[float, int, int] | None = None
        best_candidate: TruckPlan | None = None
        for cand in (fire_now, wait_plan, move_plan, mwf_plan):
            key = qualifies(cand)
            if key is None:
                continue
            if best_key is None or key < best_key:
                best_key = key
                best_candidate = cand

        if best_candidate is None:
            return best
        if best_candidate.plan_type == best.plan_type and best_candidate is best:
            return best

        new_dist = best_key[0] if best_key is not None else cur_dist
        print(
            "[jammer-aware] override "
            f"{best.plan_type} -> {best_candidate.plan_type} "
            f"dist {cur_dist:.2f} -> {new_dist:.2f} "
            f"intercept +{int(best_candidate.intercept_tick) - int(best.intercept_tick)}"
        )
        return best_candidate

    def _replan(self) -> TruckPlan | None:
        s = self.state
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
            missile_verify_cap=C.MISSILE_MAX_STEPS,
            prefer_truck_staging=(self.mode == self.MODE_PVA),
        )
        if best is not None:
            best = self._select_pva_jammer_aware_plan(
                fire_now=fire_now,
                wait_plan=wait_plan,
                move_plan=move_plan,
                mwf_plan=mwf_plan,
                best=best,
            )
        self.last_planner_ms = (time.perf_counter() - t0) * 1000.0
        self.last_diag = diag
        self._ticks_since_replan = 0

        print("  --- Planner ---")
        print(self._fmt_plan("fire_now", fire_now))
        print(self._fmt_plan("wait_then_fire", wait_plan))
        print(self._fmt_plan("move_then_fire", move_plan))
        print(self._fmt_plan("move_then_wait_then_fire", mwf_plan))
        if best is None:
            print(f"  selected: NONE  [{diag.no_solution_reason}]")
            self.last_plan_type = "none"
        else:
            print(f"  selected: [{best.plan_type}]  ft={best.fire_tick}  it={best.intercept_tick}")
            self.last_plan_type = best.plan_type
        print(
            f"  diag: cands={diag.candidates_evaluated}  "
            f"verified={diag.directions_verified}  "
            f"fallback={diag.fallback_used}  planner={self.last_planner_ms:.1f}ms"
        )
        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            sp = self.inferred_speed if self.inferred_speed is not None else float(C.AIRCRAFT_SPEED)
            vok = None
            if best is not None and self.inferred_direction is not None:
                vok = validate_plan(
                    best,
                    truck,
                    Vector2D(s.aircraft.position.x, s.aircraft.position.y),
                    self.inferred_direction,
                    sp,
                    C.MISSILE_SPEED,
                    C.DT,
                    s.grid,
                    C.PLANNING_HORIZON,
                    self._predictor,
                    missile_verify_cap=C.MISSILE_MAX_STEPS,
                )
            print(
                f"  [pva-planner] predictor={diag.predictor_name}  "
                f"sam_threat_turn_dirs={self._pva_last_sam_turn_dirs_count}  "
                f"futures={diag.futures_evaluated}  validate_plan_ok={vok}  "
                f"no_sol={diag.no_solution_reason or '—'}"
            )
        return best

    def _jammer_pressure_action(self) -> bool:
        s = self.state
        truck = s.sam_truck
        ac = s.aircraft

        dist = self.jammer_distance_tiles()
        if dist is not None and dist <= float(C.JAMMER_RADIUS) + 1e-9:
            self.last_action = "jammer pressure (hold)"
            print(f"  ACTION: jammer pressure hold  dist={dist:.2f} r={float(C.JAMMER_RADIUS):.1f}")
            return True

        best_d = None
        best_dist = 1e18
        for d in DIRECTIONS:
            nxt = Vector2D(
                truck.position.x + d.x * truck.speed * C.DT,
                truck.position.y + d.y * truck.speed * C.DT,
            )
            if not s.grid.in_bounds(nxt):
                continue
            dd = nxt.distance_to(ac.position)
            if dd < best_dist:
                best_dist = dd
                best_d = d

        if best_d is None:
            return False

        start = Vector2D(truck.position.x, truck.position.y)
        truck.direction = best_d
        move_entity(truck, C.DT, s.grid, state=s)
        self._record_entity_move("sam", start, truck, s.tick)
        self.last_action = "jammer pressure"
        print(f"  ACTION: jammer pressure move  d={self._direction_name(best_d)}")
        return True

    def _apply_truck_action(self) -> TruckPlan | None:
        s = self.state
        truck = s.sam_truck
        best: TruckPlan | None = None

        self._refresh_predictor()

        if truck.has_fired:
            return None

        if self.inferred_direction is None:
            self.last_action = f"waiting for radar lock (tick {s.tick})"
            print(f"  ACTION: waiting for radar lock (tick {s.tick})")
            return None

        if self._plan_is_stale():
            self.active_plan = self._replan()
        else:
            self._ticks_since_replan += 1
            ap = self.active_plan
            print(
                f"  [plan reuse {self._ticks_since_replan}/{_REPLAN_INTERVAL} "
                f"type={ap.plan_type if ap else 'none'} ft={ap.fire_tick if ap else '?'}]"
            )

        best = self.active_plan
        if best is None:
            if (
                self.mode == self.MODE_PVA
                and self.pva_phase == self.PVA_RUNNING
                and not self.pva_turn_used
                and self.has_aircraft
                and not self.scenario_finished()
            ):
                did = self._jammer_pressure_action()
                if did:
                    return None
            self.last_action = "idle (no plan)"
            print("  ACTION: idle (no plan)")
            return None

        if best.move_steps > 0:
            start = Vector2D(truck.position.x, truck.position.y)
            truck.direction = best.move_direction
            move_entity(truck, C.DT, s.grid, state=s)
            self._record_entity_move("sam", start, truck, s.tick)
            self.last_action = f"move {self._direction_name(best.move_direction)}"
            self.active_plan = replace(
                self._advance_plan_tick_fields(best),
                move_steps=best.move_steps - 1,
            )
            print(f"  ACTION: move  ({best.move_steps} steps left)")
        elif best.wait_steps > 0:
            self.last_action = "wait"
            self.active_plan = replace(
                self._advance_plan_tick_fields(best),
                wait_steps=best.wait_steps - 1,
            )
            print(f"  ACTION: wait  ({best.wait_steps} steps left)")
        else:
            missile = launch_missile_in_direction(truck, C.MISSILE_SPEED, best.fire_direction)
            if missile is not None:
                s.missiles.append(missile)
                self.last_action = f"FIRED {self._direction_name(best.fire_direction)}"
                print(f"  ACTION: >>> FIRED  tick={s.tick}")
            self.active_plan = None
        return best

    # ------------------------------------------------------------------
    # Automatic mode logic
    # ------------------------------------------------------------------

    def _update_stall(self, had_plan: bool) -> None:
        s = self.state
        pos = s.aircraft.position
        at_border = (
            pos.x <= 0.05
            or pos.y <= 0.05
            or pos.x >= s.grid.width - 0.05
            or pos.y >= s.grid.height - 0.05
        )
        if at_border and not had_plan:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0
        if self._stall_ticks >= _STALL_LIMIT:
            s.failed = True
            print("  *** SCENARIO FAILED — border stall, no solution ***")

    def _append_trail_segment(self, kind: str, start: Vector2D, end: Vector2D, tick: int) -> None:
        dx = end.x - start.x
        dy = end.y - start.y
        if dx * dx + dy * dy <= 1e-12:
            return
        self.movement_trails.append(TrailSegment(kind=kind, start=start, end=end, tick=int(tick)))

    def _record_entity_move(self, kind: str, start: Vector2D, ent, tick: int) -> None:
        end = Vector2D(ent.position.x, ent.position.y)
        self._append_trail_segment(kind, start, end, tick)

    def _run_step_automatic(self) -> None:
        t_step = time.perf_counter()
        s = self.state
        truck = s.sam_truck
        s.tick += 1
        self._update_radar()
        best = self._apply_truck_action()

        ac_start = Vector2D(s.aircraft.position.x, s.aircraft.position.y)
        move_entity(s.aircraft, C.DT, s.grid, state=s)
        self._record_entity_move("aircraft", ac_start, s.aircraft, s.tick)
        for missile in s.missiles:
            ms = Vector2D(missile.position.x, missile.position.y)
            move_entity(missile, C.DT, s.grid, state=s)
            self._record_entity_move("missile", ms, missile, s.tick)
        if check_interception(s.aircraft, s.missiles, s.grid):
            s.intercepted = True
        self._update_stall(had_plan=(best is not None))

        self.last_step_ms = (time.perf_counter() - t_step) * 1000.0
        idir = self._direction_name(self.inferred_direction)
        print(
            f"[Tick {s.tick:>3}]  AC={s.aircraft.position}  idir={idir}  "
            f"truck={truck.position}  fired={truck.has_fired}  "
            f"step={self.last_step_ms:.1f}ms  plan={self.last_planner_ms:.1f}ms"
        )
        for i, m in enumerate(s.missiles):
            print(f"  M{i}: {m.position}  active={m.active}")
        if s.intercepted:
            print(f"\n*** INTERCEPTED at tick {s.tick}! ***\n")

    # ------------------------------------------------------------------
    # PVA deployment interaction
    # ------------------------------------------------------------------

    def set_pva_hover_tile(self, tile: tuple[int, int] | None) -> None:
        if self.mode != self.MODE_PVA or self.pva_phase != self.PVA_TILE_SELECT:
            return
        if tile is None or not is_border_tile(self.state.grid, tile):
            self.pva_hover_tile = None
            return
        self.pva_hover_tile = tile

    def _pick_nearest_dir(self, dirs: list[DirectionVector], dx: float, dy: float) -> DirectionVector | None:
        if not dirs:
            return None
        mag = math.sqrt(dx * dx + dy * dy)
        if mag < 1e-9:
            return dirs[0]
        best = None
        best_score = -1e18
        for d in dirs:
            score = (d.x * dx + d.y * dy) / mag
            if score > best_score:
                best_score = score
                best = d
        return best

    def update_pva_hover_direction(self, dx: float, dy: float) -> None:
        if self.mode != self.MODE_PVA:
            return
        if self.pva_phase not in (self.PVA_ANGLE_SELECT, self.PVA_CONFIRM):
            return
        d = self._pick_nearest_dir(self.pva_valid_launch_dirs, dx, dy)
        self.pva_hover_direction = d
        if d is not None and self.pva_locked_tile is not None:
            self.pva_hover_exit_tiles = project_valid_exit_tiles(
                self.state.grid,
                self.pva_locked_tile,
                d,
                speed=float(C.AIRCRAFT_SPEED),
                dt=float(C.DT),
            )
        else:
            self.pva_hover_exit_tiles = set()

    def update_pva_turn_hover(self, dx: float, dy: float) -> None:
        self._pva_turn_ptr = (dx, dy)
        if self.mode != self.MODE_PVA or self.pva_phase != self.PVA_RUNNING:
            return
        if self.pva_turn_used:
            return
        if not self._pva_in_turn_window():
            self.pva_player_turn_dirs = []
            self.pva_turn_hover_direction = None
            return
        if self.aircraft_jammed():
            self.pva_player_turn_dirs = []
            self.pva_turn_hover_direction = None
            return
        self.pva_player_turn_dirs = list(DIRECTIONS)
        self.pva_turn_hover_direction = self._pick_nearest_dir(self.pva_player_turn_dirs, dx, dy)

    def pva_left_click(self) -> None:
        if self.mode != self.MODE_PVA:
            return
        if self.pva_phase == self.PVA_TILE_SELECT:
            if self.pva_hover_tile is None:
                return
            self.pva_locked_tile = self.pva_hover_tile
            sam_p = self.state.sam_truck.position
            self.pva_valid_launch_dirs = legal_launch_directions(
                self.state.grid,
                self.pva_locked_tile,
                sam_p,
                C.AIRCRAFT_SPEED,
                C.DT,
            )
            base_rad = ESCAPE_DEBUG_LAST.get("pva_launch_base_rad")
            base_deg = math.degrees(float(base_rad)) if isinstance(base_rad, (int, float)) else None
            ch = ESCAPE_DEBUG_LAST.get("pva_launch_half_cone_deg")
            ex = ESCAPE_DEBUG_LAST.get("pva_launch_cone_expand_extra_deg")
            ang_list = [round(math.degrees(math.atan2(d.y, d.x)), 1) for d in self.pva_valid_launch_dirs]
            print(
                f"[pva-launch] tile={self.pva_locked_tile}  sam=({sam_p.x:.2f},{sam_p.y:.2f})  "
                f"base_deg={base_deg}  cone_half_deg={ch}  cone_expand_extra_deg={ex}  "
                f"n_dirs={len(self.pva_valid_launch_dirs)}  angles_deg={ang_list}"
            )
            self.pva_hover_direction = self.pva_valid_launch_dirs[0] if self.pva_valid_launch_dirs else None
            self.pva_hover_exit_tiles = (
                project_valid_exit_tiles(
                    self.state.grid,
                    self.pva_locked_tile,
                    self.pva_hover_direction,
                    speed=float(C.AIRCRAFT_SPEED),
                    dt=float(C.DT),
                )
                if self.pva_hover_direction is not None else set()
            )
            self.pva_phase = self.PVA_ANGLE_SELECT
            self.last_action = "Select heading"
            return

        if self.pva_phase == self.PVA_ANGLE_SELECT:
            if self.pva_hover_direction is None:
                return
            self.pva_locked_direction = self.pva_hover_direction
            self.pva_locked_exit_tiles = project_valid_exit_tiles(
                self.state.grid,
                self.pva_locked_tile,
                self.pva_locked_direction,
                speed=float(C.AIRCRAFT_SPEED),
                dt=float(C.DT),
            )
            self.pva_exit_stripe_half = exit_stripe_half_for_pva()
            first_hit = ESCAPE_DEBUG_LAST.get("first_hit_debug", "?")
            print(
                "[pva-heading-confirm] "
                f"locked_exit_count={len(self.pva_locked_exit_tiles)}  "
                f"locked_exit_tiles={sorted(self.pva_locked_exit_tiles)}  "
                f"stripe_half={self.pva_exit_stripe_half}  first_ray_hit={first_hit}"
            )
            self.pva_phase = self.PVA_CONFIRM
            self.last_action = "Confirm deployment"
            return

        if self.pva_phase == self.PVA_CONFIRM:
            if self.pva_locked_tile is None or self.pva_locked_direction is None:
                return
            self.state.aircraft.position = tile_pos(self.pva_locked_tile)
            self.state.aircraft.direction = self.pva_locked_direction
            self.state.aircraft.speed = C.AIRCRAFT_SPEED
            self.state.aircraft_history.clear()
            self.state.tick = 0
            self.active_plan = None
            self._ticks_since_replan = 0
            self._predictor = ConstantVelocityPredictor()
            self.pva_phase = self.PVA_RUNNING
            self.pva_turn_used = False
            self._pva_turn_ptr = (1.0, 0.0)
            self._dbg_turn_fan_key = None
            self.pva_turn_hover_direction = None
            self._recompute_pva_turn_fan()
            self.last_action = "Aircraft deployed"
            return

        if self.pva_phase == self.PVA_RUNNING and not self.pva_turn_used:
            if self.aircraft_jammed():
                self.last_action = "TURN BLOCKED BY JAMMER"
                return
            if not self._pva_in_turn_window():
                self.last_action = "Turn unavailable"
                return
            if self.pva_turn_hover_direction is None:
                dx, dy = self._pva_turn_ptr
                pick = self._pick_nearest_dir(list(DIRECTIONS), dx, dy)
                self.pva_turn_hover_direction = pick if pick is not None else self.state.aircraft.direction
            sel = self.pva_turn_hover_direction
            self.state.aircraft.direction = sel
            self.pva_turn_used = True
            self.pva_player_turn_dirs = []
            self.pva_turn_hover_direction = None
            self.active_plan = None
            self._ticks_since_replan = 0
            self._refresh_predictor()
            self.last_action = f"TURN {self._direction_name(sel)}"

    def pva_right_click(self) -> None:
        if self.mode != self.MODE_PVA:
            return
        if self.pva_phase == self.PVA_ANGLE_SELECT:
            self.pva_phase = self.PVA_TILE_SELECT
            self.pva_locked_tile = None
            self.pva_valid_launch_dirs = []
            self.pva_hover_direction = None
            self.pva_hover_exit_tiles = set()
            self.last_action = "Select a border tile"
        elif self.pva_phase == self.PVA_CONFIRM:
            self.pva_phase = self.PVA_ANGLE_SELECT
            self.pva_locked_direction = None
            self.pva_locked_exit_tiles = set()
            self.pva_exit_stripe_half = 0
            self.pva_player_turn_dirs = []
            self.pva_sam_threat_turn_dirs = []
            self._dbg_turn_fan_key = None
            self._pva_logged_outside_turn_fan = False
            self.last_action = "Select heading"

    # ------------------------------------------------------------------
    # PVA running logic
    # ------------------------------------------------------------------

    def _move_pva_aircraft(self) -> None:
        s = self.state
        ac = s.aircraft
        start = Vector2D(ac.position.x, ac.position.y)
        next_pos = Vector2D(
            ac.position.x + ac.direction.x * ac.speed * C.DT,
            ac.position.y + ac.direction.y * ac.speed * C.DT,
        )
        if s.grid.in_bounds(next_pos):
            ac.position = next_pos
            self._append_trail_segment("aircraft", start, Vector2D(next_pos.x, next_pos.y), s.tick)
            return
        self._append_trail_segment("aircraft", start, Vector2D(next_pos.x, next_pos.y), s.tick)
        exit_tile = crossed_exit_tile(ac.position, next_pos, s.grid)
        if exit_tile is not None and exit_tile in self.pva_locked_exit_tiles:
            s.escaped = True
            self.pva_phase = self.PVA_END
            self.pva_result_text = "ESCAPED"
            self.last_action = f"ESCAPED via {exit_tile}"
        else:
            s.failed = True
            self.pva_phase = self.PVA_END
            self.pva_result_text = "ILLEGAL EXIT"
            self.last_action = f"FAILED exit via {exit_tile}"

    def _run_step_pva(self) -> None:
        t_step = time.perf_counter()
        s = self.state
        truck = s.sam_truck
        s.tick += 1
        self._update_radar()
        self._apply_truck_action()

        self._move_pva_aircraft()
        if not (s.failed or s.escaped):
            for missile in s.missiles:
                ms = Vector2D(missile.position.x, missile.position.y)
                move_entity(missile, C.DT, s.grid, state=s)
                self._record_entity_move("missile", ms, missile, s.tick)
            if check_interception(s.aircraft, s.missiles, s.grid):
                s.intercepted = True
                self.pva_phase = self.PVA_END
                self.pva_result_text = "INTERCEPTED"
                self.last_action = "INTERCEPTED"

        if (
            self.pva_phase == self.PVA_RUNNING
            and not (s.failed or s.escaped or s.intercepted)
            and not self.pva_turn_used
        ):
            self._recompute_pva_turn_fan()

        self.last_step_ms = (time.perf_counter() - t_step) * 1000.0
        idir = self._direction_name(self.inferred_direction)
        print(
            f"[Tick {s.tick:>3}]  AC={s.aircraft.position}  idir={idir}  "
            f"truck={truck.position}  fired={truck.has_fired}  "
            f"step={self.last_step_ms:.1f}ms  plan={self.last_planner_ms:.1f}ms"
        )
        for i, m in enumerate(s.missiles):
            print(f"  M{i}: {m.position}  active={m.active}")
        if s.intercepted:
            print(f"\n*** INTERCEPTED at tick {s.tick}! ***\n")
        elif s.escaped:
            print(f"\n*** ESCAPED at tick {s.tick}! ***\n")
        elif s.failed:
            print(f"\n*** FAILED at tick {s.tick}! ***\n")

    # ------------------------------------------------------------------
    # Public tick runner
    # ------------------------------------------------------------------

    def run_step(self) -> None:
        if self.mode == self.MODE_AUTOMATIC:
            self._run_step_automatic()
        elif self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            self._run_step_pva()