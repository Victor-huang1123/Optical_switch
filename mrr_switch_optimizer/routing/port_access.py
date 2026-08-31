from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .geometry import (
    _inflate_obstacle,
    _orthogonal_crossing_point,
    _routing_obstacle,
    _same_point,
    _segment_contact_point,
    _segments_collinear_overlap,
)
from .types import EPS, Point, RoutingRules, Segment

if TYPE_CHECKING:
    from ..core.models import MRRCell
    from ..core.topology import Path


# Standard physical add-drop ring conventions. These are two SEPARATE
# classifications and must not be collapsed into a single left/right rule:
#   - physical side: which side of the cell bbox the port sits on
#   - optical role:  whether light enters or leaves the cell through the port
# The lower bus runs right->left, so an optical input (add) can be on the right
# while an optical output (drop) is on the left.
_PORT_SIDE: dict[str, str] = {"in": "left", "drop": "left", "th": "right", "add": "right"}
_PORT_OPTICAL_ROLE: dict[str, str] = {"in": "input", "add": "input", "th": "output", "drop": "output"}


def port_side(port: str) -> str:
    try:
        return _PORT_SIDE[port]
    except KeyError:
        raise ValueError(f"unknown MRR port {port!r}") from None


def port_optical_role(port: str) -> str:
    try:
        return _PORT_OPTICAL_ROLE[port]
    except KeyError:
        raise ValueError(f"unknown MRR port {port!r}") from None


def hop_crossing_class(src_port: str | None, dst_port: str | None) -> str:
    """Classify a hop's port-side pattern for repeated-crossing pricing.

    Bus endpoints do not have a cell-side port, so they stay conservative and
    use the normal high repeated-crossing penalty.
    """
    if src_port is None or dst_port is None:
        return "same_side"
    src_side = port_side(src_port)
    dst_side = port_side(dst_port)
    if src_side == "right" and dst_side == "left":
        return "inward"
    if src_side == "left" and dst_side == "right":
        return "outward"
    return "same_side"


@dataclass(frozen=True)
class PortAccessPoint:
    cell_id: str
    port: str
    owner_input: int | None
    port_xy: Point
    stub_xy: Point
    escape_xy: Point
    route_xy: Point
    side: str          # "left" | "right"  (physical cell side)
    optical_role: str  # "input" | "output"  (optical propagation role)


@dataclass(frozen=True)
class PortAccessRegion:
    owner_input: int | None
    segment: Segment
    cell_id: str
    port: str
    side: str          # "left" | "right"
    optical_role: str  # "input" | "output"
    reserved_for_owner_only: bool = True


@dataclass(frozen=True)
class PortAccessPlan:
    points_by_cell_port: dict[tuple[str, str], PortAccessPoint]
    reserved_regions: tuple[PortAccessRegion, ...]


def build_port_access_plan(
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
) -> PortAccessPlan:
    """Build the explicit port-access plan described in mrr_switch_optimizer/Lidar.md.

    Phase 1 keeps the existing escape coordinates and simply makes ownership and
    reserved access segments explicit for later owner-aware routing policies.
    """
    owner_by_cell_port: dict[tuple[str, str], int] = {}
    for path in paths:
        for step in path.steps:
            owner_by_cell_port.setdefault((step.mrr_id, step.in_port), path.input_port)
            owner_by_cell_port.setdefault((step.mrr_id, step.out_port), path.input_port)

    points: dict[tuple[str, str], PortAccessPoint] = {}
    regions: list[PortAccessRegion] = []
    for cell in cells.values():
        for port in cell.ports:
            owner = owner_by_cell_port.get((cell.id, port))
            port_xy = cell.port_xy(port)
            stub_xy = _port_stub_point(cell, port, port_stub_um)
            escape_xy = _port_escape_point(cell, port, rules, port_stub_um)
            route_xy = _port_route_point(cell, port, rules, port_stub_um)
            points[(cell.id, port)] = PortAccessPoint(
                cell_id=cell.id,
                port=port,
                owner_input=owner,
                port_xy=port_xy,
                stub_xy=stub_xy,
                escape_xy=escape_xy,
                route_xy=route_xy,
                side=port_side(port),
                optical_role=port_optical_role(port),
            )
            if owner is not None:
                regions.append(
                    PortAccessRegion(
                        owner_input=owner,
                        segment=(stub_xy, route_xy),
                        cell_id=cell.id,
                        port=port,
                        side=port_side(port),
                        optical_role=port_optical_role(port),
                    )
                )
    return PortAccessPlan(points, tuple(regions))


