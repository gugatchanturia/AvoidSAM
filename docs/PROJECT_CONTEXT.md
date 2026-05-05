# AvoidSAM - Project Context for Cursor

This file is the persistent context file for the AvoidSAM project. It exists so Cursor or any AI coding assistant can work on the repository without losing the design history.

Cursor must treat this file as the project specification. If this file conflicts with a vague prompt, follow this file and ask the user for clarification. If this file conflicts with the current repository code, do not guess: inspect the current files, explain the mismatch, and ask before changing architecture.

---

## 1. Project identity

Project name: `AvoidSAM`

AvoidSAM is a 2D Python/Pygame tactical simulation and game. An aircraft moves through a bounded 2D grid world while a SAM truck tries to intercept it with a single non-homing missile.

The SAM truck is the AI agent. The aircraft is the target, and in Player-vs-Agent mode it is controlled by the player.

The project has evolved through several stages:

1. Basic automatic SAM-vs-aircraft simulation.
2. Multi-scenario Pygame visualizer.
3. Improved 32-direction movement and hit-radius collision.
4. Radar inference instead of perfect velocity knowledge.
5. Bounded/analytic SAM planner.
6. Player-vs-Agent mode where the player deploys the aircraft from an edge tile and may turn once.
7. Turn-aware prediction layer so the SAM reasons about possible player turns.

The core AI problem is:

```text
Given:
- SAM truck position
- inferred aircraft position
- inferred aircraft speed and direction
- truck speed
- missile speed
- finite planning horizon
- one-shot non-homing missile

Choose whether the SAM truck should:
1. fire now
2. wait then fire
3. move then fire
4. move then wait then fire
```

The SAM must plan under constraints. It does not get a homing missile. It does not get infinite ammunition. It does not get perfect velocity information at spawn.

---

## 2. Non-negotiable project rules

These rules must never be changed unless the user explicitly asks for a design change.

### 2.1 Do not redesign from scratch

Do not rewrite the project from scratch.

Do not collapse the project into one file.

Do not replace the current architecture with a different architecture.

Do not remove Automatic mode.

Do not remove Player-vs-Agent mode.

Do not remove the current planner abstractions unless explicitly requested.

Do not remove the `TargetPredictor` abstraction.

Do not remove diagnostic output.

Do not remove bounded search protections.

Do not reintroduce the old unbounded 32-direction brute force that froze the game.

### 2.2 Minimal changes only

When asked to fix or add something, change only the files required for that task.

Do not refactor unrelated files.

Do not rename public functions/classes unless required and approved.

Do not change constants for balance unless the task is explicitly about balance.

Do not change visual UI structure unless the task is explicitly about UI.

### 2.3 Runtime and planner must agree

The runtime rules and planner simulation rules must match.

This applies to:

```text
- aircraft leaving the map
- truck boundary behavior
- missile boundary behavior
- missile lifetime
- collision / hit radius
- direction system
- radar inference
- PVA turn legality
- PVA valid exit tiles
```

Earlier bugs happened because runtime and planner used different assumptions. Do not repeat this.

---

## 3. World model

### 3.1 Grid

The world is a 2D grid.

Current standard constants:

```python
GRID_WIDTH = 32
GRID_HEIGHT = 18
```

The grid is conceptually discrete but entity positions are stored as floats.

Example:

```python
Vector2D(3.528, 4.9)
```

This means the object is physically located at that floating-point position in grid/world coordinates.

### 3.2 Grid helpers

`core/grid.py` should provide a dataclass similar to:

```python
@dataclass
class Grid:
    width: int
    height: int

    def to_tile(self, pos: Vector2D) -> tuple[int, int]:
        return (int(pos.x), int(pos.y))

    def in_bounds(self, pos: Vector2D) -> bool:
        return 0.0 <= pos.x < self.width and 0.0 <= pos.y < self.height
```

