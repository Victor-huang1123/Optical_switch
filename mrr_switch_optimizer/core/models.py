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
class CellGeometry:
    """Physical add-drop cell dimensions shared by placement and reporting."""

    bbox_width_um: float = 28.0
    bbox_height_um: float = 22.0
    port_dx_um: float = 8.0
    port_dy_um: float = 5.5
    d_min_th_um: float = 24.0
    waveguide_width_um: float = 0.45


# The 140-um-pitch gates are router-code regressions, not PDK-current geometry
# references.  A zero width deliberately preserves their historical
# centerline-spacing interpretation when copied into RoutingRules.
LEGACY_V2_CELL_GEOMETRY = CellGeometry(
    port_dy_um=4.0,
    waveguide_width_um=0.0,
)
V3_CELL_GEOMETRY = CellGeometry()
DEFAULT_CELL_GEOMETRY = V3_CELL_GEOMETRY


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


def default_add_drop_ports(
    geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> dict[str, PortDef]:
    """Unrotated v1 add-drop MRR port geometry (standard physical add-drop ring).

    The S-parameter CSV exposes A->B, A->D, C->B, and C->D. We map that to
    A=in, B=th, C=add, D=drop, which are the transmissions of a physical 4-port
    add-drop ring:

        top bus:     in   -> th     (left to right)
        bottom bus:  drop <- add    (right to left)

    so the physical sides are: in/drop on the LEFT, th/add on the RIGHT. The
    optical roles are a SEPARATE classification (in/add are inputs, th/drop are
    outputs); they must not be collapsed into the left/right side rule. This
    geometry must match the device the S-table describes -- do not move add/drop
    just to make routes monotonic.
    """
    return {
        "in": PortDef(dx=-geometry.port_dx_um, dy=geometry.port_dy_um, phi=0.0, kind="in"),
        "th": PortDef(dx=geometry.port_dx_um, dy=geometry.port_dy_um, phi=0.0, kind="th"),
        "add": PortDef(dx=geometry.port_dx_um, dy=-geometry.port_dy_um, phi=0.0, kind="add"),
        "drop": PortDef(dx=-geometry.port_dx_um, dy=-geometry.port_dy_um, phi=0.0, kind="drop"),
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
