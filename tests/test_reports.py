from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden"


def test_default_cli_reports_match_golden(tmp_path: Path) -> None:
    outdir = tmp_path / "default_cli"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--topology",
            "all",
            # The golden files were generated with mock physics; pin it so the
            # test stays valid whether or not mrr_sparam_library/ is present.
            "--sparam-dir",
            "MOCK",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    expected_files = [
        "routing_comparison.csv",
        "routing_summary.json",
        "padded_benes_8x8_2-0-5-1-3-4_states.csv",
        "waksman_6x6_2-0-5-1-3-4_states.csv",
        "spanke_benes_6x6_2-0-5-1-3-4_states.csv",
    ]
    for filename in expected_files:
        assert (outdir / filename).read_bytes() == (GOLDEN_DIR / filename).read_bytes()
        archived_name = f"{Path(filename).stem}_v2{Path(filename).suffix}"
        assert (GOLDEN_DIR / filename).read_bytes() == (
            GOLDEN_DIR / archived_name
        ).read_bytes()

    # These legacy-only demo routes are part of the old-canvas gate. In
    # particular, Padded-Benes I4->O3 corridor-squeezes under v3 dy/width.
    for topology in ("padded_benes_8x8", "waksman_6x6", "spanke_benes_6x6"):
        assert (outdir / f"{topology}_2-0-5-1-3-4.png").is_file()
