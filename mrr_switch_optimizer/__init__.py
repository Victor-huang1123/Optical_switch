"""Add-drop MRR switch optimizer."""

from .analysis.activity import scan_mrr_activity, summarize_mrr_activity
from .analysis.cost import aggregate_cost, evaluate_routing, make_permutation_split
from .analysis.surrogate import analytic_edge_costs, analytic_layout_cost
from .core.sparams import load_mrr_s_table
from .core.topology import (
    PaddedBenesTopology,
    SpankeBenesTopology,
    WaksmanTopology,
    build_waksman_stage_pairs,
)
from .placement.sa import is_feasible_placement, sa_placement
from .routing import (
    CrossingCandidate,
    CrossingRule,
    DRCViolation,
    GridNodeOccupancy,
    HistoryCost,
    NeighborMove,
    PhysicalRoute,
    PhysicalRoutingResult,
    RouteGrid,
    RoutingError,
    RoutingRules,
    RoutingWindow,
    RouterState,
    legal_crossing_candidate,
    route_physical_design,
    route_physical_paths,
)

__all__ = [
    "CrossingCandidate",
    "CrossingRule",
    "DRCViolation",
    "GridNodeOccupancy",
    "HistoryCost",
    "NeighborMove",
    "PaddedBenesTopology",
    "PhysicalRoute",
    "PhysicalRoutingResult",
    "RouteGrid",
    "RoutingError",
    "RoutingRules",
    "RoutingWindow",
    "RouterState",
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
    "legal_crossing_candidate",
    "route_physical_design",
    "route_physical_paths",
    "sa_placement",
    "scan_mrr_activity",
    "summarize_mrr_activity",
]