def port_access_segments(plan: PortAccessPlan) -> list[Segment]:
    return [region.segment for region in plan.reserved_regions]


def reserved_segments_for_net(plan: PortAccessPlan, owner_input: int | None) -> list[Segment]:
    """Port-access segments that ``owner_input`` must treat as reserved.

    Owner-aware policy: a net may use its OWN port-access regions (it has to, to
    enter/leave its MRR ports), so those are excluded. Every other net's reserved
    region -- and any region with no owner -- still blocks this net. This replaces
    the previous behavior where ``port_access_segments(plan)`` blocked all regions
    for all nets, including a net's own ports.
    """
    return [
        region.segment
        for region in plan.reserved_regions
        if region.owner_input != owner_input
    ]


@dataclass(frozen=True)
class PortAccessLegality:
    """Owner-aware view of the port-access plan handed to the A* segment check.

    Bundles the structured :class:`PortAccessPlan` with the id of the net being
    routed so a blocked candidate segment can be classified as a port-access
    ownership violation (with an explicit reason) instead of being reported as a
    generic blocker overlap. Pass ``current_net_id=None`` for searches that have
    no owning net (every region is then treated as foreign).
    """

    plan: PortAccessPlan
    current_net_id: int | None

    @classmethod
    def for_net(cls, plan: PortAccessPlan, current_net_id: int | None) -> "PortAccessLegality":
        return cls(plan, current_net_id)


def _region_conflict(
    segment: Segment,
    region: PortAccessRegion,
    current_net_id: int | None,
    rules: RoutingRules,
    allowed_touch_points: tuple[Point, ...],
) -> str | None:
    """Reason ``segment`` conflicts with one reserved region, or ``None``.

    External A* may never overlap a reserved access corridor -- not even the
    owner's own corridor, which the owner uses only through the explicit local
    stub/escape construction. Overlap and spacing are always rejected (the
    behavior-preserving policy, redundant with the flat blockers the router still
    passes). Orthogonal crossings are rejected when crossings are globally
    disabled, or -- inside a reserved region -- under ``rules.strict_port_access``.
    Strict mode additionally forbids a non-owner net from T-touching or end-
    blocking another net's region (the owner may touch its own corridor, since it
    enters through the local stub/escape). Owner vs foreign is encoded in the
    reason so a DRC/debug message can name the real cause.
    """
    rseg = region.segment
    owner = region.owner_input is not None and region.owner_input == current_net_id
    role = "owner" if owner else "foreign"
    if _segments_collinear_overlap(segment, rseg):
        return f"port_access_overlap_{role}"
    crossing = _orthogonal_crossing_point(segment, rseg)
    if crossing is not None and owner:
        return "port_access_crossing_owner"
    if crossing is not None and (not rules.allow_crossings or rules.strict_port_access):
        return f"port_access_crossing_{role}"
    if rules.strict_port_access and not owner:
        contact = _segment_contact_point(segment, rseg)
        if contact is not None and not any(
            _same_point(contact, allowed) for allowed in allowed_touch_points
        ):
            return "port_access_touch_foreign"
    return None


def port_access_conflict(
    segment: Segment,
    legality: PortAccessLegality | None,
    rules: RoutingRules,
    *,
    allowed_touch_points: tuple[Point, ...] = (),
) -> str | None:
    """First port-access reason ``segment`` is illegal, or ``None`` if legal.

    This is the single source of truth for owner-aware port-access legality used
    by the A* segment check. The returned reason string ``port_access_*`` lets
    the router surface an explicit ownership violation instead of a generic
    blocker failure.
    """
    detail = port_access_conflict_detail(
        segment,
        legality,
        rules,
        allowed_touch_points=allowed_touch_points,
    )
    return detail[0] if detail is not None else None


