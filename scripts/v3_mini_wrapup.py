from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import pickle
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = ROOT / "outputs" / "nsweep_fixed_fabric_v2"
V3_ROOT = ROOT / "outputs" / "nsweep_fixed_fabric_v3_mini"
TARGET_NS = (4, 6, 8)
TOPOLOGY_ORDER = {"waksman": 0, "padded_benes": 1}
CONFIG_ORDER = {"B_db_placeholder": 0, "C_db_realistic": 1}
EXPECTED_TEMPLATE_HASHES = {
    4: "85ff74a15c329e751866a9407a351a1b845299cc83f4d6ed7d917a56cdf5fc93",
    6: "c5ad40e124038391c6f1e9370af9c85b3e232445defec170e634db1b5093547a",
    8: "c5ad40e124038391c6f1e9370af9c85b3e232445defec170e634db1b5093547a",
}
FAILURE_DIAGNOSIS = (
    "V3-GEOMETRY REGRESSION: routable in v2 (WIL 3.798421 dB), but v3 "
    "I6->O6 is blocked on its final hop by the wider foreign I3 port-access "
    "region (port_access_overlap_foreign; frontier exhausted at 10,220 pops, "
    "not budget-limited)."
)


def _key(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["topology"]), int(row["N"]), str(row["config"])


def _sort_key(key: tuple[str, int, str]) -> tuple[int, int, int]:
    topology, n, config = key
    return n, TOPOLOGY_ORDER[topology], CONFIG_ORDER[config]


