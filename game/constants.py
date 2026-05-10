GRID_WIDTH = 32
GRID_HEIGHT = 18

TICK_RATE = 8
DT = 1.0 / TICK_RATE

AIRCRAFT_SPEED = 5.0
MISSILE_SPEED = 3.5
TRUCK_SPEED = 2.25

# Debug logging: set to True to enable verbose debug output, False for quiet gameplay
DEBUG_LOG = False

# Missile lifetime: exactly this many successful in-bounds movement ticks before deactivation.
MISSILE_MAX_STEPS = 96

# Planner lookahead in ticks. Verification is additionally capped by MISSILE_MAX_STEPS in
# launch_system; PLANNING_HORIZON is deliberately smaller than MISSILE_MAX_STEPS for bounded
# predictor work at TICK_RATE = 8 while missiles can still fly MISSILE_MAX_STEPS in runtime.
# Reduced from 40 to 20 for better performance (still covers most scenarios).
PLANNING_HORIZON = 20

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

# PVA replanning: use interval-based reuse instead of every-tick replanning for performance
PVA_REPLAN_INTERVAL = 4
PVA_REPLAN_BUDGET_MS = 12.0

# Planner search size reduction for performance
# These parameters are passed to predictor_post_turn_candidates; lower values reduce search space
PVA_PREDICTOR_MAX_PRIMARY = 8
PVA_PREDICTOR_MAX_TOTAL = 10
PVA_PREDICTOR_LOOKAHEAD_STEPS = 20
