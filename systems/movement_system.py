from core.grid import Grid
from core.vector import Vector2D
from entities.base import BaseEntity
from entities.missile import Missile

def move_entity(entity: BaseEntity, dt: float, grid: Grid) -> None:
    if hasattr(entity, "active") and not entity.active:
        return

    displacement = entity.direction.value * (entity.speed * dt)
    new_pos = entity.position + displacement

    if grid.in_bounds(new_pos):
        entity.position = new_pos
    else:
        if isinstance(entity, Missile):
            entity.active = False