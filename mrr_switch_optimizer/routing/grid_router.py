from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import _direction, _manhattan
from .types import EPS, Point, Segment

__all__ = [
    "RouterState",
    "NeighborMove",
    "HistoryCost",
    "orientation_axis",
    "opposite_orientation",
    "neighbor_moves",
]


@dataclass(frozen=True)
class RouterState:
    x_idx: int
    y_idx: int
    orientation: str = ""
    straight_run_um: float = 0.0


@dataclass(frozen=True)
class NeighborMove:
    next_state: RouterState
    segment: Segment
    move_type: str
    bend_point: Point | None = None
    crossing: object | None = None


@dataclass
class HistoryCost:
    penalties: dict[tuple[int, int, str], float] = field(default_factory=dict)
    step_penalty_um: float = 12.0

    def cost(self, state: RouterState) -> float:
        return self.penalties.get((state.x_idx, state.y_idx, state.orientation), 0.0)

    def bump_state(self, state: RouterState, amount: float | None = None) -> None:
        key = (state.x_idx, state.y_idx, state.orientation)
        self.penalties[key] = self.penalties.get(key, 0.0) + (
            self.step_penalty_um if amount is None else amount
        )

    def bump_segment(
        self,
        segment: Segment,
        x_tracks: tuple[float, ...],
        y_tracks: tuple[float, ...],
        amount: float | None = None,
    ) -> None:
        orientation = _segment_orientation_tag(segment)
        for x_idx, x in enumerate(x_tracks):
            for y_idx, y in enumerate(y_tracks):
                point = (x, y)
                if _point_on_axis_segment(point, segment):
                    self.bump_state(RouterState(x_idx, y_idx, orientation), amount)

    def bump_route(
        self,
        segments: tuple[Segment, ...] | list[Segment],
        x_tracks: tuple[float, ...],
        y_tracks: tuple[float, ...],
        amount: float | None = None,
    ) -> None:
        for segment in segments:
            self.bump_segment(segment, x_tracks, y_tracks, amount)


def neighbor_moves(
    state: RouterState,
    x_tracks: tuple[float, ...],
    y_tracks: tuple[float, ...],
    *,
    bend_spacing_um: float | None = None,
) -> tuple[NeighborMove, ...]:
    moves: list[NeighborMove] = []
    x_idx, y_idx = state.x_idx, state.y_idx
    current = (x_tracks[x_idx], y_tracks[y_idx])
    candidates = (
        (x_idx - 1, y_idx, "W"),
        (x_idx + 1, y_idx, "E"),
        (x_idx, y_idx - 1, "S"),
        (x_idx, y_idx + 1, "N"),
    )
    for next_x, next_y, orientation in candidates:
        if next_x < 0 or next_x >= len(x_tracks):
            continue
        if next_y < 0 or next_y >= len(y_tracks):
            continue
        if state.orientation and orientation == opposite_orientation(state.orientation):
            continue
        point = (x_tracks[next_x], y_tracks[next_y])
        turning = (
            bool(state.orientation)
            and orientation_axis(state.orientation) != orientation_axis(orientation)
        )
        if (
            bend_spacing_um is not None
            and turning
            and state.straight_run_um < bend_spacing_um - EPS
        ):
            continue
        if bend_spacing_um is None:
            straight_run_um = 0.0
        else:
            step_length_um = _manhattan(current, point)
            straight_run_um = min(
                bend_spacing_um,
                step_length_um
                if turning or not state.orientation
                else state.straight_run_um + step_length_um,
            )
        nxt = RouterState(
            next_x,
            next_y,
            orientation,
            round(straight_run_um, 6),
        )
        moves.append(
            NeighborMove(
                next_state=nxt,
                segment=(current, point),
                move_type="bend90" if turning else "straight",
                bend_point=current if turning else None,
            )
        )
    return tuple(moves)


def orientation_axis(orientation: str) -> str:
    if orientation in {"E", "W"}:
        return "h"
    if orientation in {"N", "S"}:
        return "v"
    return ""


def opposite_orientation(orientation: str) -> str:
    return {
        "E": "W",
        "W": "E",
        "N": "S",
        "S": "N",
    }.get(orientation, "")


def _segment_orientation_tag(segment: Segment) -> str:
    direction = _direction(segment[0], segment[1])
    if direction == "h":
        return "E" if segment[1][0] >= segment[0][0] else "W"
    return "N" if segment[1][1] >= segment[0][1] else "S"


def _point_on_axis_segment(point: Point, segment: Segment) -> bool:
    start, end = segment
    if abs(start[0] - end[0]) < EPS:
        if abs(point[0] - start[0]) >= EPS:
            return False
        y_lo, y_hi = sorted((start[1], end[1]))
        return y_lo - EPS <= point[1] <= y_hi + EPS
    if abs(start[1] - end[1]) >= EPS:
        return False
    if abs(point[1] - start[1]) >= EPS:
        return False
    x_lo, x_hi = sorted((start[0], end[0]))
    return x_lo - EPS <= point[0] <= x_hi + EPS
