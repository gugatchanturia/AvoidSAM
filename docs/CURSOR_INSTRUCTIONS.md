# Cursor Instructions for AvoidSAM

This file contains strict operating rules for Cursor or any AI coding assistant working on AvoidSAM.

Cursor must read and follow:

```text
docs/PROJECT_CONTEXT.md
```

before making any code changes.

---

## 1. Absolute instruction hierarchy

When working on AvoidSAM, follow this order:

```text
1. User's current explicit request
2. docs/PROJECT_CONTEXT.md
3. Current repository code
4. Existing style and naming
5. General programming knowledge
```

If the user's request conflicts with `PROJECT_CONTEXT.md`, ask for confirmation before changing design.

If repository code conflicts with `PROJECT_CONTEXT.md`, do not guess. Explain the mismatch and ask what state should be treated as source of truth.

---

## 2. Never assume missing context

Do not invent missing rules.

Do not invent new features.

Do not infer architecture changes without permission.

Do not assume a file is unused without checking imports/references.

Do not assume older code is obsolete unless the current task says so.

If something is unclear, ask.

---

## 3. No unsolicited refactors

Do not refactor unless explicitly requested.

Forbidden unless explicitly requested:

```text
- moving rendering out of main.py
- replacing launch_system.py architecture
- removing Automatic mode
- removing PVA mode
- replacing DirectionVector/DIRECTIONS
- changing constants for balance
- renaming public functions/classes
- changing folder structure
- replacing dataclasses with another model
- adding external dependencies
```

A fix should be as small as possible while remaining correct.

---

## 4. File modification rules

Only modify files relevant to the current task.

Before editing, identify the files that need changes.

After editing, list exactly which files changed and why.

If asked for full files, return full files.

If asked for a patch, return a patch.

Do not include unrelated formatting-only edits.

Do not reorder large sections unless needed.

Do not rewrite comments across the project unless the task is documentation.

---

## 5. Architecture preservation rules

Preserve these systems:

```text
core/
  directions.py
  vector.py
  grid.py

entities/
  base.py
  aircraft.py
  sam_truck.py
  missile.py

systems/
  movement_system.py
  collision_system.py
  launch_system.py
  target_predictor.py

game/
  constants.py
  state.py
  scenarios.py
  app.py
  pva_rules.py

main.py
```

Do not collapse or replace them.

---

## 6. Required project rules

Cursor must preserve:

```text
- 2D grid world
- float positions
- 32 DirectionVector movement
- no old Direction enum
- aircraft/truck/missile use normalized directions
- radar position observation and inferred velocity
- no perfect velocity at spawn
- one-shot missile
- non-homing missile
- missile fixed direction after launch
- missile deactivation out of bounds
- missile deactivation at lifetime
- truck cannot leave map
- aircraft escape/end behavior
- distance-based HIT_RADIUS collision
- Automatic mode
- Player-vs-Agent mode
- PVA edge spawn
- PVA legal initial heading
- PVA valid exit tiles
- PVA one-turn limit
- bounded planner search
- planner diagnostics
```

---

## 7. Planner-specific rules

`systems/launch_system.py` is sensitive.

Do not rewrite it unless the task specifically involves planner logic.

Do not reintroduce slow brute force.

Do not loop over all 32 missile directions for every candidate and every future unless bounded and justified.

Preserve:

```text
- TruckPlan
- PlannerDiagnostic
- plan categories
- bounded move/wait search
- analytic-first or candidate-direction missile solving
- snapped-direction verification
- multi-future support through predictor.predict_set()
```

If changing planner scoring, explain the impact.

If no solution is found, diagnostics should be honest and conservative.

Do not force a shot in no-solution cases.

---

## 8. PVA-specific rules

PVA rules belong in:

```text
game/pva_rules.py
```

`game/app.py` should call public functions from `pva_rules.py`.

Do not import private helper functions from `pva_rules.py` into `app.py`.

Bad:

```python
from game.pva_rules import _segment_cone_for_tile, _angle_diff
```

Good:

```python
from game.pva_rules import legal_turn_directions
```

PVA valid exit tiles must be used by:

```text
- UI overlay
- runtime escape check
- TurnAwarePredictor filtering
```

Player turn directions must be legal. If no legal turns exist, the player cannot turn.

Do not fall back to all `DIRECTIONS`.

---

## 9. Predictor-specific rules

`systems/target_predictor.py` contains:

```text
TargetPredictor
ConstantVelocityPredictor
TurnAwarePredictor
SingleTurnPredictor placeholder/stub
```

Automatic mode should generally use `ConstantVelocityPredictor`.

PVA before player turn and while turn window remains should use `TurnAwarePredictor`.

PVA after player turn should use `ConstantVelocityPredictor` because the new direction is known.

TurnAwarePredictor must:

```text
- sample multiple turn ticks
- use legal post-turn directions
- use valid_exit_tiles
- reject illegal exit futures
- keep in-bounds-through-horizon futures
- cap generated paths
```

Do not implement fake filtering that cannot detect exits.

If the simulation stops before out-of-bounds, return the crossed exit tile explicitly.

---

## 10. Movement-specific rules

`systems/movement_system.py` must preserve runtime rules:

