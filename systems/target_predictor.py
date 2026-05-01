"""
Target Motion Predictor abstraction.

The planner consumes predicted future aircraft positions from a predictor
object, not from direct access to aircraft internals.

Current implementation: ConstantVelocityPredictor
Future placeholder:      SingleTurnPredictor (for player one-turn evasion)

To add player evasion later:
  1. Implement SingleTurnPredictor with the same interface.
  2. Pass it into find_best_truck_plan() instead of ConstantVelocityPredictor.
  3. No other planner logic changes needed.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from core.vector import Vector2D
from core.directions import DirectionVector
from core.grid import Grid


class TargetPredictor(ABC):
    """
    Abstract interface: given current aircraft state, produce a list of
    predicted future positions.

    Index 0  = current position (before any movement).
    Index k  = position after k simulation ticks.

    Simulation must honour the runtime escape rule:
      if aircraft would leave grid bounds, stop appending.
    """

    @abstractmethod
    def predict(
        self,
        position:  Vector2D,
        direction: DirectionVector,
        speed:     float,
        dt:        float,
        grid:      Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class ConstantVelocityPredictor(TargetPredictor):
    """
    Assumes aircraft continues in a straight line at constant speed.
    Stops prediction the moment aircraft would leave the map (escape = failure).
    This is the only predictor used until player-controlled aircraft is added.
    """

    @property
    def name(self) -> str:
        return "ConstantVelocity"

    def predict(
        self,
        position:  Vector2D,
        direction: DirectionVector,
        speed:     float,
        dt:        float,
        grid:      Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        pos = Vector2D(position.x, position.y)
        out = [Vector2D(pos.x, pos.y)]
        for _ in range(max_steps):
            nx = pos.x + direction.x * speed * dt
            ny = pos.y + direction.y * speed * dt
            new_pos = Vector2D(nx, ny)
            if not grid.in_bounds(new_pos):
                break          # aircraft escapes — no further valid states
            pos = new_pos
            out.append(Vector2D(pos.x, pos.y))
        return out


# ---------------------------------------------------------------------------
# Future stub (not used yet)
# ---------------------------------------------------------------------------

class SingleTurnPredictor(TargetPredictor):
    """
    PLACEHOLDER — not implemented yet.

    Will model: aircraft flies straight until some trigger condition
    (e.g. enters engagement radius), then makes exactly one direction
    change to a new constant heading.

    Interface is identical to ConstantVelocityPredictor; the planner
    does not need to know which predictor is active.
    """

    @property
    def name(self) -> str:
        return "SingleTurn(stub)"

    def predict(
        self,
        position:  Vector2D,
        direction: DirectionVector,
        speed:     float,
        dt:        float,
        grid:      Grid,
        max_steps: int,
    ) -> list[Vector2D]:
        raise NotImplementedError(
            "SingleTurnPredictor is a future placeholder. "
            "Implement when player-controlled aircraft is added."
        )