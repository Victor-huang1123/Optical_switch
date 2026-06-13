from __future__ import annotations

from .models import BBox, MRRCell, PortDef
from .topology import RNBTopology


def wire_y(wire: int, n_physical: int, pitch_um: float = 36.0) -> float:
    return (n_physical - 1 - wire) * pitch_um


def build_cells(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    stage_pitch_um: float = 105.0,
    wire_pitch_um: float = 36.0,
    x0_um: float = 85.0,
    *,
    centers: dict[str, tuple[float, float]] | None = None,
) -> dict[str, MRRCell]:
    cells: dict[str, MRRCell] = {}
    for mrr_id, stage_idx, pair in topology.iter_mrrs():
        y_upper = wire_y(pair[0], topology.N_physical, wire_pitch_um)
        y_lower = wire_y(pair[1], topology.N_physical, wire_pitch_um)
        center_y = 0.5 * (y_upper + y_lower)
        # half_gap puts ports exactly on the two waveguide buses
        half_gap = 0.5 * (y_upper - y_lower)
        ports = {
            "in":   PortDef(dx=-8.0, dy=+half_gap, phi=0.0, kind="in"),
            "th":   PortDef(dx=+8.0, dy=+half_gap, phi=0.0, kind="th"),
            "add":  PortDef(dx=+8.0, dy=-half_gap, phi=0.0, kind="add"),
            "drop": PortDef(dx=-8.0, dy=-half_gap, phi=0.0, kind="drop"),
        }
        center = centers[mrr_id] if centers is not None else (
            x0_um + stage_idx * stage_pitch_um,
            center_y,
        )
        cells[mrr_id] = MRRCell(
            id=mrr_id,
            center=center,
            ports=ports,
            s_table=s_table,
            bbox=BBox(width=28.0, height=22.0),
            d_min_th=24.0,
        )
    return cells
