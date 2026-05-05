# AvoidSAM Development Workflow

This file defines the development loop for AvoidSAM when using GPT and Cursor together.

The goal is strict control over AI-generated code.

---

## 1. Roles

### 1.1 GPT role

GPT is used for:

```text
- architecture decisions
- planning
- reasoning about bugs
- reviewing Cursor output
- deciding accept/reject
- writing precise prompts for Cursor
- preserving project memory
```

GPT should not blindly generate large code changes unless explicitly requested.

GPT acts as the architect and reviewer.

### 1.2 Cursor role

Cursor is used for:

```text
- code generation
- editing specific files
- implementing well-scoped tasks
- running local context-aware modifications
```

Cursor is not the architect.

Cursor must follow:

```text
docs/PROJECT_CONTEXT.md
docs/CURSOR_INSTRUCTIONS.md
```

Cursor must not invent project direction.

### 1.3 User role

The user controls:

```text
- which feature/fix happens next
- which files are accepted
- when code is pasted/committed
- whether project design changes
```

The user does not blindly accept AI output.

---

## 2. Standard development loop

Use this loop for every task:

```text
1. Discuss task with GPT.
2. GPT clarifies architecture and risk.
3. GPT writes a strict Cursor prompt.
4. Paste prompt into Cursor.
5. Cursor generates code.
6. Bring Cursor output back to GPT.
7. GPT evaluates accept/reject.
8. If accepted, paste/apply files.
9. Run tests/manual check.
10. Commit small working change.
11. Repeat.
```

Never skip the GPT validation step for large changes.

---

## 3. What counts as a small safe task

Good task examples:

```text
- fix missile lifetime off-by-one in movement_system.py
- make PVA turn wheel show only legal turn directions
- make TurnAwarePredictor use crossed_exit_tile correctly
- add one diagnostic field
- fix predictor turn window remaining tick calculation
- update constants comments
```

Bad task examples:

```text
- rewrite the whole planner
- redesign PVA mode
- refactor the entire app
- improve all AI behavior at once
- optimize everything
- clean all files
```

Large tasks must be split.

---

## 4. Cursor prompt format

Every Cursor prompt should include the reusable header from:

```text
docs/CURSOR_INSTRUCTIONS.md
```

Then state:

```text
- exact task
- exact files allowed to change
- exact behavior required
- exact behavior that must not change
- expected output format
```

Example structure:

```text
[Reusable header]

Task:
...

Allowed files:
- ...

Do not change:
- ...

Return:
- full updated files for changed files

After coding:
- list changed files
- explain how to test
```

---

## 5. Validation checklist for Cursor output

Before accepting Cursor output, check:

```text
1. Did it modify only requested files?
2. Did it preserve architecture?
3. Did it preserve Automatic mode?
4. Did it preserve PVA mode?
5. Did it preserve 32-direction DirectionVector usage?
6. Did it avoid the old Direction enum?
7. Did it avoid unbounded brute force?
8. Did it avoid fake implementation?
9. Did it use valid_exit_tiles for real if relevant?
10. Did it avoid importing private helpers into App?
11. Did it preserve radar inference semantics?
12. Did it preserve one-shot non-homing missile behavior?
13. Did it preserve HIT_RADIUS collision?
14. Did it avoid random balance changes?
15. Does it run?
```

If any answer is no, reject or request correction.

---

## 6. Accept / reject language

### 6.1 Accept

Use when output is safe:

```text
Accepted. Paste these files into the project.
Then run:
python main.py
pytest
```

### 6.2 Conditional accept

Use when mostly safe but one small edit is needed:

```text
Mostly accepted, but fix X before pasting.
Do not change anything else.
```

### 6.3 Reject

Use when output is dangerous:

```text
Reject. Do not paste.
Main issues:
1. ...
2. ...
3. ...
Send this correction prompt to Cursor.
```

---

## 7. Git workflow

Before applying generated code:

```bash
git status
git diff
```

After applying and testing:

```bash
git add <changed-files>
git commit -m "Short precise message"
```

Commit only small coherent changes.

Avoid committing broken intermediate AI outputs.

---

## 8. Testing workflow

After any code change:

```bash
python main.py
```

If tests exist:

```bash
pytest
```

Manual smoke test:

```text
1. App opens.
2. Menu works.
3. Automatic mode starts.
4. Scenario controls work.
5. SAM waits for radar lock.
6. SAM eventually plans or reports honest no-solution.
7. PVA mode starts.
8. Edge tile selection works.
9. Heading selection works.
10. Valid exits display.
11. Deployment works.
12. Turn wheel works only for legal turns.
13. Missile fires/moves/deactivates.
14. Game does not freeze.
```

---

## 9. When to ask GPT before Cursor

Ask GPT before Cursor if the task involves:

```text
- planner architecture
- PVA legality rules
- target prediction
- constants/balance
- speed tuning
- scenario no-solution interpretation
- large refactor
- new gameplay mechanics
```

Cursor can directly handle small mechanical fixes only when the behavior is already fully specified.

---

## 10. Current high-priority project risks

Known risky areas:

```text
1. TurnAwarePredictor may appear to filter exits but fail to detect exits if simulation stops before out-of-bounds.
2. PVA turn window can be incorrectly treated as relative to every replan instead of relative to deployment tick.
3. Player turn logic can accidentally fall back to all DIRECTIONS.
4. App can accidentally import private pva_rules helpers and duplicate legality logic.
5. Planner can become too slow if bounded search protections are removed.
6. Scenario 10 should not be forced to shoot just because it looks visually frustrating.
7. Constants can easily unbalance the game if changed casually.
```

Any Cursor change touching these areas must be reviewed carefully.

---

## 11. Example Cursor tasks

### 11.1 Analysis-only prompt

```text
Use docs/PROJECT_CONTEXT.md and docs/CURSOR_INSTRUCTIONS.md.
Do not modify code.
Inspect game/app.py, game/pva_rules.py, and systems/target_predictor.py.
Tell me whether PVA turn-aware prediction correctly uses valid_exit_tiles and whether turn windows are relative to deployment tick.
Return exact issues and recommended files to change.
```

### 11.2 Single-file fix prompt

```text
Use docs/PROJECT_CONTEXT.md and docs/CURSOR_INSTRUCTIONS.md.
Modify only systems/movement_system.py.

Fix missile lifetime so MISSILE_MAX_STEPS means exactly that many successful movement ticks.
Do not change aircraft or truck behavior.
Do not change constants.
Return the full updated file.
```

### 11.3 Multi-file but bounded prompt

```text
Use docs/PROJECT_CONTEXT.md and docs/CURSOR_INSTRUCTIONS.md.
Modify only:
- systems/target_predictor.py
- game/app.py

Task:
Fix PVA TurnAwarePredictor usage so turn window is relative to current deployment tick.
If the player has already turned or the turn window has expired, use ConstantVelocityPredictor.
Do not touch launch_system.py.
Do not touch main.py.
Return full updated files for changed files.
```

---

## 12. Final principle

AvoidSAM must remain understandable.

A correct small change is better than a clever large rewrite.

If Cursor is uncertain, it must stop and ask.
