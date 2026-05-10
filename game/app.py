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
    segment_crosses_locked_exit_display,
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
from systems.collision_system import HIT_RADIUS, check_interception

_STALL_LIMIT = 16
_REPLAN_INTERVAL = 4


@dataclass(frozen=True)
class PVAPreview:
    tile: tuple[int, int] | None
    direction: DirectionVector | None
    exit_tiles: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class PVAReplayFrame:
    """One tick of recorded PVA state for UI replay (no re-simulation)."""

    tick: int
    aircraft_x: float
    aircraft_y: float
    aircraft_dir_index: int
    truck_x: float
    truck_y: float
    truck_dir_index: int
    truck_has_fired: bool
    missiles: tuple[tuple[float, float, int, bool], ...]
    locked_exit_tiles: tuple[tuple[int, int], ...]
    intercepted: bool
    escaped: bool
    failed: bool
    pva_phase: str
    pva_result_text: str
    last_action: str
    ai_confidence_label: str
    ai_plan_summary: str
    ai_explanation: str
    last_plan_type: str
    last_planner_ms: float
    action_policy: str
    action_policy_reason: str
    coverage: str
    futures_hit: int
    futures_total: int
    risky_unvalidated: bool
    ai_future_paths: tuple[tuple[tuple[float, float], ...], ...]
    pva_turn_used: bool
    # Replay/UI only — frozen snapshot from the launcher commit moment (no gameplay impact).
    launch_tick: int = -1
    launch_confidence: str = ""
    launch_coverage: str = ""
    launch_plan_type: str = ""
    launch_policy_snapshot: str = ""


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
        # PVA diagnostics: last validate_plan() result for the last planner output.
        # None means "skipped/not run".
        self._pva_last_validate_plan_ok: bool | None = None

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
        # SAM-side "threat" directions: a filtered set used for display/diagnostics and, optionally,
        # for predictor branching. Player turns can still be all 32 directions when enabled.
        self.pva_sam_threat_turn_dirs: list[DirectionVector] = []
        self._dbg_turn_fan_key: object | None = None
        self._pva_logged_outside_turn_fan: bool = False
        self._pva_turn_ptr: tuple[float, float] = (1.0, 0.0)
        self._pva_last_predictor_candidate_dirs: int = 0
        # Smoothness control for forced every-tick PVA replans:
        # allow at most one "heavy keep staging" skip per heavy replan result.
        self._pva_heavy_skip_armed: bool = False
        self.pva_turn_used: bool = False
        self.pva_turn_hover_direction: DirectionVector | None = None
        self.pva_result_text: str = ""
        self._pva_result_logged: bool = False
        # PVA replay (UI only): recorded per running tick; cleared on new round.
        self.pva_replay_frames: list[PVAReplayFrame] = []
        self._pva_replay_snap_policy: str = ""
        self._pva_replay_snap_reason: str = ""
        self._pva_replay_snap_coverage: str = "—"
        self._pva_replay_snap_futures_hit: int = 0
        self._pva_replay_snap_futures_total: int = 0
        self._pva_replay_snap_risky: bool = False
        self._pva_replay_launch_tick: int = -1
        self._pva_replay_launch_confidence: str = ""
        self._pva_replay_launch_coverage: str = ""
        self._pva_replay_launch_plan_type: str = ""
        self._pva_replay_launch_policy_snapshot: str = ""

        self.ai_future_paths: list[list[Vector2D]] = []
        self.ai_confidence_label: str = "LOW"
        self.ai_explanation: str = "Waiting for planner."
        self.ai_plan_summary: str = "AI LOW | Waiting for planner"
        self._reset_ai_inspector()

    def _reset_ai_inspector(self) -> None:
        self.ai_future_paths = []
        self.ai_confidence_label = "LOW"
        self.ai_explanation = "Waiting for planner."
        self.ai_plan_summary = "AI LOW | Waiting for planner"

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
        # Player turn UI can remain available beyond TURN_WINDOW_MAX; keep the SAM predictor
        # bounded by a reasonable horizon so branching cost doesn't explode.
        turn_max_remaining = max(0, min(int(C.PLANNING_HORIZON), int(C.PLANNING_HORIZON)))

        spd = float(self.inferred_speed) if self.inferred_speed is not None else float(C.AIRCRAFT_SPEED)

        vx = frozenset(self.pva_locked_exit_tiles)
        ac = s.aircraft
        pos_v = Vector2D(ac.position.x, ac.position.y)
        plan_heading = self.inferred_direction if self.inferred_direction is not None else ac.direction

        turn_min_remaining = max(0, C.TURN_WINDOW_MIN - s.tick)

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
                turn_min_remaining=turn_min_remaining,
                turn_max_remaining=turn_max_remaining,
            )
        else:
            dirs = list(DIRECTIONS)
            ESCAPE_DEBUG_LAST["predictor_late_turn_dirs"] = 0
            ESCAPE_DEBUG_LAST["predictor_late_turn_prefix_ticks"] = 0
        self._pva_last_predictor_candidate_dirs = len(dirs)

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
        self._reset_ai_inspector()

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
        self._pva_last_predictor_candidate_dirs = 0
        self.pva_turn_used = False
        self.pva_turn_hover_direction = None
        self.pva_result_text = ""
        self._pva_result_logged = False
        self.pva_replay_frames = []
        self._pva_clear_replay_snap()
        self._pva_reset_replay_launch_memo()
        self._reset_ai_inspector()

    def _pva_reset_replay_launch_memo(self) -> None:
        self._pva_replay_launch_tick = -1
        self._pva_replay_launch_confidence = ""
        self._pva_replay_launch_coverage = ""
        self._pva_replay_launch_plan_type = ""
        self._pva_replay_launch_policy_snapshot = ""

    def _pva_clear_replay_snap(self) -> None:
        self._pva_replay_snap_policy = ""
        self._pva_replay_snap_reason = ""
        self._pva_replay_snap_coverage = "—"
        self._pva_replay_snap_futures_hit = 0
        self._pva_replay_snap_futures_total = 0
        self._pva_replay_snap_risky = False

    def _pva_capture_replay_stamp(
        self,
        *,
        policy: str,
        plan: TruckPlan | None,
        reason: str,
    ) -> None:
        fh = int(getattr(plan, "futures_hit", 0)) if plan is not None else 0
        ft_raw = int(getattr(plan, "futures_total", 0)) if plan is not None else 0
        ft = ft_raw if plan is not None else 0
        cov_s = "—" if plan is None else f"{fh}/{max(ft, 1)}"
        self._pva_replay_snap_policy = str(policy or "")
        self._pva_replay_snap_reason = str(reason or "")
        self._pva_replay_snap_coverage = cov_s
        self._pva_replay_snap_futures_hit = fh
        self._pva_replay_snap_futures_total = max(ft, 1) if plan is not None else 0
        vok = self._pva_last_validate_plan_ok
        self._pva_replay_snap_risky = vok is False

    def _pva_append_replay_frame(self) -> None:
        """Append one snapshot after physics for this tick (PVA_RUNNING only enters here via run_step)."""
        if self.mode != self.MODE_PVA:
            return
        s = self.state
        ac = s.aircraft
        truck = s.sam_truck
        missiles: list[tuple[float, float, int, bool]] = []
        for m in s.missiles:
            try:
                missiles.append((float(m.position.x), float(m.position.y), int(m.direction.index), bool(m.active)))
            except Exception:
                continue
        paths_plain: list[tuple[tuple[float, float], ...]] = []
        for raw in self.ai_future_paths or []:
            seg: list[tuple[float, float]] = []
            for pt in raw or []:
                try:
                    seg.append((float(pt.x), float(pt.y)))
                except Exception:
                    continue
            if seg:
                paths_plain.append(tuple(seg))
            else:
                paths_plain.append(tuple())
        locked = tuple(sorted(self.pva_locked_exit_tiles))
        prev_fired = bool(self.pva_replay_frames and self.pva_replay_frames[-1].truck_has_fired)
        if truck.has_fired and (not prev_fired):
            self._pva_replay_launch_tick = int(s.tick)
            self._pva_replay_launch_confidence = str(self.ai_confidence_label or "").strip()
            lc = str(self._pva_replay_snap_coverage or "").strip()
            if not lc or lc == "—":
                fh0 = int(self._pva_replay_snap_futures_hit)
                ft0 = int(max(1, self._pva_replay_snap_futures_total))
                lc = f"{fh0}/{ft0}"
            self._pva_replay_launch_coverage = lc
            self._pva_replay_launch_plan_type = str(self.last_plan_type or "").strip()
            self._pva_replay_launch_policy_snapshot = str(self._pva_replay_snap_policy or "").strip()
        try:
            fr = PVAReplayFrame(
                tick=int(s.tick),
                aircraft_x=float(ac.position.x),
                aircraft_y=float(ac.position.y),
                aircraft_dir_index=int(ac.direction.index),
                truck_x=float(truck.position.x),
                truck_y=float(truck.position.y),
                truck_dir_index=int(truck.direction.index),
                truck_has_fired=bool(truck.has_fired),
                missiles=tuple(missiles),
                locked_exit_tiles=locked,
                intercepted=bool(s.intercepted),
                escaped=bool(s.escaped),
                failed=bool(s.failed),
                pva_phase=str(self.pva_phase),
                pva_result_text=str(self.pva_result_text or ""),
                last_action=str(self.last_action or ""),
                ai_confidence_label=str(self.ai_confidence_label or ""),
                ai_plan_summary=str(self.ai_plan_summary or ""),
                ai_explanation=str(self.ai_explanation or ""),
                last_plan_type=str(self.last_plan_type or ""),
                last_planner_ms=float(self.last_planner_ms),
                action_policy=str(self._pva_replay_snap_policy or ""),
                action_policy_reason=str(self._pva_replay_snap_reason or ""),
                coverage=str(self._pva_replay_snap_coverage or "—"),
                futures_hit=int(self._pva_replay_snap_futures_hit),
                futures_total=int(max(1, self._pva_replay_snap_futures_total)),
                risky_unvalidated=bool(self._pva_replay_snap_risky),
                ai_future_paths=tuple(tuple(p) for p in paths_plain),
                pva_turn_used=bool(self.pva_turn_used),
                launch_tick=int(self._pva_replay_launch_tick),
                launch_confidence=str(self._pva_replay_launch_confidence or ""),
                launch_coverage=str(self._pva_replay_launch_coverage or ""),
                launch_plan_type=str(self._pva_replay_launch_plan_type or ""),
                launch_policy_snapshot=str(self._pva_replay_launch_policy_snapshot or ""),
            )
            self.pva_replay_frames.append(fr)
        except Exception:
            pass

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
        self._reset_ai_inspector()
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
        """Player may execute the one-turn while tick is >= TURN_WINDOW_MIN (one-turn-only still enforced)."""
        return (
            self.mode == self.MODE_PVA
            and self.pva_phase == self.PVA_RUNNING
            and int(C.TURN_WINDOW_MIN)
            <= self.state.tick
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
            if not self._pva_logged_outside_turn_fan:
                self._pva_logged_outside_turn_fan = True
                print(
                    f"[pva-turn] tick={self.state.tick} player_turn_dirs=0 sam_threat_turn_dirs=0 "
                    f"window=[{C.TURN_WINDOW_MIN},end] heading=— hover=None "
                    f"— outside_turn_window"
                )
            return

        self._pva_logged_outside_turn_fan = False
        ac = self.state.aircraft
        pos_v = Vector2D(ac.position.x, ac.position.y)
        vx = frozenset(self.pva_locked_exit_tiles)
        self.pva_sam_threat_turn_dirs = legal_turn_directions(
            self.state.grid,
            pos_v,
            ac.direction,
            vx,
            exit_stripe_half=self.pva_exit_stripe_half,
            max_turn_angle_deg=None,
        )
        if C.PVA_PLAYER_TURN_FREE:
            self.pva_player_turn_dirs = list(DIRECTIONS)
        else:
            self.pva_player_turn_dirs = list(self.pva_sam_threat_turn_dirs)

        self.pva_turn_hover_direction = self._pick_nearest_dir(self.pva_player_turn_dirs, pdx, pdy)

        hi = (
            self.pva_turn_hover_direction.index
            if self.pva_turn_hover_direction is not None
            else -1
        )
        key = ("in", len(self.pva_player_turn_dirs), len(self.pva_sam_threat_turn_dirs), hi)
        if key != self._dbg_turn_fan_key:
            self._dbg_turn_fan_key = key
            hname = self._direction_name(self.pva_turn_hover_direction)
            hd = self._direction_name(ac.direction)
            msg = (
                f"[pva-turn] tick={self.state.tick} player_turn_dirs={len(self.pva_player_turn_dirs)} "
                f"sam_threat_turn_dirs={len(self.pva_sam_threat_turn_dirs)} "
                f"window=[{C.TURN_WINDOW_MIN},end] heading={hd} hover={hname}"
            )
            if len(self.pva_sam_threat_turn_dirs) == 0:
                why = explain_legal_turn_empty(
                    self.state.grid,
                    pos_v,
                    ac.direction,
                    vx,
                    self.pva_exit_stripe_half,
                    self.state.tick,
                    int(C.TURN_WINDOW_MIN),
                    10**9,
                    self.pva_turn_used,
                )
                msg += f"  sam_threat_empty_why={why}"
            print(msg)

    def _plan_display_name(self, plan_type: str) -> str:
        mapping = {
            "fire_now": "Fire now",
            "wait_then_fire": "Wait, then fire",
            "move_then_fire": "Reposition, then fire",
            "move_then_wait_then_fire": "Reposition, wait, then fire",
        }
        return mapping.get(plan_type, plan_type.replace("_", " "))

    def _build_ai_inspector(self, best: TruckPlan | None, diag: PlannerDiagnostic) -> None:
        if self.inferred_direction is None or self.inferred_speed is None:
            self.ai_future_paths = []
            self.ai_confidence_label = "LOW"
            self.ai_plan_summary = "AI LOW | Waiting for radar lock"
            self.ai_explanation = "Waiting for radar lock before planning an intercept."
            return

        futures_raw: object = []
        try:
            futures_raw = self._predictor.predict_set(
                position=Vector2D(self.state.aircraft.position.x, self.state.aircraft.position.y),
                direction=self.inferred_direction,
                speed=float(self.inferred_speed),
                dt=C.DT,
                grid=self.state.grid,
                max_steps=C.PLANNING_HORIZON,
            )
        except Exception:
            futures_raw = []

        futures_list: list = []
        if isinstance(futures_raw, list):
            futures_list = futures_raw
        # Display-only: select a small diverse subset of futures, keeping the primary future first.
        def _future_sig(path: list[Vector2D]) -> tuple[tuple[int, int] | None, int, int]:
            if path is None or len(path) < 2:
                return (None, -1, 0)
            p1 = path[-1]
            p0 = path[-2]
            dx = float(getattr(p1, "x", 0.0)) - float(getattr(p0, "x", 0.0))
            dy = float(getattr(p1, "y", 0.0)) - float(getattr(p0, "y", 0.0))
            d = nearest_direction(dx, dy) if (abs(dx) + abs(dy)) > 1e-9 else None
            dir_bucket = int(d.index) if d is not None else -1
            len_bucket = int(len(path) // 6)

            g = self.state.grid
            lx = float(getattr(p1, "x", 0.0))
            ly = float(getattr(p1, "y", 0.0))
            near_border = (
                lx <= 0.05
                or ly <= 0.05
                or lx >= float(g.width - 1) - 0.05
                or ly >= float(g.height - 1) - 0.05
            )
            exit_tile = None
            if near_border:
                last = Vector2D(lx, ly)
                nxt = Vector2D(lx + dx, ly + dy)
                if not g.in_bounds(nxt):
                    exit_tile = crossed_exit_tile(last, nxt, g)
            return (exit_tile, dir_bucket, len_bucket)

        selected: list[list[Vector2D]] = []
        if futures_list:
            selected.append(futures_list[0])
            seen = {_future_sig(futures_list[0])}
            for p in futures_list[1:]:
                if len(selected) >= 5:
                    break
                try:
                    sig = _future_sig(p)
                except Exception:
                    sig = (None, -1, 0)
                if sig in seen:
                    continue
                seen.add(sig)
                selected.append(p)
            if len(selected) < 5:
                for p in futures_list[1:]:
                    if len(selected) >= 5:
                        break
                    if p in selected:
                        continue
                    selected.append(p)
        self.ai_future_paths = selected

        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING and self.ai_future_paths:
            g = self.state.grid
            for i, path in enumerate(self.ai_future_paths):
                if path is None or len(path) < 2:
                    continue
                p0 = path[0]
                p1 = path[-1]
                dx = float(getattr(p1, "x", 0.0)) - float(getattr(path[-2], "x", 0.0))
                dy = float(getattr(p1, "y", 0.0)) - float(getattr(path[-2], "y", 0.0))
                mag = math.hypot(dx, dy)
                fx, fy = (dx / mag, dy / mag) if mag > 1e-9 else (0.0, 0.0)
                lx = float(getattr(p1, "x", 0.0))
                ly = float(getattr(p1, "y", 0.0))
                near_border = (
                    lx <= 0.05
                    or ly <= 0.05
                    or lx >= float(g.width - 1) - 0.05
                    or ly >= float(g.height - 1) - 0.05
                )
                exit_tile = None
                if near_border and mag > 1e-9:
                    last = Vector2D(lx, ly)
                    nxt = Vector2D(lx + dx, ly + dy)
                    if not g.in_bounds(nxt):
                        exit_tile = crossed_exit_tile(last, nxt, g)
                print(
                    f"[ai-future] i={i} len={len(path)} "
                    f"first=({float(getattr(p0,'x',0.0)):.2f},{float(getattr(p0,'y',0.0)):.2f}) "
                    f"last=({lx:.2f},{ly:.2f}) "
                    f"final_dir=({fx:.2f},{fy:.2f}) "
                    f"near_border={near_border} exit_tile={exit_tile}"
                )

        pred_name = getattr(diag, "predictor_name", None)
        pred_name_str = pred_name if isinstance(pred_name, str) and pred_name else "?"

        if best is None:
            self.ai_confidence_label = "LOW"
            ns = getattr(diag, "no_solution_reason", "")
            rs = ns.strip() if isinstance(ns, str) and ns.strip() else "planner found no safe plan"
            self.ai_explanation = f"No verified intercept: {rs}"
            self.ai_plan_summary = f"AI LOW | {pred_name_str} | No verified intercept"
            tex = self.ai_explanation.replace('"', "'")
            print(
                "[ai-explain] "
                f"confidence=LOW predictor={pred_name_str} plan=none coverage=— primary_hit=False "
                f'text="{tex}"'
            )
            return

        fh = int(getattr(best, "futures_hit", 0))
        ft = int(getattr(best, "futures_total", 0))
        cov = fh / ft if ft > 0 else 0.0
        primary_hit = bool(getattr(best, "primary_hit", False))
        if primary_hit and cov >= 0.75:
            confidence = "HIGH"
        elif primary_hit and cov >= 0.35:
            confidence = "MEDIUM"
        elif cov >= 0.50:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Confidence should reflect actual evidence: with no branching, don't claim "HIGH".
        if ft <= 1:
            if confidence == "HIGH":
                confidence = "MEDIUM"
            if not primary_hit:
                confidence = "LOW"

        # In PVA, cap "HIGH" when the predictor considered very few post-turn candidates.
        if (
            confidence == "HIGH"
            and self.mode == self.MODE_PVA
            and self.pva_phase == self.PVA_RUNNING
            and int(self._pva_last_predictor_candidate_dirs) <= 2
        ):
            confidence = "MEDIUM"

        self.ai_confidence_label = confidence

        pit_v = getattr(best, "primary_intercept_tick", 10**9)
        pit_i = int(pit_v) if isinstance(pit_v, int | float) else 10**9
        if primary_hit and pit_i < 10**8:
            primary_seg = f"primary future hit in {pit_i} ticks"
        else:
            primary_seg = "primary future not guaranteed"

        plan_raw = getattr(best, "plan_type", "") or ""
        plan_type_s = plan_raw if isinstance(plan_raw, str) else ""

        if plan_type_s == "fire_now":
            expl = f"Fire now; {primary_seg}; covers {fh}/{max(ft, 1)} futures."
        elif plan_type_s == "wait_then_fire":
            wv = int(getattr(best, "wait_steps", 0))
            expl = f"Wait {wv} ticks, then fire; {primary_seg}; covers {fh}/{max(ft, 1)} futures."
        elif plan_type_s == "move_then_fire":
            mv = int(getattr(best, "move_steps", 0))
            expl = f"Reposition {mv} ticks, then fire; {primary_seg}; covers {fh}/{max(ft, 1)} futures."
        elif plan_type_s == "move_then_wait_then_fire":
            mv = int(getattr(best, "move_steps", 0))
            wv = int(getattr(best, "wait_steps", 0))
            expl = (
                f"Reposition {mv} ticks, wait {wv} ticks, then fire; {primary_seg}; "
                f"covers {fh}/{max(ft, 1)} futures."
            )
        else:
            dn = self._plan_display_name(plan_type_s)
            expl = f"{dn}; {primary_seg}; covers {fh}/{max(ft, 1)} futures."

        if confidence == "LOW" and (int(getattr(best, "move_steps", 0)) > 0 or int(getattr(best, "wait_steps", 0)) > 0):
            expl = f"Weak staging (best available): {expl}"

        self.ai_explanation = expl

        tier = "HIGH" if confidence == "HIGH" else ("MED" if confidence == "MEDIUM" else "LOW")
        pdn = self._plan_display_name(plan_type_s)
        if confidence == "LOW" and (int(getattr(best, "move_steps", 0)) > 0 or int(getattr(best, "wait_steps", 0)) > 0):
            self.ai_plan_summary = f"AI {tier} | {pred_name_str} | weak staging | {pdn} | covers {fh}/{max(ft, 1)}"
        else:
            self.ai_plan_summary = f"AI {tier} | {pred_name_str} | {pdn} | covers {fh}/{max(ft, 1)}"

        tex = expl.replace('"', "'")
        ph = str(primary_hit).lower()
        print(
            "[ai-explain] "
            f"confidence={confidence} predictor={pred_name_str} plan={plan_type_s} "
            f"coverage={fh}/{max(ft, 1)} coverage_pct={cov:.3f} primary_hit={ph} "
            f"planner_ms={self.last_planner_ms:.1f} text=\"{tex}\""
        )

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

    def _replan(self, *, validate_diag: bool = True) -> TruckPlan | None:
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
        vok: bool | None = None
        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            sp = self.inferred_speed if self.inferred_speed is not None else float(C.AIRCRAFT_SPEED)
            if validate_diag and best is not None and self.inferred_direction is not None:
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
            elif not validate_diag:
                vok = None
        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            self._pva_last_validate_plan_ok = vok

        self._build_ai_inspector(best, diag)

        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            cov_h = ""
            if best is not None:
                bft = max(int(getattr(best, "futures_total", 0)), 1)
                bfh = int(getattr(best, "futures_hit", 0))
                cov_pct = bfh / bft
                cov_h = f"coverage={bfh}/{bft} coverage_pct={cov_pct:.3f}  "
            else:
                cov_h = "coverage=— coverage_pct=n/a  "
            ltd = ESCAPE_DEBUG_LAST.get("predictor_late_turn_dirs")
            late_turn_n = int(ltd) if isinstance(ltd, (int, float)) else 0
            print(
                f"  [pva-planner] predictor={diag.predictor_name}  "
                f"predictor_candidate_dirs={self._pva_last_predictor_candidate_dirs}  "
                f"strict_turn_dirs={len(self.pva_sam_threat_turn_dirs)}  "
                f"late_turn_dirs={late_turn_n}  "
                f"futures={diag.futures_evaluated}  {cov_h}"
                f"planner_ms={self.last_planner_ms:.1f}  "
                f"confidence={self.ai_confidence_label}  "
                f"validate_plan_ok={'skipped' if not validate_diag else vok}  "
                f"no_sol={diag.no_solution_reason or '—'}"
            )

        return best

    def _apply_truck_action(self) -> TruckPlan | None:
        s = self.state
        truck = s.sam_truck
        best: TruckPlan | None = None

        def _pva_action_explain(
            *,
            policy: str,
            plan: TruckPlan | None,
            reason: str,
        ) -> None:
            if not (self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING):
                return
            self._pva_capture_replay_stamp(policy=policy, plan=plan, reason=reason)
            fh = int(getattr(plan, "futures_hit", 0)) if plan is not None else 0
            ft_raw = int(getattr(plan, "futures_total", 0)) if plan is not None else 0
            ft = max(ft_raw, 1) if plan is not None else 0
            confidence = str(self.ai_confidence_label or "")
            vok = self._pva_last_validate_plan_ok
            validate_s = "skipped" if vok is None else ("true" if bool(vok) else "false")
            tag = " tag=RISKY_UNVALIDATED" if vok is False else ""
            cov_s = "—" if plan is None else f"{fh}/{ft}"
            safe_reason = reason.replace('"', "'")
            print(
                "[pva-action-explain] "
                f"policy={policy}{tag} confidence={confidence} coverage={cov_s} validate={validate_s} "
                f"reason=\"{safe_reason}\""
            )

        self._refresh_predictor()

        if truck.has_fired:
            if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
                self._pva_capture_replay_stamp(
                    policy="OBSERVING",
                    plan=None,
                    reason="Missile is in flight; observing the outcome.",
                )
            return None

        if self.inferred_direction is None or self.inferred_speed is None:
            self.last_action = f"waiting for radar lock (tick {s.tick})"
            print(f"  ACTION: waiting for radar lock (tick {s.tick})")
            self.ai_future_paths = []
            self.ai_confidence_label = "LOW"
            self.ai_explanation = "Waiting for radar lock before planning an intercept."
            self.ai_plan_summary = "AI LOW | Waiting for radar lock"
            if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
                self._pva_capture_replay_stamp(
                    policy="WAITING_RADAR",
                    plan=None,
                    reason="Waiting for radar lock before planning an intercept.",
                )
            return None

        # Experiment (PVA only): before firing, replan every tick for maximum reactivity.
        # Automatic mode retains the existing reuse interval.
        if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING and not truck.has_fired:
            # Smoothness: if the previous replan was heavy and we already have a staging plan
            # (move/wait only), reuse it for one tick instead of replanning again immediately.
            ap0 = self.active_plan
            if (
                ap0 is not None
                and (int(ap0.move_steps) > 0 or int(ap0.wait_steps) > 0)
                and float(self.last_planner_ms) > 180.0
                and bool(self._pva_heavy_skip_armed)
            ):
                _pva_action_explain(
                    policy="HEAVY_REUSE",
                    plan=ap0,
                    reason="Reusing one staging step after an expensive planner tick to avoid lag.",
                )
                self._pva_heavy_skip_armed = False
                md0 = self._direction_name(ap0.move_direction) if ap0.move_direction else "NONE"
                print(
                    "[pva-replan-skip] reason=heavy_previous_keep_staging "
                    f"planner_ms={self.last_planner_ms:.1f} plan={ap0.plan_type} "
                    f"move={md0}x{ap0.move_steps} wait={ap0.wait_steps}"
                )
            else:
                print("[pva-replan] reason=every_tick_before_fire")
                # Reduce overhead: skip expensive validate_plan() diagnostic most ticks during forced replans.
                do_validate = (int(s.tick) % 5) == 0
                new_plan = self._replan(validate_diag=do_validate)
                if new_plan is not None:
                    self.active_plan = new_plan
                    self._pva_heavy_skip_armed = float(self.last_planner_ms) > 180.0
                elif self.active_plan is not None:
                    ap = self.active_plan
                    # Only keep old plans that still have staging steps; never keep an immediate-fire plan.
                    if int(ap.move_steps) > 0 or int(ap.wait_steps) > 0:
                        md = self._direction_name(ap.move_direction) if ap.move_direction else "NONE"
                        fd = self._direction_name(ap.fire_direction)
                        print(
                            f"[pva-plan-keep] reason=replan_none keeping={ap.plan_type} "
                            f"move={md}x{ap.move_steps} wait={ap.wait_steps} fire={fd} fire_tick={ap.fire_tick}"
                        )
                    else:
                        print(
                            f"[pva-plan-drop] reason=replan_none_old_plan_immediate_fire old={ap.plan_type} "
                            f"fire_tick={ap.fire_tick} intercept_tick={ap.intercept_tick}"
                        )
                        self.active_plan = None
            # If we still have no plan (or just dropped an unsafe immediate-fire plan), hold.
            if self.active_plan is None:
                _pva_action_explain(
                    policy="NO_PLAN_HOLD",
                    plan=None,
                    reason="No verified plan; refuses fake chase movement.",
                )
                self.last_action = "idle (no verified plan)"
                print("[pva-no-plan-hold] reason=no_verified_plan no_chase=True")
                return None
        elif self._plan_is_stale():
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
            self.last_action = "idle (no plan)"
            print("  ACTION: idle (no plan)")
            return None

        if best.move_steps > 0:
            policy = "VERIFIED_PLAN"
            if self.ai_confidence_label == "LOW":
                policy = "WEAK_STAGING"
            _pva_action_explain(
                policy=policy,
                plan=best,
                reason="Executing planner plan: staging move step.",
            )
            truck.direction = best.move_direction
            move_entity(truck, C.DT, s.grid, state=s)
            self.last_action = f"move {self._direction_name(best.move_direction)}"
            self.active_plan = replace(
                self._advance_plan_tick_fields(best),
                move_steps=best.move_steps - 1,
            )
            print(f"  ACTION: move  ({best.move_steps} steps left)")
        elif best.wait_steps > 0:
            policy = "VERIFIED_PLAN"
            if self.ai_confidence_label == "LOW":
                policy = "WEAK_STAGING"
            _pva_action_explain(
                policy=policy,
                plan=best,
                reason="Executing planner plan: staging wait step.",
            )
            self.last_action = "wait"
            self.active_plan = replace(
                self._advance_plan_tick_fields(best),
                wait_steps=best.wait_steps - 1,
            )
            print(f"  ACTION: wait  ({best.wait_steps} steps left)")
        else:
            # PVA policy: before the player uses their one turn, guard against obviously weak
            # immediate-fire plans. Movement/wait plans are always allowed.
            if (
                self.mode == self.MODE_PVA
                and self.pva_phase == self.PVA_RUNNING
                and (not truck.has_fired)
                and (not self.pva_turn_used)
                and int(best.move_steps) <= 0
                and int(best.wait_steps) <= 0
            ):
                fh = int(getattr(best, "futures_hit", 0))
                ft = int(getattr(best, "futures_total", 0))
                cov = (fh / ft) if ft > 0 else 0.0
                confidence = str(self.ai_confidence_label or "")
                if (confidence == "LOW") and (cov < 0.50):
                    _pva_action_explain(
                        policy="WEAK_FIRE_HELD",
                        plan=best,
                        reason="Refused a bad shot: LOW confidence and coverage below 0.50.",
                    )
                    self.last_action = "hold fire (weak plan)"
                    print(
                        "[pva-fire-hold] "
                        f"coverage={fh}/{max(ft,1)} coverage_pct={cov:.3f} "
                        f"confidence={confidence} plan={best.plan_type}"
                    )
                    self.active_plan = None
                    return None

            # Allowed to fire.
            _pva_action_explain(
                policy="VERIFIED_PLAN" if self.ai_confidence_label in ("HIGH", "MEDIUM") else "WEAK_STAGING",
                plan=best,
                reason="Executing planner plan: firing step.",
            )
            missile = launch_missile_in_direction(truck, C.MISSILE_SPEED, best.fire_direction)
            if missile is not None:
                s.missiles.append(missile)
                if self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
                    self._pva_capture_replay_stamp(
                        policy="FIRED",
                        plan=best,
                        reason="Executing planner plan: firing step.",
                    )
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

    def _run_step_automatic(self) -> None:
        t_step = time.perf_counter()
        s = self.state
        truck = s.sam_truck
        s.tick += 1
        self._update_radar()
        best = self._apply_truck_action()

        move_entity(s.aircraft, C.DT, s.grid, state=s)
        for missile in s.missiles:
            move_entity(missile, C.DT, s.grid, state=s)
        if (not s.intercepted) and check_interception(s.aircraft, s.missiles, s.grid):
            s.intercepted = True
            self._log_intercept_quality(tick=int(s.tick), mode="automatic")
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
        self._recompute_pva_turn_fan()

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
            raw_n = ESCAPE_DEBUG_LAST.get("pva_launch_raw_count")
            raw_n_disp = (
                int(raw_n) if isinstance(raw_n, (int, float)) else len(self.pva_valid_launch_dirs)
            )
            rj_s = ESCAPE_DEBUG_LAST.get("pva_launch_rejected_short")
            rj_a = ESCAPE_DEBUG_LAST.get("pva_launch_rejected_adjacent_short")
            rj_ne = ESCAPE_DEBUG_LAST.get("pva_launch_rejected_no_exit")
            rs = int(rj_s) if isinstance(rj_s, (int, float)) else 0
            ra = int(rj_a) if isinstance(rj_a, (int, float)) else 0
            rn = int(rj_ne) if isinstance(rj_ne, (int, float)) else 0
            ang_list = [round(math.degrees(math.atan2(d.y, d.x)), 1) for d in self.pva_valid_launch_dirs]
            print(
                f"[pva-launch] tile={self.pva_locked_tile}  sam=({sam_p.x:.2f},{sam_p.y:.2f})  "
                f"base_deg={base_deg}  cone_half_deg={ch}  cone_expand_extra_deg={ex}  "
                f"raw_dirs={raw_n_disp}  valid_dirs={len(self.pva_valid_launch_dirs)}  "
                f"rejected_short={rs}  rejected_adjacent_short={ra}  rejected_no_exit={rn}  "
                f"angles_deg={ang_list}"
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
            if len(self.pva_locked_exit_tiles) == 0:
                dn = self._direction_name(self.pva_locked_direction)
                print(
                    f"[pva-heading-reject] tile={self.pva_locked_tile} dir={dn} "
                    "reason=no_valid_exit_tiles"
                )
                self.pva_locked_direction = None
                self.pva_locked_exit_tiles = set()
                self.last_action = "Invalid heading: no valid exit corridor"
                return

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
            if len(self.pva_locked_exit_tiles) == 0:
                self.pva_locked_exit_tiles = project_valid_exit_tiles(
                    self.state.grid,
                    self.pva_locked_tile,
                    self.pva_locked_direction,
                    speed=float(C.AIRCRAFT_SPEED),
                    dt=float(C.DT),
                )
            if len(self.pva_locked_exit_tiles) == 0:
                dn = self._direction_name(self.pva_locked_direction)
                print(
                    f"[pva-heading-reject] tile={self.pva_locked_tile} dir={dn} "
                    "reason=no_valid_exit_tiles"
                )
                self.pva_phase = self.PVA_ANGLE_SELECT
                self.pva_locked_direction = None
                self.pva_locked_exit_tiles = set()
                self.pva_exit_stripe_half = 0
                self.pva_player_turn_dirs = []
                self.pva_sam_threat_turn_dirs = []
                self._dbg_turn_fan_key = None
                self._pva_logged_outside_turn_fan = False
                self.last_action = "Invalid heading: no valid exit corridor"
                return

            self.state.aircraft.position = tile_pos(self.pva_locked_tile)
            self.state.aircraft.direction = self.pva_locked_direction
            self.state.aircraft.speed = C.AIRCRAFT_SPEED
            self.state.aircraft_history.clear()
            self.state.tick = 0
            self.active_plan = None
            self._ticks_since_replan = 0
            self._predictor = ConstantVelocityPredictor()
            self._reset_ai_inspector()
            self.pva_phase = self.PVA_RUNNING
            self.pva_turn_used = False
            self._pva_turn_ptr = (1.0, 0.0)
            self._dbg_turn_fan_key = None
            self.pva_turn_hover_direction = None
            self._recompute_pva_turn_fan()
            print(
                f"[pva-turn] deployed tick={self.state.tick} "
                f"player_turn_dirs={len(self.pva_player_turn_dirs)} "
                f"sam_threat_turn_dirs={len(self.pva_sam_threat_turn_dirs)} "
                f"window=[{C.TURN_WINDOW_MIN},end] "
                f"hover={self._direction_name(self.pva_turn_hover_direction)}"
            )
            self.last_action = "Aircraft deployed"
            return

        if self.pva_phase == self.PVA_RUNNING and not self.pva_turn_used:
            if not self._pva_in_turn_window():
                self.last_action = "Turn unavailable"
                return
            if self.pva_turn_hover_direction is None:
                return
            sel = self.pva_turn_hover_direction
            self.state.aircraft.direction = sel
            self.pva_turn_used = True
            self.active_plan = None
            self._ticks_since_replan = 0
            self._refresh_predictor()
            print(
                f"[pva-turn] SELECTED dir={self._direction_name(sel)}  "
                f"free_turn={bool(C.PVA_PLAYER_TURN_FREE)}  "
                f"runtime_will_validate_exit=True"
            )
            self.last_action = f"TURN {self._direction_name(self.pva_turn_hover_direction)}"

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

    def _log_pva_result_once(
        self,
        *,
        result: str,
        tick: int,
        crossed: tuple[int, int] | None = None,
        locked: list[tuple[int, int]] | None = None,
        action: str = "",
    ) -> None:
        if self._pva_result_logged:
            return
        self._pva_result_logged = True
        if result == "INTERCEPTED":
            print(f'[pva-result] result=INTERCEPTED tick={tick} action="{action}"')
            return
        locked_s = locked if locked is not None else []
        print(
            f'[pva-result] result={result} tick={tick} crossed={crossed} locked={locked_s} action="{action}"'
        )

    def _log_intercept_quality(self, *, tick: int, mode: str) -> None:
        """Logging-only: how close was the interception to HIT_RADIUS."""
        s = self.state
        r = float(HIT_RADIUS)
        if r <= 0:
            return
        acp = s.aircraft.position
        best_dist = float("inf")
        best_idx = -1
        for i, m in enumerate(s.missiles):
            if not getattr(m, "active", False):
                continue
            d = acp.distance_to(m.position)
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx < 0 or not math.isfinite(best_dist):
            return
        ratio = best_dist / r
        if ratio > 0.75:
            qual = "CLOSE"
        elif ratio > 0.50:
            qual = "MEDIUM"
        else:
            qual = "CLEAN"
        print(
            f"[intercept-quality] mode={mode} tick={tick} missile={best_idx} "
            f"dist={best_dist:.4f} radius={r:.4f} ratio={ratio:.3f} quality={qual}"
        )

    def _move_pva_aircraft(self) -> None:
        s = self.state
        ac = s.aircraft
        next_pos = Vector2D(
            ac.position.x + ac.direction.x * ac.speed * C.DT,
            ac.position.y + ac.direction.y * ac.speed * C.DT,
        )
        prev = ac.position

        # If the segment crosses the *visible* board border, decide outcome based on whether the
        # crossing point lies on a locked (green) border segment.
        visual_in_locked, visual_tile, _edge = segment_crosses_locked_exit_display(
            s.grid,
            prev,
            next_pos,
            frozenset(self.pva_locked_exit_tiles),
        )
        if visual_tile is not None:
            locked_sorted = sorted(self.pva_locked_exit_tiles)
            crossed_dbg = crossed_exit_tile(prev, next_pos, s.grid)
            print(
                f"[pva-exit-check] prev=({prev.x:.4f},{prev.y:.4f}) next=({next_pos.x:.4f},{next_pos.y:.4f}) "
                f"visual_tile={visual_tile} crossed={crossed_dbg} locked={locked_sorted} "
                f"visual_in_locked={visual_in_locked} "
                f"result={'ESCAPED' if visual_in_locked else 'FAILED'}"
            )
            if visual_in_locked:
                s.escaped = True
                self.pva_phase = self.PVA_END
                self.pva_result_text = "ESCAPED"
                self.last_action = f"ESCAPED via {visual_tile}"
                self._log_pva_result_once(
                    result="ESCAPED",
                    tick=int(s.tick),
                    crossed=visual_tile,
                    locked=locked_sorted,
                    action=self.last_action,
                )
            else:
                s.failed = True
                self.pva_phase = self.PVA_END
                self.pva_result_text = "ILLEGAL EXIT"
                self.last_action = f"FAILED exit via {visual_tile}"
                self._log_pva_result_once(
                    result="FAILED_ILLEGAL_EXIT",
                    tick=int(s.tick),
                    crossed=visual_tile,
                    locked=locked_sorted,
                    action=self.last_action,
                )
            return

        if s.grid.in_bounds(next_pos):
            ac.position = next_pos
            return

        # Fallback (should be rare): out-of-bounds without a visual border intersection.
        exit_tile = crossed_exit_tile(prev, next_pos, s.grid)
        locked_sorted = sorted(self.pva_locked_exit_tiles)
        in_locked = exit_tile is not None and exit_tile in self.pva_locked_exit_tiles
        esc = in_locked
        print(
            f"[pva-exit-check] prev=({prev.x:.4f},{prev.y:.4f}) next=({next_pos.x:.4f},{next_pos.y:.4f}) "
            f"visual_tile=None crossed={exit_tile} locked={locked_sorted} in_locked={in_locked} "
            f"result={'ESCAPED' if esc else 'FAILED'}"
        )
        if esc:
            s.escaped = True
            self.pva_phase = self.PVA_END
            self.pva_result_text = "ESCAPED"
            self.last_action = f"ESCAPED via {exit_tile}"
            self._log_pva_result_once(
                result="ESCAPED",
                tick=int(s.tick),
                crossed=exit_tile,
                locked=locked_sorted,
                action=self.last_action,
            )
        else:
            s.failed = True
            self.pva_phase = self.PVA_END
            self.pva_result_text = "ILLEGAL EXIT"
            self.last_action = f"FAILED exit via {exit_tile}"
            self._log_pva_result_once(
                result="FAILED_ILLEGAL_EXIT",
                tick=int(s.tick),
                crossed=exit_tile,
                locked=locked_sorted,
                action=self.last_action,
            )

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
                move_entity(missile, C.DT, s.grid, state=s)
            if check_interception(s.aircraft, s.missiles, s.grid):
                s.intercepted = True
                self._log_intercept_quality(tick=int(s.tick), mode="pva")
                self.pva_phase = self.PVA_END
                self.pva_result_text = "INTERCEPTED"
                self.last_action = "INTERCEPTED"
                self._log_pva_result_once(
                    result="INTERCEPTED",
                    tick=int(s.tick),
                    action=self.last_action,
                )

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

        if self.mode == self.MODE_PVA:
            if self.pva_result_text:
                rt = str(self.pva_result_text).upper()
                if "INTERCEPTED" in rt:
                    self._pva_capture_replay_stamp(
                        policy="INTERCEPTED",
                        plan=None,
                        reason="Round ended: missile intercepted the aircraft.",
                    )
                elif "ESCAPED" in rt or s.escaped:
                    self._pva_capture_replay_stamp(
                        policy="ESCAPED",
                        plan=None,
                        reason="Round ended: you escaped through the valid corridor.",
                    )
                elif "ILLEGAL" in rt or (s.failed and not s.intercepted):
                    self._pva_capture_replay_stamp(
                        policy="ILLEGAL_EXIT",
                        plan=None,
                        reason="Round ended: exit outside the green corridor.",
                    )
            self._pva_append_replay_frame()

    # ------------------------------------------------------------------
    # Public tick runner
    # ------------------------------------------------------------------

    def run_step(self) -> None:
        if self.mode == self.MODE_AUTOMATIC:
            self._run_step_automatic()
        elif self.mode == self.MODE_PVA and self.pva_phase == self.PVA_RUNNING:
            self._run_step_pva()