`to_tile()` is still useful for debug output and tile UI. It must not be used as the main collision rule.

### 3.3 Coordinate system

The coordinate system follows screen coordinates:

```text
x increases to the right
y increases downward
```

So:

```text
(1, 0)   = right / east
(-1, 0)  = left / west
(0, 1)   = down / south
(0, -1)  = up / north
```

Angles are computed with:

```python
math.atan2(direction.y, direction.x)
```

Angle 0 points to the right/east.

---

## 4. Direction system

### 4.1 Current design

The project originally used 8 compass directions. It was upgraded to 32 directions.

The current direction model lives in:

```text
core/directions.py
```

Expected structure:

```python
@dataclass(frozen=True)
class DirectionVector:
    x: float
    y: float
    index: int
```

The module should expose:

```python
DIRECTIONS: list[DirectionVector]
nearest_direction(dx: float, dy: float) -> DirectionVector
direction_angle(d: DirectionVector) -> float
```

### 4.2 32-direction requirement

`DIRECTIONS` contains 32 evenly spaced unit vectors.

```text
360 / 32 = 11.25 degrees
```

Generation should be equivalent to:

```python
step = 2 * math.pi / 32
for i in range(32):
    DirectionVector(
        x=math.cos(i * step),
        y=math.sin(i * step),
        index=i,
    )
```

All direction vectors must be normalized.

Never reintroduce faster diagonal movement.

Never silently downgrade to 8 directions.

Do not use an old `Direction` enum unless the user explicitly asks to revert the direction system.

### 4.3 Direction use

The same direction system applies to:

```text
- aircraft movement
- SAM truck movement
- missile movement
- planner search
- PVA launch headings
- PVA turn headings
- rendering orientation
```

---

## 5. Vector system

`core/vector.py` should contain `Vector2D`.

Expected operations:

```python
x: float
y: float
__add__
__sub__
__mul__
__rmul__
length()
normalized()
distance_to(other)
readable __repr__
```

Readable repr is important for console debugging:

```python
Vector2D(10.000, 14.000)
```

---

## 6. Entity model

### 6.1 Base entity

Entities are expected to have:

```python
position: Vector2D
speed: float
direction: DirectionVector
```

### 6.2 Aircraft

File:

```text
entities/aircraft.py
```

Current aircraft is intentionally simple:

```python
@dataclass
class Aircraft(BaseEntity):
    pass
```

Aircraft behavior is controlled by `game/app.py` and movement systems.

In Automatic mode, the aircraft follows scenario direction.

In Player-vs-Agent mode, the player selects spawn and initial heading, then may turn once.

### 6.3 SAM truck

File:

```text
entities/sam_truck.py
```

Expected structure:

```python
@dataclass
class SAMTruck(BaseEntity):
    has_fired: bool = False
```

The truck:

```text
- is AI-controlled
- can move before firing
- can wait before firing
- can fire only one missile
- cannot leave the map
```

Do not restore obsolete fields such as:

```text
planned_move_direction
planned_move_steps_remaining
planned_wait_steps_remaining
planned_fire_direction
```

Those belonged to an older committed-plan design and became obsolete after the planner evolved.

### 6.4 Missile

File:

```text
entities/missile.py
```

Expected current/near-current structure:

```python
@dataclass
class Missile(BaseEntity):
    active: bool = True
    steps_alive: int = 0
```

Missile rules:

```text
- one-shot
- non-homing
- fixed direction after launch
- moves while active
- deactivates out of bounds
- deactivates when lifetime expires
```

Do not make missiles home unless explicitly asked.

---

## 7. Constants and their meaning

Main constants live in:

```text
game/constants.py
```

Current intended values may be:

