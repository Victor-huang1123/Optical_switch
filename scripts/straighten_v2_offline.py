from __future__ import annotations

import argparse
import json
from pathlib import Path
import pickle
import time

from mrr_switch_optimizer.analysis.fabric_loss import _evaluate_path_loss
from mrr_switch_optimizer.analysis.nsweep import (
    _loss_setup,
    _witness_for_path,
    enumerate_fabric_paths,
)
from mrr_switch_optimizer.core.models import MRRCell
from mrr_switch_optimizer.core.state_assignment import (
    BenesLoopingStrategy,
    WaksmanStrategy,
)
from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    RNBTopology,
    WaksmanTopology,
)
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.fabric import (
    FixedFabricRoutingResult,
    _fabric_waveguide_paths,
)
from mrr_switch_optimizer.routing.port_access import build_port_access_plan
from mrr_switch_optimizer.routing.straighten import (
    physical_routes_from_fixed,
    straighten_fixed_fabric,
)


DEFAULT_INPUT_ROOT = Path("outputs/nsweep_fixed_fabric_v2")
DEFAULT_OUTPUT = Path("outputs/v3_offline_analysis/straighten_report.md")


def _topology(kind: str, n: int) -> RNBTopology:
    if kind == "waksman":
        return WaksmanTopology(n, strategy=WaksmanStrategy(), verify_rnb=False)
    if kind == "padded_benes":
        return PaddedBenesTopology(
            n,
            strategy=BenesLoopingStrategy(),
            verify_rnb=False,
        )
    raise ValueError(f"unknown topology {kind!r}")


def _completed_cases(root: Path) -> list[tuple[Path, dict[str, object]]]:
    cases: list[tuple[Path, dict[str, object]]] = []
    for metrics_path in root.glob("cases/*/n*/[BC]_*/case_metrics.json"):
        metrics = json.loads(metrics_path.read_text())
        if metrics.get("status") == "complete":
            cases.append((metrics_path.parent, metrics))
    return sorted(
        cases,
        key=lambda item: (
            str(item[1]["topology"]),
            int(item[1]["N"]),
            str(item[1]["config"]),
        ),
    )


def _format_float(value: float, digits: int = 6) -> str:
    if abs(value) < 0.5 * 10 ** (-digits):
        value = 0.0
    return f"{value:.{digits}f}"


def _reevaluate_wil_with_certificate(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    old_result: FixedFabricRoutingResult,
    new_result: FixedFabricRoutingResult,
    certificate_path: Path,
) -> float:
    """Re-rank modified losses while reusing topology-only v2 path proofs.

    Path realizability is independent of physical geometry.  The v2 certificate
    therefore supplies exact proofs/witnesses for its ranked prefix, including
    the expensive n11 impossibility proofs.  Any newly promoted path is checked
    by the same witness routine used by the campaign.
    """
    certificate = json.loads(certificate_path.read_text())
    paths = enumerate_fabric_paths(topology, old_result.graph)
    old_setup = _loss_setup(old_result)
    old_losses = {
        path: _evaluate_path_loss(path, cells, old_result, *old_setup)
        for path in paths
    }
    old_ranked = sorted(
        paths,
        key=lambda path: (
            -old_losses[path].insertion_loss_db,
            path.input_port,
            path.output_port,
            tuple((step.mrr_id, step.state) for step in path.steps),
        ),
    )
    known: dict[object, tuple[bool, tuple[int, ...] | None]] = {}
    for expected, path in zip(certificate["path_rank_table"], old_ranked):
        if (
            int(expected["rank"]) != len(known) + 1
            or int(expected["input_port"]) != path.input_port
            or int(expected["output_port"]) != path.output_port
            or abs(
                float(expected["insertion_loss_db"])
                - old_losses[path].insertion_loss_db
            )
            > 1e-9
        ):
            raise RuntimeError("stored v2 path-rank certificate does not match its pickle")
        witness = expected["witness_permutation"]
        known[path] = (
            bool(expected["realizable"]),
            None if witness is None else tuple(int(value) for value in witness),
        )

    new_setup = _loss_setup(new_result)
    new_losses = {
        path: _evaluate_path_loss(path, cells, new_result, *new_setup)
        for path in paths
    }
    new_ranked = sorted(
        paths,
        key=lambda path: (
            -new_losses[path].insertion_loss_db,
            path.input_port,
            path.output_port,
            tuple((step.mrr_id, step.state) for step in path.steps),
        ),
    )
    for path in new_ranked:
        if path in known:
            realizable, _witness = known[path]
        else:
            witness, _proof = _witness_for_path(topology, path, random_tries=4096)
            realizable = witness is not None
        if realizable:
            return new_losses[path].insertion_loss_db
    raise RuntimeError(f"{topology.name} has no strategy-realizable logical path")


