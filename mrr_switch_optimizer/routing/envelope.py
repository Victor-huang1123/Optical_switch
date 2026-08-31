from __future__ import annotations

from dataclasses import dataclass
from math import log2
from typing import Mapping

from ..core.models import DEFAULT_CELL_GEOMETRY, CellGeometry, MRRCell
from ..core.topology import RNBTopology
from ..placement.layout import build_cells


DEFAULT_STAGE_PITCH_BY_OCTAVE = {4: 136.0, 8: 168.0, 16: 232.0}


@dataclass(frozen=True)
class OctaveEnvelope:
    """Pinned physical canvas shared by both fabrics in one radix octave."""

    n_canvas: int
    stage_pitch_um: float
    wire_pitch_um: float = 64.0
    grid_pitch_um: float = 8.0
    grid_margin_tracks: int = 20
    x0_um: float = 85.0
    x_start_um: float = 20.0
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY

    @property
    def n_stages(self) -> int:
        return 2 * int(log2(self.n_canvas)) - 1

    @property
    def x_end_um(self) -> float:
        return self.x0_um + (self.n_stages - 1) * self.stage_pitch_um + 70.0

    @property
    def y_bottom_um(self) -> float:
        return -self.grid_margin_tracks * self.grid_pitch_um

    @property
    def y_top_um(self) -> float:
        return (
            (self.n_canvas - 1) * self.wire_pitch_um
            + self.grid_margin_tracks * self.grid_pitch_um
        )

    @property
    def envelope_id(self) -> str:
        port_dy = f"{self.cell_geometry.port_dy_um:g}".replace(".", "p")
        width_nm = round(self.cell_geometry.waveguide_width_um * 1000.0)
        return (
            f"octave-p{self.n_canvas}"
            f"-bbox{self.cell_geometry.bbox_width_um:g}x"
            f"{self.cell_geometry.bbox_height_um:g}"
            f"-dy{port_dy}-w{width_nm}"
            f"-sp{int(self.stage_pitch_um)}"
            f"-wp{int(self.wire_pitch_um)}-gp{int(self.grid_pitch_um)}"
            f"-m{self.grid_margin_tracks}"
        )


def octave_physical_n(n_logical: int) -> int:
    if 3 <= n_logical <= 4:
        return 4
    if 5 <= n_logical <= 8:
        return 8
    if 9 <= n_logical <= 16:
        return 16
    raise ValueError(f"N={n_logical} is outside the supported octave campaign")


def octave_envelope(
    n_logical: int,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
    stage_pitch_by_octave: Mapping[int, float] = DEFAULT_STAGE_PITCH_BY_OCTAVE,
) -> OctaveEnvelope:
    n_canvas = octave_physical_n(n_logical)
    return OctaveEnvelope(
        n_canvas=n_canvas,
        stage_pitch_um=stage_pitch_by_octave[n_canvas],
        cell_geometry=cell_geometry,
    )


def build_envelope_cells(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    envelope: OctaveEnvelope,
) -> dict[str, MRRCell]:
    expected_stages = 2 * (topology.N_logical - 1).bit_length() - 1
    if topology.n_stages != expected_stages or expected_stages != envelope.n_stages:
        raise ValueError(
            f"{topology.name} has {topology.n_stages} stages but "
            f"{envelope.envelope_id} requires {envelope.n_stages}"
        )
    return build_cells(
        topology,
        s_table,
        stage_pitch_um=envelope.stage_pitch_um,
        wire_pitch_um=envelope.wire_pitch_um,
        x0_um=envelope.x0_um,
        cell_geometry=envelope.cell_geometry,
    )


__all__ = [
    "DEFAULT_STAGE_PITCH_BY_OCTAVE",
    "OctaveEnvelope",
    "build_envelope_cells",
    "octave_envelope",
    "octave_physical_n",
]