```python
GRID_WIDTH = 32
GRID_HEIGHT = 18

TICK_RATE = 8
DT = 1.0 / TICK_RATE

AIRCRAFT_SPEED = 5.0
MISSILE_SPEED = 3.5
TRUCK_SPEED = 2.25

MISSILE_MAX_STEPS = 96
PLANNING_HORIZON = 40
MAX_STEPS = 120

RANDOMIZE_SAM_SPAWN = True
SAM_SPAWN_HALF = 3

TURN_WINDOW_MIN = 6
TURN_WINDOW_MAX = 20
MAX_TURN_ANGLE_DEG = 120.0
TURN_AWARE_MAX_PATHS = 24
```

If the repository has slightly different values, inspect first and do not blindly overwrite unless the task is about constants.

### 7.1 `TICK_RATE` and `DT`

`TICK_RATE` controls simulation ticks per second.

Current baseline:

```python
TICK_RATE = 8
DT = 1.0 / TICK_RATE
```

Changing `TICK_RATE` changes:

```text
- movement distance per tick
- planner geometry
- missile verification
- radar inference
- visual pacing
- lifetime interpretation
```

Do not casually change it.

### 7.2 Speeds

Current intended balance:

```python
AIRCRAFT_SPEED = 5.0
MISSILE_SPEED = 3.5
TRUCK_SPEED = 2.25
```

Earlier baseline values were lower for the aircraft and truck. The newer values are part of current balance experiments.

Do not randomly change speeds to force behavior. If changing balance, explain expected effect.

### 7.3 `MISSILE_MAX_STEPS`

Runtime missile lifetime in ticks.

If `MISSILE_MAX_STEPS = 96`, the missile should be allowed to move for exactly 96 ticks before deactivating.

Avoid off-by-one errors.

### 7.4 `PLANNING_HORIZON`

Planner lookahead/search horizon.

This is intentionally allowed to be shorter than missile lifetime for performance.

Important distinction:

```text
MISSILE_MAX_STEPS = runtime lifetime
PLANNING_HORIZON = planner search budget
```

The planner must not plan beyond missile lifetime.

Effective verification horizon should be no more than:

```python
min(PLANNING_HORIZON, MISSILE_MAX_STEPS)
```

### 7.5 `SAM_SPAWN_HALF`

Used for randomized SAM center spawn in PVA mode.

If:

```python
SAM_SPAWN_HALF = 3
```

then the SAM randomization zone is a 7x7 block around the grid center.

Important: randomized spawn means randomized SAM truck spawn in a central block. It does not mean random aircraft spawn.

### 7.6 Turn window constants

```python
TURN_WINDOW_MIN
TURN_WINDOW_MAX
```

These are ticks from aircraft deployment, not ticks from each replan.

If current game tick is already inside the turn window, the remaining predictor turn window must be adjusted relative to current tick.

Example:

```text
TURN_WINDOW_MIN = 6
TURN_WINDOW_MAX = 20
current tick = 12
remaining turn_min = 0
remaining turn_max = 8
```

After `TURN_WINDOW_MAX`, no future turn remains and PVA should use constant-velocity prediction unless the player has already turned.

### 7.7 `MAX_TURN_ANGLE_DEG`

Maximum angle the player can turn relative to current heading.

This prevents impossible instant reversal and keeps PVA fair.

### 7.8 `TURN_AWARE_MAX_PATHS`

Maximum number of candidate futures generated by `TurnAwarePredictor`.

This prevents planner explosion.

---

## 8. Movement rules

File:

```text
systems/movement_system.py
```

Movement formula:

```python
dx = entity.direction.x * entity.speed * dt
dy = entity.direction.y * entity.speed * dt
new_pos = Vector2D(entity.position.x + dx, entity.position.y + dy)
```

### 8.1 Aircraft movement

Automatic mode:

```text
If aircraft leaves bounds, scenario ends as SAM failure / aircraft escape.
```

Current code may use `state.failed = True` in automatic mode. If adding clearer `escaped` semantics, do it deliberately.

PVA mode uses custom aircraft movement in `App._move_pva_aircraft()` because it must check valid exit tiles.

