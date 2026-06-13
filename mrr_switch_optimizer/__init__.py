"""Add-drop MRR routing demo utilities."""

from .activity import scan_mrr_activity, summarize_mrr_activity
from .cost import aggregate_cost, evaluate_routing, make_permutation_split
from .sa_placement import is_feasible_placement, sa_placement
from .sparams import load_mrr_s_table
from .surrogate import analytic_edge_costs, analytic_layout_cost
from .topology import (
    PaddedBenesTopology,
    SpankeBenesTopology,
    WaksmanTopology,
    build_waksman_stage_pairs,
)

__all__ = [
    "PaddedBenesTopology",
    "SpankeBenesTopology",
    "WaksmanTopology",
    "aggregate_cost",
    "analytic_edge_costs",
    "analytic_layout_cost",
    "build_waksman_stage_pairs",
    "evaluate_routing",
    "is_feasible_placement",
    "load_mrr_s_table",
    "make_permutation_split",
    "scan_mrr_activity",
    "sa_placement",
    "summarize_mrr_activity",
]
