from core.grid import Grid
from core.vector import Vector2D
from entities.base import BaseEntity
from entities.missile import Missile
from entities.aircraft import Aircraft
from game import constants as C


def move_entity(entity: BaseEntity, dt: float, grid: Grid, state=None) -> None:
    """
    Move entity one tick.

    Aircraft : out-of-bounds → mark state.failed = True (scenario over).
    Truck    : out-of-bounds → cancel move (stay put).
    Missile  : out-of-bounds → active = False.
    """
    if isinstance(entity, Missile) and not entity.active:
        return

    dx = entity.direction.x * entity.speed * dt
    dy = entity.direction.y * entity.speed * dt
    new_pos = Vector2D(entity.position.x + dx, entity.position.y + dy)

    if grid.in_bounds(new_pos):
        entity.position = new_pos
        if isinstance(entity, Missile):
            missile = entity
            missile.steps_alive += 1
            if missile.steps_alive >= C.MISSILE_MAX_STEPS:
                missile.active = False
    else:
        if isinstance(entity, Missile):
            entity.active = False
        elif isinstance(entity, Aircraft):
            if state is not None:
                state.failed = True
            # position stays at last valid spot so rendering shows it
        # Truck: just stay put (no move applied)