### 8.2 Truck movement

Truck cannot leave bounds.

If a truck move would leave bounds, cancel that move and keep the old position.

### 8.3 Missile movement

Missile movement rules:

```text
- inactive missile does not move
- active missile computes next position
- if next position out of bounds: active = False
- otherwise position updates
- steps_alive increments after successful movement
- if steps_alive >= MISSILE_MAX_STEPS: active = False
```

Do not increment lifetime before movement in a way that shortens lifetime by one tick.

---

## 9. Collision / interception

File:

```text
systems/collision_system.py
```

Collision is distance-based.

Expected:

```python
HIT_RADIUS = 0.4
```

Rule:

```python
if aircraft.position.distance_to(missile.position) <= HIT_RADIUS:
    intercepted = True
```

Interpretation:

```text
HIT_RADIUS is physical contact tolerance.
It is not a blast radius.
```

Do not restore same-tile collision.

Old invalid rule:

```python
grid.to_tile(aircraft.position) == grid.to_tile(missile.position)
```

That rule caused fake hits/misses and should not return.

---

## 10. Radar / information model

The SAM does not know aircraft velocity instantly at spawn.

Radar sees exact aircraft position each tick.

Velocity is inferred from position history.

State field:

```python
aircraft_history: list[Vector2D]
```

Each tick:

```python
state.aircraft_history.append(current_aircraft_position)
```

Once at least two observations exist:

```python
delta = p1 - p0
inferred_speed = delta.length() / DT
inferred_direction = nearest_direction(delta.x, delta.y)
```

Before radar lock:

```text
SAM waits.
Planner returns no plan.
```

Tick semantics:

```text
Tick 1:
- observe first position
- no velocity yet
- SAM waits

Tick 2:
- observe second position
- infer velocity
- SAM may plan/fire on the same tick
```

Do not delay until tick 3 unless explicitly requested.

---

## 11. Planner architecture

Main planner file:

```text
systems/launch_system.py
```

The planner has evolved through several versions. The current intended planner is bounded and often analytic-first.

It must not be replaced with slow brute-force search.

### 11.1 TruckPlan

Current/near-current `TruckPlan` includes fields similar to:

```python
@dataclass(frozen=True)
class TruckPlan:
    plan_type: str
    move_direction: DirectionVector | None
    move_steps: int
    wait_steps: int
    fire_direction: DirectionVector
    missile_steps: int
    fire_tick: int
    intercept_tick: int
    futures_hit: int = 1
    futures_total: int = 1
    primary_hit: bool = False
    primary_intercept_tick: int = 10**9
    weighted_hits: int = 0
```

Do not manually rebuild `TruckPlan` without preserving all fields.

When decrementing active plan fields, use:

```python
from dataclasses import replace

self.active_plan = replace(
    best,
    move_steps=best.move_steps - 1,
    fire_tick=max(0, best.fire_tick - 1),
    intercept_tick=max(0, best.intercept_tick - 1),
)
```

### 11.2 Plan categories

The planner evaluates:

```text
fire_now
wait_then_fire
move_then_fire
move_then_wait_then_fire
```

### 11.3 Bounded search

The planner must keep search bounded.

Known tuning constants may include:

```python
_MAX_MOVE_STEPS
_MAX_WAIT_STEPS
_SNAP_NEIGHBOURS
_MISSILE_DIR_BUDGET
_TRUCK_DIRS_FAST
_TRUCK_DIRS_MED
```

These exist to avoid combinatorial explosion.

The 32-direction brute-force issue:

```text
32 directions x horizon x move steps x wait steps x missile verification
```

previously caused the app to freeze or be killed by the OS. Do not reintroduce that.

### 11.4 Analytic-first missile search

A later planner design uses analytic intercept solving to avoid simulating every missile direction.

Core concept:

