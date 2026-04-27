from entities.aircraft import Aircraft
from entities.missile import Missile
from core.grid import Grid

HIT_RADIUS = 0.4


def check_interception(aircraft: Aircraft, missiles: list[Missile], grid: Grid) -> bool:
    for missile in missiles:
        if not missile.active:
            continue
        dist = aircraft.position.distance_to(missile.position)
        if dist <= HIT_RADIUS:
            return True
    return False