```text
Aircraft:
- out of bounds ends scenario/fails/escapes according to mode logic

Truck:
- blocked by bounds

Missile:
- inactive missile does not move
- active missile moves in fixed direction
- out of bounds -> inactive
- lifetime reached -> inactive
```

Missile lifetime should allow exactly `MISSILE_MAX_STEPS` successful movement ticks.

---

## 11. Rendering-specific rules

Current rendering is mostly in `main.py`.

Do not move rendering to another file unless asked.

Preserve:

```text
- menu
- automatic UI
- PVA UI
- edge outlines
- valid exit overlays
- direction fan
- preview path
- turn wheel
- diagnostics panel
```

Turn wheel should show legal turn directions only.

Do not show all directions as legal turns.

---

## 12. Constants rules

Do not casually change values in `game/constants.py`.

If changing constants, explain:

```text
- what changed
- why it changed
- expected effect on gameplay/planner
- whether it affects Automatic mode, PVA mode, or both
```

Important constants:

```python
GRID_WIDTH
GRID_HEIGHT
TICK_RATE
DT
AIRCRAFT_SPEED
MISSILE_SPEED
TRUCK_SPEED
MISSILE_MAX_STEPS
PLANNING_HORIZON
MAX_STEPS
RANDOMIZE_SAM_SPAWN
SAM_SPAWN_HALF
TURN_WINDOW_MIN
TURN_WINDOW_MAX
MAX_TURN_ANGLE_DEG
TURN_AWARE_MAX_PATHS
```

---

## 13. Output rules for Cursor

When returning code:

```text
- return complete files if requested
- otherwise return precise patches or snippets as requested
- do not mix styles
- do not omit imports
- do not leave TODO placeholders unless explicitly asked
- do not claim something works without explaining what changed
```

After code, include:

```text
1. Files changed
2. Why each file changed
3. Behavior preserved
4. How to test
5. Risks / limitations
```

Keep the explanation short unless asked for detail.

---

## 14. Reusable Cursor prompt header

Paste this before any Cursor task:

```text
You are working on the AvoidSAM Python/Pygame project.

Before changing code, read and follow:
- docs/PROJECT_CONTEXT.md
- docs/CURSOR_INSTRUCTIONS.md

Use the current repository files as source of truth.
Do not redesign the project.
Do not refactor unrelated files.
Do not invent new features.
Do not change constants or balance unless explicitly asked.
Only modify the files required for this task.
Preserve Automatic mode and Player-vs-Agent mode.
Preserve the 32-direction DirectionVector system.
Preserve radar inference semantics.
Preserve one-shot non-homing missile behavior.
Preserve HIT_RADIUS distance collision.
Preserve bounded planner search and diagnostics.

If context is missing or a requested change conflicts with existing design, ask before coding.
Return full updated files for every changed file unless I ask for a patch.
At the end, list changed files and how to test.
```

---

## 15. Good Cursor prompt examples

### 15.1 Modify a single file

```text
Use the AvoidSAM context files.
Modify only game/pva_rules.py.

Task:
Fix legal_turn_directions() so it never falls back to raw DIRECTIONS when no legal turn exists.
If no legal turn is available, return an empty list.
Do not change launch heading logic.
Do not touch app.py or main.py.

Return the full updated game/pva_rules.py file.
```

### 15.2 Add logic without breaking architecture

```text
Use the AvoidSAM context files.

Task:
In systems/target_predictor.py, fix TurnAwarePredictor path filtering so it can detect when a simulated future exits the map.
The simulator must return both the path and crossed exit tile.
Use game.pva_rules.crossed_exit_tile() if possible.
Reject futures that exit through tiles not in valid_exit_tiles.
Keep futures that stay in-bounds through the horizon.

Do not change launch_system.py.
Do not change pva_rules.py unless absolutely necessary.
Return full updated files only for changed files.
```

### 15.3 Create a new helper function

```text
Use the AvoidSAM context files.
Modify only game/pva_rules.py unless imports require otherwise.

Task:
Add a public helper function legal_turn_directions(...) that centralizes PVA turn legality.
It should use grid, current aircraft position, current direction, valid_exit_tiles, speed, and dt.
It must not return directions that immediately leave bounds.
It must not return directions outside MAX_TURN_ANGLE_DEG.
It must not fall back to all DIRECTIONS.

Preserve existing public functions and behavior.
Return the full updated file.
```

### 15.4 Fix App behavior safely

```text
Use the AvoidSAM context files.
Modify only game/app.py.

Task:
Fix PVA predictor selection so TurnAwarePredictor is used only while the player has not turned and the turn window is still open.
TURN_WINDOW_MIN/MAX are absolute ticks from deployment, so convert them to remaining ticks using state.tick.
After the player uses the turn, force active_plan = None and use ConstantVelocityPredictor.

Do not modify target_predictor.py.
Do not modify pva_rules.py.
Return the full updated game/app.py file.
```

### 15.5 Ask for analysis before code

```text
Use the AvoidSAM context files.
Do not write code yet.
Inspect these files:
- game/app.py
- game/pva_rules.py
- systems/target_predictor.py

Tell me whether PVA valid_exit_tiles are used consistently by rendering, runtime escape checks, and TurnAwarePredictor.
List exact problems and exact files that need changes.
Do not modify code.
```