```text
Given fire position q, aircraft position a0 at fire time, aircraft velocity va, and missile speed vm:
Solve ||a0 + va*t - q|| = vm*t for t >= 0.
```

Then:

```text
- compute ideal continuous direction
- snap to nearest 32-direction bin(s)
- verify snapped direction by exact simulation
```

Important: analytic solve is not enough by itself. Snapped direction must be verified.

### 11.5 Multi-future prediction

Planner can use:

```python
predictor.predict_set(...)
```

This allows PVA mode to provide several plausible aircraft futures.

Planner scoring should prefer plans that hit the primary future and/or robustly hit multiple futures.

Do not reduce PVA turn-aware planning to a single arbitrary path unless explicitly requested.

### 11.6 Diagnostics

PlannerDiagnostic should remain.

Useful fields include:

```python
candidates_evaluated
directions_verified
fallback_used
any_continuous
futures_evaluated
no_solution_reason
predictor_name
```

Diagnostics must be honest.

Do not claim “physically impossible” unless actually proven.

No-solution messages should distinguish:

```text
- no radar lock
- no futures generated
- no legal turn futures
- futures generated but no verified shot
- discrete direction limitation
- horizon/lifetime limit
- aircraft escaped before intercept
```

---

## 12. Target predictor architecture

File:

```text
systems/target_predictor.py
```

### 12.1 TargetPredictor abstraction

Expected abstraction:

```python
class TargetPredictor(ABC):
    def predict(...) -> list[Vector2D]:
        ...

    def predict_set(...) -> list[list[Vector2D]]:
        return [self.predict(...)]

    @property
    def name(self) -> str:
        ...
```

### 12.2 ConstantVelocityPredictor

Used in Automatic mode.

Also used in PVA after the player has already used the one allowed turn, because the post-turn heading becomes known.

It predicts a single straight-line path until boundary or horizon.

### 12.3 TurnAwarePredictor

Used in PVA mode before the player has used the one allowed turn and while the turn window remains open.

It should generate a bounded set of plausible future paths.

It must consider:

```text
- current position
- current inferred direction
- speed
- grid
- remaining turn window
- legal post-turn directions
- valid_exit_tiles
- max path count
```

It must not use only a single midpoint turn tick.

It must sample multiple turn ticks.

Turn ticks are relative to the current prediction call, but the App must convert the absolute deployment turn window into a remaining window before constructing the predictor.

### 12.4 TurnAwarePredictor exit filtering

Important bug to avoid:

If the simulation helper stops before adding an out-of-bounds position, then checking `path[-1]` for out-of-bounds will never detect exits.

Correct approach:

```text
simulate step
if next_pos is out of bounds:
    compute exit_tile = crossed_exit_tile(current_pos, next_pos, grid)
    return path, exit_tile
```

A future path is legal if:

```text
- it stays in bounds through horizon, or
- it exits through a tile in valid_exit_tiles
```

A future path is illegal if:

```text
- it exits through a tile not in valid_exit_tiles
```

Use the same `crossed_exit_tile()` logic as PVA runtime if possible.

Do not duplicate contradictory exit detection logic.

---

## 13. Automatic mode

Automatic mode is the scenario viewer / planner test environment.

It runs predefined scenarios from:

```text
game/scenarios.py
```

It should use:

```python
ConstantVelocityPredictor()
```

Automatic mode should remain useful for:

```text
- debugging planner behavior
- testing no-solution cases
- checking performance
- inspecting missile/truck movement
```

Automatic mode should not be removed or replaced by PVA mode.

---

## 14. Player-vs-Agent mode

PVA mode is the playable game mode.

### 14.1 Concept

The player controls aircraft deployment and one later turn.

The SAM truck is controlled by the AI.

The goal of the player is to escape through a legal valid exit sector.

The goal of the SAM is to intercept with its single non-homing missile.

### 14.2 PVA phases

Expected phases:

