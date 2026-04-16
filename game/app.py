import math
from core.grid import Grid
from core.vector import Vector2D
from core.directions import Direction
from entities.aircraft import Aircraft
from entities.sam_truck import SAMTruck
from entities.missile import Missile
from game.state import GameState
from game import constants as C
from systems.movement_system import move_entity
from systems.launch_system import launch_missile, find_launch_solution
from systems.collision_system import check_interception

_DIRECTION_MAP = {
    ( 0, -1): Direction.N,
    ( 1, -1): Direction.NE,
    ( 1,  0): Direction.E,
    ( 1,  1): Direction.SE,
    ( 0,  1): Direction.S,
    (-1,  1): Direction.SW,
    (-1,  0): Direction.W,
    (-1, -1): Direction.NW,
}

def _toward(src: Vector2D, dst: Vector2D) -> Direction:
    dx = dst.x - src.x
    dy = dst.y - src.y
    if dx == 0 and dy == 0:
        return Direction.N
    x_sign = 0 if dx == 0 else (1 if dx > 0 else -1)
    y_sign = 0 if dy == 0 else (1 if dy > 0 else -1)
    return _DIRECTION_MAP[(x_sign, y_sign)]


def _find_best_reposition(state: GameState) -> Direction:
    truck = state.sam_truck
    aircraft = state.aircraft

    best_launch_dir = None
    best_launch_step = None
    best_fallback_dir = None
    best_fallback_dist = None

    for move_dir in Direction:
        candidate_pos = truck.position + move_dir.value * (truck.speed * C.DT)
        if not state.grid.in_bounds(candidate_pos):
            continue

        temp_truck = SAMTruck(
            position=candidate_pos,
            speed=truck.speed,
            direction=move_dir,
            has_fired=False,
        )

        result = find_launch_solution(
            temp_truck, aircraft, C.MISSILE_SPEED, C.DT, state.grid
        )

        if result is not None:
            _, step = result
            if best_launch_step is None or step < best_launch_step:
                best_launch_step = step
                best_launch_dir = move_dir
        else:
            dx = candidate_pos.x - aircraft.position.x
            dy = candidate_pos.y - aircraft.position.y
            dist = math.sqrt(dx * dx + dy * dy)
            if best_fallback_dist is None or dist < best_fallback_dist:
                best_fallback_dist = dist
                best_fallback_dir = move_dir

    if best_launch_dir is not None:
        return best_launch_dir

    if best_fallback_dir is not None:
        return best_fallback_dir

    return _toward(truck.position, aircraft.position)


class App:
    def __init__(self):
        grid = Grid(width=C.GRID_WIDTH, height=C.GRID_HEIGHT)

        aircraft = Aircraft(
            position=Vector2D(2.0, 2.0),
            speed=C.AIRCRAFT_SPEED,
            direction=Direction.E,
        )

        sam_truck = SAMTruck(
            position=Vector2D(20.0, 12.0),
            speed=C.TRUCK_SPEED,
            direction=Direction.N,
        )

        self.state = GameState(
            grid=grid,
            aircraft=aircraft,
            sam_truck=sam_truck,
        )

    def run(self):
        s = self.state

        for _ in range(C.MAX_STEPS):
            s.tick += 1

            if not s.sam_truck.has_fired:
                # --- DEBUG: can we launch from current position? ---
                solution = find_launch_solution(
                    s.sam_truck, s.aircraft, C.MISSILE_SPEED, C.DT, s.grid
                )

                if solution is not None:
                    launch_dir, step = solution
                    print(f"  DEBUG: Launch possible NOW dir={launch_dir.name} in {step} steps")
                else:
                    print("  DEBUG: No launch solution from current position")

                # --- actual launch attempt ---
                missile = launch_missile(
                    s.sam_truck, s.aircraft, C.MISSILE_SPEED, C.DT, s.grid
                )

                if missile:
                    print(f"  DEBUG: >>> MISSILE FIRED dir={missile.direction.name}")
                    s.missiles.append(missile)
                else:
                    # --- DEBUG: evaluate reposition ---
                    best_dir = _find_best_reposition(s)
                    print(f"  DEBUG: Repositioning {best_dir.name}")

                    s.sam_truck.direction = best_dir
                    move_entity(s.sam_truck, C.DT, s.grid)

            move_entity(s.aircraft, C.DT, s.grid)

            for missile in s.missiles:
                move_entity(missile, C.DT, s.grid)

            if check_interception(s.aircraft, s.missiles, s.grid):
                s.intercepted = True

            ac_tile = s.aircraft.tile(s.grid)
            truck_tile = s.sam_truck.tile(s.grid)

            print(f"[Tick {s.tick:>3}]")
            print(f"  Aircraft : pos={s.aircraft.position}  tile={ac_tile}")
            print(f"  Truck    : pos={s.sam_truck.position}  tile={truck_tile}  fired={s.sam_truck.has_fired}")
            for i, m in enumerate(s.missiles):
                print(f"  M{i}       : pos={m.position}  tile={m.tile(s.grid)}  active={m.active}")

            if s.intercepted:
                print(f"\n*** INTERCEPTED at tick {s.tick}! ***\n")
                break