from __future__ import annotations

from dataclasses import dataclass
from math import pi


@dataclass(frozen=True)
class PortDef:
    dx: float
    dy: float
    phi: float
    kind: str


@dataclass(frozen=True)
class BBox:
    width: float
    height: float


@dataclass(frozen=True)
class MRRCell:
    id: str
    center: tuple[float, float]
    ports: dict[str, PortDef]
    s_table: dict[tuple[str, str, int], float]
    bbox: BBox
    d_min_th: float

    def port_xy(self, port: str) -> tuple[float, float]:
        p = self.ports[port]
        return self.center[0] + p.dx, self.center[1] + p.dy


def default_add_drop_ports() -> dict[str, PortDef]:
    """Unrotated v1 add-drop MRR port geometry.

    The S-parameter CSV exposes A->B, A->D, C->B, and C->D. We map that to
    A=in, B=th, C=add, D=drop. With the usual add-drop ring sketch, the lower
    bus runs in the opposite direction, so add is on the lower-right and drop
    is on the lower-left.
    """
    return {
        "in": PortDef(dx=-8.0, dy=4.0, phi=0.0, kind="in"),
        "th": PortDef(dx=8.0, dy=4.0, phi=0.0, kind="th"),
        "add": PortDef(dx=8.0, dy=-4.0, phi=0.0, kind="add"),
        "drop": PortDef(dx=-8.0, dy=-4.0, phi=0.0, kind="drop"),
    }


def port_for_wire(pair: tuple[int, int], wire: int, side: str) -> str:
    """Map a 2x2 switch wire to add-drop input/output ports."""
    upper, lower = pair
    if side == "input":
        return "in" if wire == upper else "add"
    if side == "output":
        return "th" if wire == upper else "drop"
    raise ValueError(f"unknown side {side!r}")


def state_for_transition(pair: tuple[int, int], wire_in: int, wire_out: int) -> int:
    """0 means bar/through, 1 means cross/drop."""
    if wire_in == wire_out:
        return 0
    if set(pair) == {wire_in, wire_out}:
        return 1
    raise ValueError(f"wire transition {wire_in}->{wire_out} is not inside pair {pair}")


TWO_PI = 2.0 * pi
