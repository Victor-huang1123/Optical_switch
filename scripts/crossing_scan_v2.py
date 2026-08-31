from __future__ import annotations

import argparse
import csv
import json
from math import hypot, inf
from pathlib import Path
import pickle

from mrr_switch_optimizer.routing.crossing import merged_route_arm_clearances
from mrr_switch_optimizer.routing.straighten import physical_routes_from_fixed
from mrr_switch_optimizer.routing.types import EPS, PhysicalRoute, Point


DEFAULT_INPUT_ROOT = Path("outputs/nsweep_fixed_fabric_v2")
DEFAULT_OUTPUT = Path(
    "outputs/v3_offline_analysis/crossing_manufacturability.csv"
)
THRESHOLDS_UM = (5.0, 10.0, 15.0)


def _completed_case_dirs(root: Path) -> list[Path]:
    case_dirs: list[Path] = []
    for metrics_path in root.glob("cases/*/n*/[BC]_*/case_metrics.json"):
        if json.loads(metrics_path.read_text()).get("status") == "complete":
            case_dirs.append(metrics_path.parent)
    return sorted(case_dirs)


def _arm_clearances(
    route: PhysicalRoute,
    location: Point,
) -> tuple[float, float]:
    first, second = merged_route_arm_clearances(route, location)
    if first < EPS or second < EPS:
        raise RuntimeError(
            f"crossing {location} is not in the strict interior of the merged "
            f"segment on input {route.input_port}"
        )
    return first, second


def run(input_root: Path, output: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case_dir in _completed_case_dirs(input_root):
        metrics = json.loads((case_dir / "case_metrics.json").read_text())
        config = json.loads((case_dir / "config.json").read_text())
        with (case_dir / "routing_result.pkl").open("rb") as handle:
            _cells, result = pickle.load(handle)
        routes = physical_routes_from_fixed(result)
        route_by_owner = {
            route.edge_id: physical
            for route, physical in zip(result.routes, routes)
        }
        locations = tuple(crossing.location for crossing in result.crossings)
        for crossing_index, crossing in enumerate(result.crossings):
            nearest = min(
                (
                    hypot(
                        crossing.location[0] - other[0],
                        crossing.location[1] - other[1],
                    )
                    for other_index, other in enumerate(locations)
                    if other_index != crossing_index
                ),
                default=inf,
            )
            a_before, a_after = _arm_clearances(
                route_by_owner[crossing.edge_a], crossing.location
            )
            b_before, b_after = _arm_clearances(
                route_by_owner[crossing.edge_b], crossing.location
            )
            min_arm = min(a_before, a_after, b_before, b_after)
            row: dict[str, object] = {
                "record_type": "crossing",
                "topology": metrics["topology"],
                "N": metrics["N"],
                "config": metrics["config"],
                "layout_mode": config["layout_mode"],
                "crossing_index": crossing_index,
                "edge_a": crossing.edge_a,
                "edge_b": crossing.edge_b,
                "x_um": crossing.location[0],
                "y_um": crossing.location[1],
                "nearest_crossing_distance_um": nearest,
                "edge_a_arm_before_um": a_before,
                "edge_a_arm_after_um": a_after,
                "edge_b_arm_before_um": b_before,
                "edge_b_arm_after_um": b_after,
                "min_arm_clearance_um": min_arm,
            }
            for threshold in THRESHOLDS_UM:
                suffix = str(int(threshold))
                row[f"arm_violation_{suffix}um"] = int(min_arm < threshold - EPS)
                row[f"crossing_spacing_violation_{suffix}um"] = int(
                    nearest < threshold - EPS
                )
                row[f"any_violation_{suffix}um"] = int(
                    min(min_arm, nearest) < threshold - EPS
                )
            rows.append(row)

    fields = [
        "record_type",
        "topology",
        "N",
        "config",
        "layout_mode",
        "crossing_index",
        "edge_a",
        "edge_b",
        "x_um",
        "y_um",
        "nearest_crossing_distance_um",
        "edge_a_arm_before_um",
        "edge_a_arm_after_um",
        "edge_b_arm_before_um",
        "edge_b_arm_after_um",
        "min_arm_clearance_um",
    ]
    for threshold in THRESHOLDS_UM:
        suffix = str(int(threshold))
        fields.extend(
            (
                f"arm_violation_{suffix}um",
                f"crossing_spacing_violation_{suffix}um",
                f"any_violation_{suffix}um",
            )
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"crossings={len(rows)}")
    for mode in ("all", "astar", "template"):
        selected = rows if mode == "all" else [
            row for row in rows if row["layout_mode"] == mode
        ]
        for threshold in THRESHOLDS_UM:
            suffix = str(int(threshold))
            arm = sum(int(row[f"arm_violation_{suffix}um"]) for row in selected)
            spacing = sum(
                int(row[f"crossing_spacing_violation_{suffix}um"])
                for row in selected
            )
            either = sum(int(row[f"any_violation_{suffix}um"]) for row in selected)
            print(
                f"{mode} threshold={threshold:g}um crossings={len(selected)} "
                f"arm={arm} spacing={spacing} either={either}"
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run(args.input_root, args.output)


if __name__ == "__main__":
    main()