```python
PVA_TILE_SELECT
PVA_ANGLE_SELECT
PVA_CONFIRM
PVA_RUNNING
PVA_END
```

Flow:

```text
1. tile_select:
   - player hovers/selects a border tile
   - left click locks tile

2. angle_select:
   - player selects legal initial heading
   - left click locks heading
   - right click goes back to tile_select

3. confirm:
   - left click deploys aircraft
   - right click goes back to angle_select

4. running:
   - aircraft moves
   - player may turn once
   - SAM plans/acts

5. end:
   - intercepted, escaped, failed, or timeout
```

### 14.3 PVA spawn rule

The aircraft must spawn on a border tile.

Corner tiles are allowed.

Initial heading must point inward.

Initial heading must not point outward.

Initial heading must not instantly leave the map.

Initial heading must not allow obvious edge-skimming cheese.

### 14.4 Grouped edge heading fairness

Legal launch headings should be grouped by edge/corner region.

The goal is that nearby edge tiles share sensible allowed heading cones, rather than each tile having fragile local geometry.

Actual corners:

```text
top-left     -> base direction down-right
top-right    -> base direction down-left
bottom-left  -> base direction up-right
bottom-right -> base direction up-left
```

Center edge regions should point perpendicular inward.

Near-corner edge regions should point diagonally inward-ish.

Do not rotate an actual corner diagonal again.

### 14.5 Valid exit tiles

PVA uses a legal exit sector.

The player only succeeds if the aircraft leaves the map through one of the valid exit tiles.

`pva_locked_exit_tiles` is the committed set after heading selection.

Valid exit tiles must be used by:

```text
- rendering overlays
- runtime escape check
- TurnAwarePredictor filtering
```

Do not compute valid exit tiles only for display and then ignore them in prediction.

### 14.6 Valid exit sector generation

File:

```text
game/pva_rules.py
```

Function:

```python
project_valid_exit_tiles(...)
```

The valid exit sector should include:

```text
- straight projected exit sector from initial heading
- plausible one-turn exit sectors based on turn window and legal turns
```

It must not return all border tiles.

It must not include impossible outward exits.

It must be bounded and deterministic.

If the function accepts parameters such as `speed`, `dt`, `turn_window_min`, `turn_window_max`, it should respect those parameters and not overwrite them internally without reason.

### 14.7 Player turn

The player may turn once.

PVA turn directions should be constrained by a public function in `pva_rules.py`, such as:

```python
legal_turn_directions(
    grid: Grid,
    position: Vector2D,
    current_direction: DirectionVector,
    valid_exit_tiles: set[Tile],
    speed: float,
    dt: float,
) -> list[DirectionVector]
```

Rules:

```text
- turn direction must not immediately leave bounds
- turn direction must be within MAX_TURN_ANGLE_DEG of current heading
- turn direction should plausibly reach at least one valid_exit_tile
- if no legal turn directions exist, the player cannot turn
```

Do not fall back to raw `DIRECTIONS` if no legal turn directions exist.

Bad logic:

```python
dirs = legal_dirs if legal_dirs else DIRECTIONS
```

Correct logic:

```python
if not legal_dirs:
    pva_turn_hover_direction = None
    return
```

Before applying turn on click, verify the selected turn direction is still legal.

### 14.8 Turn window semantics

`TURN_WINDOW_MIN` and `TURN_WINDOW_MAX` are absolute ticks from deployment.

When building a predictor at current tick `s.tick`, convert to remaining turn ticks:

```python
turn_min_remaining = max(0, C.TURN_WINDOW_MIN - s.tick)
turn_max_remaining = max(0, C.TURN_WINDOW_MAX - s.tick)
```

If:

```python
s.tick > C.TURN_WINDOW_MAX
```

then no future turn remains, so use `ConstantVelocityPredictor`.

If player has already turned, use `ConstantVelocityPredictor`.

### 14.9 Randomized SAM spawn

Randomized spawn refers only to the SAM truck in PVA mode.