def _load_cases(root: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    cases: dict[tuple[str, int, str], dict[str, Any]] = {}
    for n in TARGET_NS:
        for metrics_path in root.glob(f"cases/*/n{n:02d}/*/case_metrics.json"):
            row = json.loads(metrics_path.read_text())
            pickle_path = metrics_path.parent / "routing_result.pkl"
            with pickle_path.open("rb") as handle:
                _cells, result = pickle.load(handle)
            row["total_bends_derived"] = sum(
                route.bend_count for route in result.routes
            )
            row["total_crossings_derived"] = len(result.crossings)
            row["case_dir"] = metrics_path.parent
            cases[_key(row)] = row
    return cases


def _expected_keys() -> set[tuple[str, int, str]]:
    return {
        (topology, n, config)
        for n in TARGET_NS
        for topology in TOPOLOGY_ORDER
        for config in CONFIG_ORDER
    }


def _validate(
    v2: dict[tuple[str, int, str], dict[str, Any]],
    v3: dict[tuple[str, int, str], dict[str, Any]],
) -> list[Path]:
    expected = _expected_keys()
    if set(v2) != expected or set(v3) != expected:
        raise RuntimeError(
            "12-case inventory failure: "
            f"v2_missing={sorted(expected - set(v2))!r}, "
            f"v3_missing={sorted(expected - set(v3))!r}"
        )
    failures = [key for key, row in v3.items() if row["status"] != "complete"]
    expected_failure = ("waksman", 8, "C_db_realistic")
    if failures != [expected_failure]:
        raise RuntimeError(f"unexpected v3 failure inventory: {failures!r}")
    failed_row = v3[expected_failure]
    if failed_row.get("failure_class") != "port_access_overlap_foreign":
        raise RuntimeError(f"missing foreign-port failure class: {failed_row!r}")

    for key, row in v3.items():
        topology, n, _config = key
        if topology == "padded_benes":
            required = (
                row["status"] == "complete"
                and row.get("acceptance_tier") == "template_hard_gate"
                and row.get("crossing_clearance_acceptance") == "hard_gate"
                and row.get("manufacturability_status") == "all_clear"
                and row.get("geometry_sha256") == EXPECTED_TEMPLATE_HASHES[n]
                and int(row.get("astar_calls", -1)) == 0
                and all(
                    int(row.get(field, -1)) == 0
                    for field in (
                        "legacy_drc",
                        "same_net_min_spacing",
                        "perpendicular_clearance",
                        "crossing_clearance",
                        "bend_radius_legality",
                    )
                )
            )
            if not required:
                raise RuntimeError(f"template-tier gate failure: {key!r} {row!r}")
        if row["status"] == "complete" and (
            row.get("method_agreement") is not True
            or int(row.get("coverage_failures", -1)) != 0
        ):
            raise RuntimeError(f"dual-method/coverage failure: {key!r} {row!r}")

    pngs = [
        Path(row["case_dir"]) / "fixed_fabric_layout.png"
        for _key_value, row in sorted(v3.items(), key=lambda item: _sort_key(item[0]))
    ]
    missing_pngs = [path for path in pngs if not path.is_file()]
    if len(pngs) != 12 or missing_pngs:
        raise RuntimeError(
            f"layout PNG inventory failure: count={len(pngs)}, missing={missing_pngs!r}"
        )
    return pngs


def _crossing_status(row: dict[str, Any], *, v3: bool) -> str:
    if not v3:
        return "disabled (legacy tier)"
    count = int(row["crossing_clearance"])
    if row["acceptance_tier"] == "template_hard_gate":
        return f"hard-gate all-clear ({count})"
    return f"audit-only ({count})"


def _diagnosis(
    key: tuple[str, int, str],
    v2_row: dict[str, Any],
    v3_row: dict[str, Any],
) -> str:
    topology, _n, config = key
    if key == ("waksman", 8, "C_db_realistic"):
        return FAILURE_DIAGNOSIS
    delta = float(v3_row["worst_il_db"]) - float(v2_row["worst_il_db"])
    if topology == "padded_benes":
        return (
            f"Template hard-gate all-clear; WIL {'cost' if delta > 0 else 'gain'} "
            f"{abs(delta):.6f} dB under v3 internal propagation geometry."
        )
    if config == "C_db_realistic":
        return (
            f"A*-C WIL gain {abs(delta):.6f} dB with "
            f"{int(v2_row['total_bends_derived']) - int(v3_row['total_bends_derived'])} "
            "fewer bends; clearance counts are measurement-only."
        )
    direction = "cost" if delta > 0 else "gain"
    return (
        f"A*-B WIL {direction} {abs(delta):.6f} dB; clearance counts are "
        "measurement-only."
    )


def _comparison_rows(
    v2: dict[tuple[str, int, str], dict[str, Any]],
    v3: dict[tuple[str, int, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(v2, key=_sort_key):
        topology, n, config = key
        old = v2[key]
        new = v3[key]
        v3_wil = new.get("worst_il_db")
        wil_delta = (
            None
            if v3_wil is None
            else float(v3_wil) - float(old["worst_il_db"])
        )
        rows.append(
            {
                "topology": topology,
                "N": n,
                "config": config,
                "v2_status": old["status"],
                "v3_status": new["status"],
                "v2_wil_db": old["worst_il_db"],
                "v3_wil_db": v3_wil,
                "wil_delta_v3_minus_v2_db": wil_delta,
                "v2_total_bends": old["total_bends_derived"],
                "v3_total_bends": new["total_bends_derived"],
                "v3_geometry_scope": (
                    "full" if new["status"] == "complete" else "partial_failed_route"
                ),
                "v2_total_crossings": old["total_crossings_derived"],
                "v3_total_crossings": new["total_crossings_derived"],
                "v2_crossing_clearance_status": _crossing_status(old, v3=False),
                "v3_acceptance_tier": new["acceptance_tier"],
                "v3_crossing_clearance_status": _crossing_status(new, v3=True),
                "v2_same_net_min_spacing": old["same_net_min_spacing"],
                "v2_perpendicular_clearance": old["perpendicular_clearance"],
                "v3_same_net_min_spacing": new["same_net_min_spacing"],
                "v3_perpendicular_clearance": new["perpendicular_clearance"],
                "v3_failure_class": new.get("failure_class"),
                "diagnosis": _diagnosis(key, old, new),
            }
        )
    return rows


def _write_csv(rows: list[dict[str, Any]]) -> Path:
    target = V3_ROOT / "V2_V3_COMPARISON.csv"
    with target.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(
            {
                field: "" if value is None else value
                for field, value in row.items()
            }
            for row in rows
        )
    return target


def _fmt_wil(value: Any) -> str:
    return "—" if value is None else f"{float(value):.6f}"


def _write_markdown(rows: list[dict[str, Any]]) -> Path:
    lines = [
        "# Phase 5 v2-vs-v3 mini comparison",
        "",
        "WIL delta is v3 minus v2, so negative is better. V2 crossing-clearance "
        "is disabled by design; v3 template counts are hard-gated and v3 A* "
        "counts are measurement-only. The failed n8/C bend and crossing totals "
        "describe the retained partial route and are not full-fabric comparisons.",
        "",
        "| Topology | N | Config | Status v2 -> v3 | WIL dB v2 -> v3 (delta) | Bends v2 -> v3 | Crossings v2 -> v3 | Crossing clearance v2 / v3 | Same/perp v2 -> v3 | Diagnosis |",
        "|---|---:|:---:|---|---:|---:|---:|---|---:|---|",
    ]
    for row in rows:
        delta = row["wil_delta_v3_minus_v2_db"]
        delta_text = "—" if delta is None else f"{float(delta):+.6f}"
        partial = " (partial)" if row["v3_geometry_scope"] != "full" else ""
        lines.append(
            "| {topology} | {N} | {config} | {v2_status} -> {v3_status} | "
            "{v2_wil} -> {v3_wil} ({delta}) | {v2_bends} -> {v3_bends}{partial} | "
            "{v2_crossings} -> {v3_crossings}{partial} | {v2_cross} / {v3_cross} | "
            "{v2_same}/{v2_perp} -> {v3_same}/{v3_perp} | {diagnosis} |".format(
                **row,
                v2_wil=_fmt_wil(row["v2_wil_db"]),
                v3_wil=_fmt_wil(row["v3_wil_db"]),
                delta=delta_text,
                v2_bends=row["v2_total_bends"],
                v3_bends=row["v3_total_bends"],
                partial=partial,
                v2_crossings=row["v2_total_crossings"],
                v3_crossings=row["v3_total_crossings"],
                v2_cross=row["v2_crossing_clearance_status"],
                v3_cross=row["v3_crossing_clearance_status"],
                v2_same=row["v2_same_net_min_spacing"],
                v2_perp=row["v2_perpendicular_clearance"],
                v3_same=row["v3_same_net_min_spacing"],
                v3_perp=row["v3_perpendicular_clearance"],
            )
        )
    lines.extend(
        [
            "",
            "Inventory: 12/12 cases accounted for; 11 complete and one ruled "
            "`route_failed`. Every completed case has exact dual-method agreement "
            "and zero coverage failures.",
            "",
        ]
    )
    target = V3_ROOT / "V2_V3_COMPARISON.md"
    target.write_text("\n".join(lines))
    return target


def _write_delta_explanation(rows: list[dict[str, Any]]) -> Path:
    target = V3_ROOT / "V2_V3_DELTA.md"
    target.write_text(
        """# Phase 5 v3 mini: one-page delta explanation

## Outcome

All 12 mini cases are accounted for under the fixed two-tier semantics: 11 completed with exact exhaustive/path-space agreement and full permutation coverage, and Waksman n8/C is the ruled `route_failed` geometry regression. No WIL is imputed for that failure. Padded-Beneš uses the deterministic hard-gated template; Waksman uses A* with crossing and perpendicular clearances recorded as measurement-only audits.

## A*-C gains and the n8/C boundary

The completed Waksman C cases improve materially. N=4 falls from 1.349836 to 0.720030 dB (−0.629806 dB), with bends/crossings falling from 67/16 to 31/4. N=6 falls from 2.277754 to 1.637784 dB (−0.639971 dB), with bends/crossings falling from 126/32 to 73/16. Those gains show that the v3 A* layouts can more than offset the added internal cross-state propagation when the route remains feasible. N=8/C sets the opposite boundary: v2 completed at 3.798421 dB, while v3 exhausts the frontier at 10,220 pops because the final I6→O6 hop intersects the wider foreign I3 port-access region. This is `port_access_overlap_foreign`, not a pop-budget failure; the retained partial geometry has no core or bend-legality finding, and no remediation was attempted after the ruling.

## B-config WIL cost

The propagation-heavy B model exposes the v3 length cost. Padded-Beneš increases by +0.030000, +0.042000, and +0.050667 dB at N=4/6/8, respectively. Waksman is layout-dependent: +0.098000 dB at N=4, +0.020292 dB at N=6, and −0.021041 dB at N=8. Thus the internal +3 µm per cross-state hop is a consistent template cost, while A* route-shape changes can either add to it or recover it.

## Template 10 µm achievement and campaign implication

All six padded-Beneš cases are full-DRC all-clear at the configured 10.0 µm crossing-arm rule (9.55 µm effective centerline threshold): zero crossing-clearance, same-net, perpendicular, core, and bend-legality findings; zero A* calls; 14/60/60 crossings for N=4/6/8; and the gated P4/P8 hashes. This confirms the fixed layout achieved the template design goal without changing the preserved 14/60/232 P4/P8/P16 crossing counts. The full v3 campaign decision therefore has one explicit open geometry risk rather than a template risk: Waksman feasibility under widened port-access regions, demonstrated by the n8/C regression.
"""
    )
    return target


def _make_wil_chart(rows: list[dict[str, Any]]) -> Path:
    import matplotlib.pyplot as plt

    labels = [
        f"{'W' if row['topology'] == 'waksman' else 'PB'}{row['N']}{row['config'][0]}"
        for row in rows
    ]
    values = [
        math.nan
        if row["wil_delta_v3_minus_v2_db"] is None
        else float(row["wil_delta_v3_minus_v2_db"])
        for row in rows
    ]
    colors = [
        "#B63E3E"
        if math.isnan(value)
        else ("#2474B5" if value <= 0.0 else "#E07A2D")
        for value in values
    ]
    figure, axis = plt.subplots(figsize=(12, 4.8))
    positions = list(range(len(rows)))
    axis.bar(positions, [0.0 if math.isnan(value) else value for value in values], color=colors)
    failed_index = next(index for index, value in enumerate(values) if math.isnan(value))
    axis.scatter([failed_index], [0.0], marker="x", s=110, linewidths=2.5, color="#B63E3E")
    axis.annotate(
        "route_failed\nforeign port access",
        (failed_index, 0.0),
        xytext=(0, 18),
        textcoords="offset points",
        ha="center",
        color="#8A2525",
        fontsize=8,
    )
    axis.axhline(0.0, color="#333333", linewidth=0.8)
    axis.set_xticks(positions, labels, rotation=35, ha="right")
    axis.set_ylabel("WIL delta, v3 - v2 (dB)")
    axis.set_title("Phase 5 mini: v2-to-v3 worst insertion-loss delta")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    target = V3_ROOT / "v2_v3_wil_delta.png"
    figure.savefig(target, dpi=180)
    plt.close(figure)
    return target


def _make_contact_sheet(
    v3: dict[tuple[str, int, str], dict[str, Any]],
) -> Path:
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    columns = (
        ("waksman", "B_db_placeholder"),
        ("waksman", "C_db_realistic"),
        ("padded_benes", "B_db_placeholder"),
        ("padded_benes", "C_db_realistic"),
    )
    figure, axes = plt.subplots(3, 4, figsize=(16, 11))
    for row_index, n in enumerate(TARGET_NS):
        for column_index, (topology, config) in enumerate(columns):
            row = v3[(topology, n, config)]
            image_path = Path(row["case_dir"]) / "fixed_fabric_layout.png"
            axis = axes[row_index][column_index]
            axis.imshow(mpimg.imread(image_path))
            short_topology = "Waksman" if topology == "waksman" else "Padded-Beneš"
            short_config = config[0]
            status = "" if row["status"] == "complete" else " — ROUTE FAILED"
            axis.set_title(f"{short_topology} n{n} {short_config}{status}", fontsize=10)
            axis.axis("off")
    figure.suptitle("Phase 5 v3 mini layouts (fixed geometry)", fontsize=15)
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    target = V3_ROOT / "v3_mini_layout_contact_sheet.png"
    figure.savefig(target, dpi=160)
    plt.close(figure)
    return target


def _write_png_manifest(case_pngs: list[Path], generated_pngs: list[Path]) -> Path:
    target = V3_ROOT / "PNG_MANIFEST.md"
    lines = ["# Phase 5 PNG manifest", "", "## Per-case layouts", ""]
    lines.extend(f"- `{path.relative_to(ROOT)}`" for path in case_pngs)
    lines.extend(["", "## Wrap-up figures", ""])
    lines.extend(f"- `{path.relative_to(ROOT)}`" for path in generated_pngs)
    lines.append("")
    target.write_text("\n".join(lines))
    return target


def main() -> None:
    v2 = _load_cases(V2_ROOT)
    v3 = _load_cases(V3_ROOT)
    case_pngs = _validate(v2, v3)
    rows = _comparison_rows(v2, v3)
    comparison_csv = _write_csv(rows)
    comparison_md = _write_markdown(rows)
    delta_md = _write_delta_explanation(rows)
    wil_chart = _make_wil_chart(rows)
    contact_sheet = _make_contact_sheet(v3)
    manifest = _write_png_manifest(case_pngs, [wil_chart, contact_sheet])
    print(
        "generated "
        + ", ".join(
            str(path.relative_to(ROOT))
            for path in (
                comparison_csv,
                comparison_md,
                delta_md,
                wil_chart,
                contact_sheet,
                manifest,
            )
        )
    )


if __name__ == "__main__":
    main()
