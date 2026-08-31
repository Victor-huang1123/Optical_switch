from __future__ import annotations

import argparse
import math
import warnings
from pathlib import Path

import pytest

from mrr_switch_optimizer.app.cli import (
    _matrix_protocol,
    _percentile,
    _select_topologies,
)
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE, load_mrr_s_table


def _protocol_args(train: int | None, eval_samples: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        matrix_train_samples=train, matrix_eval_samples=eval_samples
    )


def test_matrix_protocol_default_n6_unchanged() -> None:
    assert _matrix_protocol(6, _protocol_args(None, None)) == ("exhaustive", 500, 220)


@pytest.mark.parametrize("n_logical", [3, 4, 5, 6])
def test_matrix_protocol_zero_train_is_truly_exhaustive(n_logical: int) -> None:
    mode, n_train, n_eval = _matrix_protocol(n_logical, _protocol_args(0, None))
    assert mode == "exhaustive"
    assert n_train == 0
    assert n_eval == math.factorial(n_logical)


def test_matrix_protocol_eval_default_tracks_train_override() -> None:
    _mode, n_train, n_eval = _matrix_protocol(5, _protocol_args(20, None))
    assert (n_train, n_eval) == (20, math.factorial(5) - 20)


def test_percentile_linear_interpolation() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert _percentile(values, 0.5) == pytest.approx(2.5)
    assert _percentile(values, 0.9) == pytest.approx(3.7)
    assert _percentile(values, 0.0) == 1.0
    assert _percentile(values, 1.0) == 4.0
    assert _percentile([7.0], 0.99) == 7.0


def test_select_topologies_rejects_unknown_selector() -> None:
    with pytest.raises(ValueError, match="unknown topology selector"):
        _select_topologies("spanke", 6)


def test_select_topologies_main_and_all() -> None:
    assert [t.name for t in _select_topologies("main", 6)] == [
        "padded_benes_8x8",
        "waksman_6x6",
    ]
    assert [t.name for t in _select_topologies("all", 6)] == [
        "padded_benes_8x8",
        "waksman_6x6",
        "spanke_benes_6x6",
    ]


def test_select_topologies_paper_includes_both_sb_arrangements() -> None:
    assert [t.name for t in _select_topologies("paper", 8)] == [
        "padded_benes_8x8",
        "waksman_8x8",
        "spanke_benes_8x8",
        "spanke_benes_rect_8x8",
    ]


def test_sparam_loader_warns_on_missing_library(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="falling back to MOCK_S_TABLE"):
        table = load_mrr_s_table(tmp_path / "does_not_exist")
    assert table == dict(MOCK_S_TABLE)


def test_sparam_loader_strict_rejects_missing_library(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="S-parameter files"):
        load_mrr_s_table(tmp_path / "does_not_exist", strict=True)


def test_sparam_loader_real_library_no_warning() -> None:
    library = Path(__file__).resolve().parents[1] / "mrr_sparam_library"
    if not (library / "mrr_r5.00_ch1550.0_on.csv").exists():
        pytest.skip("mrr_sparam_library not present")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        table = load_mrr_s_table(library, radius_um=5.0, channel_nm=1550.0)
    assert table != dict(MOCK_S_TABLE)
    assert set(MOCK_S_TABLE) <= set(table)
