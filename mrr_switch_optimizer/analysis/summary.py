from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LayerName = Literal["logical", "surrogate", "physical"]


@dataclass(frozen=True)
class PathLossBreakdown:
    path_id: str
    layer: LayerName
    input_port: int
    output_port: int
    mrr_count_on_path: int
    mrr_loss_db: float
    waveguide_length_um: float
    propagation_loss_db: float
    bend_count: int
    bend_loss_db: float
    crossing_count: int
    crossing_loss_db: float
    total_insertion_loss_db: float
    leak_mrr_power: float
    leak_crossing_power: float
    leak_total_power: float
    signal_power: float
    sxr_db: float
    sxr_violation: bool


@dataclass(frozen=True)
class PermutationSummary:
    topology: str
    permutation: tuple[int, ...]
    layer: LayerName
    path_count: int
    active_states: int
    total_states: int
    min_path_depth: int
    max_path_depth: int
    average_path_depth: float
    total_waveguide_length_um: float
    crossing_count: int
    worst_insertion_loss_db: float
    average_insertion_loss_db: float
    worst_sxr_db: float
    sxr_violation_count: int
    path_breakdowns: tuple[PathLossBreakdown, ...]


@dataclass(frozen=True)
class TopologySummary:
    topology: str
    n_logical: int
    n_physical: int
    n_mrr: int
    n_stages: int
    layer: LayerName
    permutation_count: int
    min_path_depth: int
    max_path_depth: int
    average_path_depth: float
    worst_insertion_loss_db: float
    average_insertion_loss_db: float
    worst_sxr_db: float
    sxr_violation_count: int