It does not mean random aircraft spawn.

The player still chooses aircraft spawn tile and heading.

If enabled:

```python
RANDOMIZE_SAM_SPAWN = True
SAM_SPAWN_HALF = 3
```

then the SAM truck spawns randomly inside a central 7x7 block.

If disabled, SAM spawns at fixed center.

Automatic scenarios must not be randomized unless explicitly requested.

---

## 15. Rendering / UI

Main rendering is currently in:

```text
main.py
```

`systems/render_system.py` may be empty or unused. Do not move rendering there unless explicitly requested.

### 15.1 Visual style

Current desired visuals:

```text
- minor grid lines thin/dim
- major grid lines slightly brighter/thicker
- SAM truck as rotated rectangle
- aircraft as pointy triangle after radar/direction known
- aircraft as alternate shape before direction known
- missile as small pointy triangle
- shapes point in their movement direction
```

### 15.2 UI modes

Main UI contains:

```text
- menu
- automatic system mode
- player-vs-agent mode
```

Automatic mode UI includes:

```text
- play/pause
- previous/next scenario
- scenario buttons
- performance/planner diagnostics
```

PVA UI includes:

```text
- restart
- menu
- tile selection overlay
- direction fan
- valid exit outlines
- turn wheel
- status text
- diagnostics
```

### 15.3 Turn wheel

Turn wheel should show legal turn directions only.

Do not show all 32 directions if only some turns are legal.

If no legal turns exist, draw no turn wheel or a clear no-legal-turn indicator.

---

## 16. Important file structure

Approximate current project structure:

```text
config/
  settings.py

core/
  __init__.py
  directions.py
  grid.py
  timer.py
  vector.py

entities/
  __init__.py
  aircraft.py
  base.py
  missile.py
  sam_truck.py

game/
  __init__.py
  app.py
  constants.py
  loop.py
  pva_rules.py
  scenarios.py
  state.py

systems/
  __init__.py
  collision_system.py
  launch_system.py
  movement_system.py
  radar_system.py
  render_system.py
  target_predictor.py

ui/
  __init__.py
  hud.py

tests/
  __init__.py
  test_collision.py
  test_launch.py
  test_movement.py

main.py
README.md
requirements.txt
```

Not all files are necessarily active. Inspect before editing.

---

## 17. Important active files

### 17.1 `main.py`

Pygame entry point and UI/rendering loop.

Handles:

```text
- window setup
- menu buttons
- automatic mode controls
- PVA mode controls
- drawing grid/entities/overlays
- fixed render loop
```

Do not rewrite this unless the task is UI-related.

### 17.2 `game/app.py`

Main orchestration layer.

Handles:

```text
- mode state
- scenario state
- PVA phase state
- radar inference
- predictor selection
- calling planner
- applying truck action
- moving aircraft/missiles
- collision check
- PVA deployment interaction
- PVA turn interaction
```

This is a sensitive file. Modify carefully.

### 17.3 `systems/launch_system.py`

SAM planning and missile launch logic.

This is the main intelligence file.

Do not rewrite casually.

### 17.4 `systems/target_predictor.py`

Prediction abstraction.

Contains:

```text
TargetPredictor
ConstantVelocityPredictor
TurnAwarePredictor
SingleTurnPredictor placeholder/stub
```

PVA turn-aware behavior belongs here.

### 17.5 `game/pva_rules.py`

PVA legality rules.

Contains:

```text
all_border_tiles
is_border_tile
tile_pos
legal_launch_directions
project_valid_exit_tiles
crossed_exit_tile
legal_turn_directions if implemented
```

App and rendering should use public functions from this file.

Do not import private helpers such as `_segment_cone_for_tile` into `app.py`.

### 17.6 `systems/movement_system.py`

Runtime movement.

Must enforce boundary and missile lifetime rules.

### 17.7 `systems/collision_system.py`

