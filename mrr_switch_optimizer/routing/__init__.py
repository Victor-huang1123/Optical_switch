"""Physical routing implementation package."""

from .crossing import CrossingCandidate, CrossingRule, legal_crossing_candidate
from .drc import validate_physical_routes
from .grid_router import HistoryCost, NeighborMove, RouterState
from .physical import route_physical_design, route_physical_paths
from .route_grid import GridNodeOccupancy, RouteGrid
from .types import (
    DRCViolation,
    FailedNet,
    PhysicalRoute,
    PhysicalRoutingResult,
    RouteCrossing,
    RoutedSegment,
    RoutingError,
    RoutingRules,
    RoutingWindow,
)

__all__ = [
    "CrossingCandidate",
    "CrossingRule",
    "DRCViolation",
    "FailedNet",
    "GridNodeOccupancy",
    "HistoryCost",
    "NeighborMove",
    "PhysicalRoute",
    "PhysicalRoutingResult",
    "RouteCrossing",
    "RouteGrid",
    "RoutedSegment",
    "RoutingError",
    "RoutingRules",
    "RoutingWindow",
    "RouterState",
    "legal_crossing_candidate",
    "route_physical_design",
    "route_physical_paths",
    "validate_physical_routes",
]
