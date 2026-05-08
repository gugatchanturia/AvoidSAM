GRID_WIDTH = 32
GRID_HEIGHT = 18

TICK_RATE = 8
DT = 1.0 / TICK_RATE

AIRCRAFT_SPEED = 5.0
MISSILE_SPEED = 3.5
TRUCK_SPEED = 2.25

# Missile lifetime: exactly this many successful in-bounds movement ticks before deactivation.
MISSILE_MAX_STEPS = 96

# Planner lookahead in ticks. Verification is additionally capped by MISSILE_MAX_STEPS in
# launch_system; PLANNING_HORIZON is deliberately smaller than MISSILE_MAX_STEPS for bounded
# predictor work at TICK_RATE = 8 while missiles can still fly MISSILE_MAX_STEPS in runtime.
PLANNING_HORIZON = 40

MAX_STEPS = 120

# Truck spawn in PVA: random tile in (2*SAM_SPAWN_HALF+1)^2 centered on grid center when enabled.
RANDOMIZE_SAM_SPAWN = True
SAM_SPAWN_HALF = 3

TURN_WINDOW_MIN = 6
TURN_WINDOW_MAX = 20
MAX_TURN_ANGLE_DEG = 60.0

# PVA launch cone half-width (degrees) around (SAM - spawn); corner tiles use the tighter value.
PVA_LAUNCH_CONE_HALF_DEG_EDGE = 36.0
PVA_LAUNCH_CONE_HALF_DEG_CORNER = 24.0

# If SAM cone yields too few choices, expand half-angle by this step up to PVA_LAUNCH_CONE_EXPAND_MAX_DEG
# (still rejects spawn-edge re-exit and out-of-board first step).
PVA_LAUNCH_CONE_EXPAND_STEP_DEG = 1.0
PVA_LAUNCH_CONE_EXPAND_MAX_DEG = 10.0
PVA_MIN_LAUNCH_DIRS_EDGE = 4
PVA_MIN_LAUNCH_DIRS_CORNER = 3

# Locked exit stripe along the first-hit edge (tiles = 2*stripe_half+1, typically 3–5).
PVA_EXIT_STRIPE_HALF = 2

# Player turn UI: all discrete headings (runtime still validates exit). SAM predictor uses only
# directions that can still reach locked exits when True.
PVA_PLAYER_TURN_FREE = True
PVA_SAM_CONSIDER_ONLY_WINNING_TURNS = True

# Bounded turn-aware futures for planner (combined with branching dirs / sampled turn ticks internally).
TURN_AWARE_MAX_PATHS = 24

# --- SAM Jammer (PVA only) ---
# Radius in grid tiles (world coordinates). Used only for UI + player turn blocking.
JAMMER_RADIUS = 4.0
# If True, jammer stays on after SAM fires its one missile.
JAMMER_ACTIVE_AFTER_FIRE = True
