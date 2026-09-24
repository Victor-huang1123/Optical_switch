from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_paper_run_writes_provenance_and_reproducible_csvs(tmp_path: Path) -> None:
    config_path = tmp_path / "paper_smoke.yaml"
    out_root = tmp_path / "outputs"
    config = {
        "seed": 42,
        "outdir": str(out_root),
        "run_id": "paper_smoke_seed42",
        "logical_matrix": {
            "topology": "all",
            "n_values": [4],
            "train_samples": 2,
            "eval_samples": 2,
        },
    }
    config_path.write_text(yaml.safe_dump(config))

    run_cmd = [sys.executable, "main.py", "--paper-run", str(config_path)]
    subprocess.run(
        run_cmd,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    run_dir = out_root / "paper_smoke_seed42"
    summary_path = run_dir / "logical_matrix" / "logical_matrix_summary.csv"
    first_summary = summary_path.read_bytes()
    first_waksman = (
        run_dir
        / "logical_matrix"
        / "waksman_4x4_n4"
        / "eval_path_distribution.csv"
    ).read_bytes()

    subprocess.run(
        run_cmd,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert summary_path.read_bytes() == first_summary
    assert (
        run_dir
        / "logical_matrix"
        / "waksman_4x4_n4"
        / "eval_path_distribution.csv"
    ).read_bytes() == first_waksman

    with (run_dir / "run_config.json").open() as f:
        run_config = json.load(f)
    assert run_config["resolved"]["perm_seed"] == 42
    assert run_config["versions"]["git_sha"]
    assert run_config["permutation_lists"][0]["train_sha256"]
    assert run_config["permutation_lists"][0]["eval_sha256"]