def port_access_conflict_detail(
    segment: Segment,
    legality: PortAccessLegality | None,
    rules: RoutingRules,
    *,
    allowed_touch_points: tuple[Point, ...] = (),
) -> tuple[str, PortAccessRegion] | None:
    """Detailed port-access conflict for diagnostics."""
    if legality is None:
        return None
    for region in legality.plan.reserved_regions:
        reason = _region_conflict(
            segment,
            region,
            legality.current_net_id,
            rules,
            allowed_touch_points,
        )
        if reason is not None:
            return reason, region
    return None

__all__ = [
    'PortAccessPoint',
    'PortAccessRegion',
    'PortAccessPlan',
    'PortAccessLegality',
    'port_side',
    'port_optical_role',
    'hop_crossing_class',
    'build_port_access_plan',
    'port_access_segments',
    'reserved_segments_for_net',
    'port_access_conflict',
    'port_access_conflict_detail',
    '_port_escape_point',
    '_port_route_point',
    '_port_junction_orientation',
    '_all_port_escape_segments',
    '_port_stub_point',
    '_port_stub_segment',
    '_all_port_stub_segments',
    '_mrr_internal_points',
]

def _port_escape_point(
    cell: MRRCell,
    port: str,
    rules: RoutingRules,
    port_stub_um: float,
) -> Point:
    _x, y = cell.port_xy(port)
    obstacle = _routing_obstacle(cell)
    inflated = _inflate_obstacle(obstacle, rules.mrr_keepout_um)
    escape = rules.port_escape_um
    if port in {"in", "drop"}:
        return inflated.left - escape, y
    if port in {"th", "add"}:
        return inflated.right + escape, y
    raise ValueError(f"unknown MRR port {port!r}")


def _port_route_point(
    cell: MRRCell,
    port: str,
    rules: RoutingRules,
    port_stub_um: float,
) -> Point:
    escape_x, escape_y = _port_escape_point(cell, port, rules, port_stub_um)
    if not rules.legalize_port_access:
        return escape_x, escape_y
    runway_um = max(
        rules.port_access_runway_um,
        2.0 * rules.bend_radius_um,
    )
    if port_side(port) == "left":
        return escape_x - runway_um, escape_y
    return escape_x + runway_um, escape_y


def _port_junction_orientation(label: str, *, source: bool) -> str:
    if "." not in label:
        return ""
    _cell_id, port = label.rsplit(".", 1)
    try:
        side = port_side(port)
    except ValueError:
        return ""
    if source:
        return "W" if side == "left" else "E"
    return "E" if side == "left" else "W"

def _all_port_escape_segments(
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
) -> list[Segment]:
    return [
        (_port_stub_point(cell, port, port_stub_um), _port_escape_point(cell, port, rules, port_stub_um))
        for cell in cells.values()
        for port in cell.ports
    ]

def _port_stub_point(cell: MRRCell, port: str, port_stub_um: float) -> Point:
    _x, y = cell.port_xy(port)
    obstacle = _routing_obstacle(cell)
    if port in {"in", "drop"}:
        return obstacle.left - port_stub_um, y
    if port in {"th", "add"}:
        return obstacle.right + port_stub_um, y
    raise ValueError(f"unknown MRR port {port!r}")

def _port_stub_segment(cell: MRRCell, port: str, port_stub_um: float) -> Segment:
    return cell.port_xy(port), _port_stub_point(cell, port, port_stub_um)

def _all_port_stub_segments(
    cells: dict[str, MRRCell],
    port_stub_um: float,
) -> list[Segment]:
    return [
        _port_stub_segment(cell, port, port_stub_um)
        for cell in cells.values()
        for port in cell.ports
    ]

def _mrr_internal_points(cell: MRRCell, in_port: str, out_port: str) -> tuple[Point, ...]:
    in_xy = cell.port_xy(in_port)
    out_xy = cell.port_xy(out_port)
    if abs(in_xy[1] - out_xy[1]) < EPS:
        return (in_xy, out_xy)
    cx = cell.center[0]
    return (in_xy, (cx, in_xy[1]), (cx, out_xy[1]), out_xy)
