from __future__ import annotations

import csv
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ..analysis.fabric_coverage import PermutationCoverageReport
from ..analysis.fabric_loss import FabricWorstILReport
from ..core.fabric import FabricGraph
from ..routing.fabric import FixedFabricRoutingResult


def fixed_fabric_geometry_hash(result: FixedFabricRoutingResult) -> str:
    payload = [
        {
            "owner_edge_id": route.edge_id,
            "covered_edge_ids": route.covered_edge_ids,
            "waypoints": route.waypoints,
        }
        for route in result.routes
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def write_fixed_fabric_reports(
    outdir: Path,
    graph: FabricGraph,
    result: FixedFabricRoutingResult,
    coverage: PermutationCoverageReport,
    legacy_comparison: dict[str, object] | None = None,
    loss_report: FabricWorstILReport | None = None,
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    owner_by_edge = {
        edge_id: waveguide.owner_edge_id
        for waveguide in graph.waveguides
        for edge_id in waveguide.edge_ids
    }
    with (outdir / "fabric_edges.csv").open("w", newline="") as handle:
        fieldnames = [
            "edge_id",
            "waveguide_owner_edge_id",
            "kind",
            "source_endpoint",
            "source_wire",
            "source_blocked",
            "target_endpoint",
            "target_wire",
            "target_blocked",
            "source_stage",
            "target_stage",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for edge in graph.edges:
            writer.writerow(
                {
                    "edge_id": edge.edge_id,
                    "waveguide_owner_edge_id": owner_by_edge[edge.edge_id],
                    "kind": edge.kind,
                    "source_endpoint": edge.source.endpoint_id,
                    "source_wire": edge.source.wire,
                    "source_blocked": edge.source.blocked,
                    "target_endpoint": edge.target.endpoint_id,
                    "target_wire": edge.target.wire,
                    "target_blocked": edge.target.blocked,
                    "source_stage": edge.source_stage,
                    "target_stage": edge.target_stage,
                }
            )

    geometry_hash = fixed_fabric_geometry_hash(result)
    summary = {
        "physical_model": "fixed_fabric",
        "topology": graph.topology_name,
        "n_logical": graph.n_logical,
        "n_physical": graph.n_physical,
        "blocked_ports": graph.blocked_ports,
        "fabric_edge_count": len(graph.edges),
        "waveguide_count": len(graph.waveguides),
        "routed_edge_count": len(result.routed_edge_ids),
        "failed_edge_count": len(result.failed_edges),
        "failed_edge_ids": result.failed_edge_ids,
        "drc_violation_count": len(result.drc_violations),
        "crossing_count": len(result.crossings),
        "geometry_sha256": geometry_hash,
        "routing_stats": asdict(result.stats),
        "rules": asdict(result.rules),
    }
    if loss_report is not None:
        summary.update(
            {
                "mrr_count": loss_report.mrr_count,
                "worst_insertion_loss_db": loss_report.worst_insertion_loss_db,
                "worst_il_permutation": loss_report.worst_permutation,
                "worst_il_input_port": loss_report.worst_input_port,
                "worst_il_output_port": loss_report.worst_output_port,
            }
        )
    _write_json(outdir / "fabric_routing_summary.json", summary)

    with (outdir / "fabric_drc_violations.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["rule", "edge_ids", "message", "x_um", "y_um"],
        )
        writer.writeheader()
        for violation in result.drc_violations:
            writer.writerow(
                {
                    "rule": violation.rule,
                    "edge_ids": ";".join(violation.edge_ids),
                    "message": violation.message,
                    "x_um": "" if violation.location is None else violation.location[0],
                    "y_um": "" if violation.location is None else violation.location[1],
                }
            )

    coverage_payload = asdict(coverage)
    coverage_payload.update(
        {
            "passed": coverage.passed,
            "physical_model": "fixed_fabric",
            "geometry_sha256": geometry_hash,
            "physical_routing_permutation_count": 0,
            "physical_fabric_routed_once": True,
        }
    )
    _write_json(outdir / "permutation_coverage.json", coverage_payload)
    if legacy_comparison is not None:
        _write_json(outdir / "legacy_comparison.json", legacy_comparison)
    if loss_report is not None:
        _write_json(outdir / "fabric_loss_summary.json", asdict(loss_report))


def _write_json(path: Path, payload: object) -> None:
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


__all__ = ["fixed_fabric_geometry_hash", "write_fixed_fabric_reports"]