Distance-based hit detection.

### 17.8 `game/state.py`

Holds current runtime `GameState`.

Expected fields include:

```python
grid: Grid
aircraft: Aircraft
sam_truck: SAMTruck
missiles: list[Missile]
tick: int
intercepted: bool
failed: bool
escaped: bool
aircraft_history: list[Vector2D]
```

---

## 18. Known AI failure patterns to avoid

### 18.1 Rebuilding too much

Previous AI output often rewrote large files unnecessarily.

Avoid this.

### 18.2 Fake implementation

Do not add fields or parameters and then fail to use them.

Example bad pattern:

```text
valid_exit_tiles is passed into predictor but not actually used for filtering.
```

### 18.3 Simulation cannot detect exits

Bad predictor pattern:

```text
simulate path until before out-of-bounds
then check if final path point is out-of-bounds
```

This never detects exits because the helper stopped before out-of-bounds.

Correct predictor simulation must return exit tile explicitly.

### 18.4 Wrong turn-window relativity

Bad pattern:

```python
TurnAwarePredictor(C.TURN_WINDOW_MIN, C.TURN_WINDOW_MAX, ...)
```

every replan.

Correct behavior converts to remaining window based on current tick.

### 18.5 Falling back to all directions

Bad pattern:

```python
dirs = legal_dirs if legal_dirs else DIRECTIONS
```

for player turn.

If no legal turns exist, player cannot turn.

### 18.6 Importing private rule helpers

Bad pattern:

```python
from game.pva_rules import _segment_cone_for_tile, _angle_diff
```

App should use public rule functions.

### 18.7 Forcing scenario 10 to shoot

Do not force a shot just because it looks visually frustrating.

If strict math says no solution under current rules, diagnostics should explain why.

Do not change physics to make one scenario shoot unless the task is explicitly a balance/design change.

### 18.8 Artificial movement costs

Earlier discussion considered adding fuel/movement costs to make SAM move more.

Decision: do not add artificial costs yet.

Reason: it can create weird decisions. Player dynamics and turn-aware uncertainty are the better way to make movement valuable.

---

## 19. Current development workflow

The user's intended workflow:

```text
GPT = architecture/planning/review
Cursor = code generation/editing
GPT = validation accept/reject
```

Cursor should not decide major architecture.

Cursor should implement clearly scoped tasks.

All Cursor output should be reviewed before being pasted/committed.

---

## 20. Git / safety expectations

Before large changes:

```bash
git status
git diff
```

Prefer small commits.

Do not mix unrelated changes in one commit.

Before applying AI-generated code, verify:

```text
- file names are correct
- changed files are expected
- imports are valid
- no old Direction enum returned
- no brute-force explosion
- PVA and automatic mode still launch
```

---

## 21. Testing expectations

Useful commands:

```bash
python main.py
pytest
```

If tests exist, run them after changes.

Manual checks:

```text
1. Automatic mode starts.
2. Scenario navigation works.
3. SAM waits for radar lock on first tick.
4. SAM plans after velocity inference.
5. Missile moves and deactivates out of bounds/lifetime.
6. PVA menu starts.
7. Player can select edge tile.
8. Player can select legal heading.
9. Valid exit tiles display.
10. Player can deploy.
11. Player can turn once only if legal turn exists.
12. SAM planner does not freeze.
13. Diagnostics remain visible.
```

---

## 22. Cursor instruction summary

Cursor must always follow these core rules:

```text
- Use current repository files as source of truth.
- Use PROJECT_CONTEXT.md as persistent design context.
- Never assume missing context.
- Never redesign unless explicitly asked.
- Change only requested files.
- Preserve Automatic and PVA modes.
- Preserve 32-direction system.
- Preserve radar inference.
- Preserve hit-radius collision.
- Preserve one-shot non-homing missile.
- Preserve bounded planner behavior.
- Ask before changing architecture or balance.
```
