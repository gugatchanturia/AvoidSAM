from entities.aircraft import Aircraft
from entities.missile import Missile
from core.grid import Grid

def check_interception(aircraft: Aircraft, missiles: list[Missile], grid: Grid) -> bool:
    ac_tile = aircraft.tile(grid)

    for missile in missiles:
        if missile.active and missile.tile(grid) == ac_tile:
            return True

    return False