def run(input_root: Path, output: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case_dir, metrics in _completed_cases(input_root):
        topology_kind = str(metrics["topology"])
        n = int(metrics["N"])
        config_name = str(metrics["config"])
        config = json.loads((case_dir / "config.json").read_text())
        with (case_dir / "routing_result.pkl").open("rb") as handle:
            cells, result = pickle.load(handle)

        baseline_drc = validate_physical_routes(
            physical_routes_from_fixed(result), cells, result.rules
        )
        if baseline_drc:
            first = baseline_drc[0]
            raise RuntimeError(
                "v2 baseline legality regression: "
                f"{topology_kind} n{n} {config_name}: "
                f"{first.rule}: {first.net_id}: {first.message}"
            )

        topology = _topology(topology_kind, n)
        port_access_plan = build_port_access_plan(
            _fabric_waveguide_paths(topology, result.graph),
            cells,
            result.rules,
            port_stub_um=2.0,
        )
        template = config.get("layout_mode") == "template"
        started = time.perf_counter()
        straightened = straighten_fixed_fabric(
            result,
            cells,
            port_access_plan=port_access_plan,
            max_rounds=10,
            preserve_template=template,
        )
        runtime_s = time.perf_counter() - started
        stats = straightened.stats

        if template and (
            stats.rewrites
            or stats.bends_removed
            or abs(stats.length_saved_um) > 1e-9
            or stats.crossings_delta
        ):
            raise RuntimeError(
                "template zero-change gate failed: "
                f"{topology_kind} n{n} {config_name}: {stats}"
            )
        final_drc = validate_physical_routes(
            physical_routes_from_fixed(straightened.routing),
            cells,
            straightened.routing.rules,
        )
        if final_drc:
            first = final_drc[0]
            raise RuntimeError(
                "straightening legality regression: "
                f"{topology_kind} n{n} {config_name}: "
                f"{first.rule}: {first.net_id}: {first.message}"
            )

        old_wil = float(metrics["worst_il_db"])
        if stats.rewrites:
            new_wil = _reevaluate_wil_with_certificate(
                topology,
                cells,
                result,
                straightened.routing,
                case_dir / "worst_il_certificate.json",
            )
        else:
            new_wil = old_wil
        rows.append(
            {
                "topology": topology_kind,
                "N": n,
                "config": config_name,
                "layout_mode": config["layout_mode"],
                "rewrites": stats.rewrites,
                "bends_removed": stats.bends_removed,
                "length_saved_um": stats.length_saved_um,
                "crossings_delta": stats.crossings_delta,
                "loss_proxy_delta_db": stats.loss_proxy_delta_db,
                "old_wil_db": old_wil,
                "new_wil_db": new_wil,
                "wil_delta_db": new_wil - old_wil,
                "runtime_s": runtime_s,
                "rounds": stats.rounds,
                "candidate_windows": stats.candidate_windows,
                "legal_candidates": stats.legal_candidates,
            }
        )
        print(
            f"{topology_kind} n{n:02d} {config_name}: "
            f"rewrites={stats.rewrites} bends={stats.bends_removed:+d} "
            f"length={stats.length_saved_um:+.3f} um "
            f"crossings={stats.crossings_delta:+d} "
            f"WIL={new_wil - old_wil:+.6f} dB"
        )

    template_rows = [row for row in rows if row["layout_mode"] == "template"]
    astar_rows = [row for row in rows if row["layout_mode"] == "astar"]
    changed_astar = [row for row in astar_rows if int(row["rewrites"]) > 0]
    lines = [
        "# v2 Offline Jog-Straightening Report",
        "",
        "This analysis loaded the completed v2 `routing_result.pkl` artifacts read-only; "
        "it did not invoke the router or write into the v2 campaign tree. Candidate spans "
        "were confined to external segments, protected every edge endpoint and local "
        "runway/stub, checked both L variants against rebuilt blockers and port-access "
        "regions, reran full DRC, and required non-increasing loss under each case's "
        "stored coefficients.",
        "Modified WIL was re-ranked over the full directed path space; topology-only "
        "realizability proofs/witnesses from each immutable v2 certificate were reused, "
        "and any newly promoted path was checked by the campaign witness routine.",
        "",
        "## Sanity gates",
        "",
        f"- Completed cases analyzed: {len(rows)}.",
        f"- Template cases: {len(template_rows)}; all were structurally held at zero change.",
        f"- A* cases changed: {len(changed_astar)}/{len(astar_rows)}.",
        "- Final legality regressions: 0.",
        "",
        "## Per-case results",
        "",
        "| topology | N | config | mode | rewrites | bends removed | length saved (um) | crossings delta | WIL delta (dB) | runtime (s) |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {topology} | {N} | {config} | {layout_mode} | {rewrites} | "
            "{bends_removed} | {length_saved} | {crossings_delta:+d} | "
            "{wil_delta} | {runtime} |".format(
                **row,
                length_saved=_format_float(float(row["length_saved_um"]), 3),
                wil_delta=_format_float(float(row["wil_delta_db"]), 6),
                runtime=_format_float(float(row["runtime_s"]), 3),
            )
        )
    lines.extend(
        [
            "",
            "## Aggregate A* deltas",
            "",
            f"- Rewrites: {sum(int(row['rewrites']) for row in astar_rows)}.",
            f"- Bends removed: {sum(int(row['bends_removed']) for row in astar_rows)}.",
            "- Length saved: "
            f"{sum(float(row['length_saved_um']) for row in astar_rows):.3f} um.",
            "- Crossing delta: "
            f"{sum(int(row['crossings_delta']) for row in astar_rows):+d}.",
            "- Sum of per-case WIL deltas: "
            f"{sum(float(row['wil_delta_db']) for row in astar_rows):+.6f} dB.",
            "",
            "A negative WIL delta is an improvement. The stored modified geometries are "
            "ephemeral analysis results only; no v2 pickle or summary was rewritten.",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run(args.input_root, args.output)


if __name__ == "__main__":
    main()
