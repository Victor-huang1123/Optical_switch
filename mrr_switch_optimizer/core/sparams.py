from __future__ import annotations

import csv
import warnings
from pathlib import Path

MOCK_S_TABLE: dict[tuple[str, str, int], float] = {
    ("in", "th", 0): 0.95,
    ("in", "drop", 0): 0.003,
    ("in", "th", 1): 0.05,
    ("in", "drop", 1): 0.85,
    ("add", "drop", 0): 0.95,
    ("add", "th", 0): 0.003,
    ("add", "drop", 1): 0.05,
    ("add", "th", 1): 0.85,
}

CSV_PORT_MAP = {
    ("A", "B"): ("in", "th"),
    ("A", "D"): ("in", "drop"),
    ("C", "B"): ("add", "th"),
    ("C", "D"): ("add", "drop"),
}


def load_mrr_s_table(
    library_dir: str | Path,
    radius_um: float = 5.0,
    channel_nm: float = 1550.0,
    wavelength_nm: float = 1550.0,
    *,
    strict: bool = False,
) -> dict[tuple[str, str, int], float]:
    """Load add-drop MRR powers from the generated S-parameter CSV library.

    The CSV stores field magnitude ``abs_S`` for ports A/B/C/D. The canonical
    spec uses power, so this loader returns ``abs_S ** 2``.
    """
    library_dir = Path(library_dir)
    on_file = library_dir / f"mrr_r{radius_um:.2f}_ch{channel_nm:.1f}_on.csv"
    off_file = library_dir / f"mrr_r{radius_um:.2f}_ch{channel_nm:.1f}_off.csv"
    if not on_file.exists() or not off_file.exists():
        message = (
            f"S-parameter files {on_file.name}/{off_file.name} not found under "
            f"{library_dir}"
        )
        if strict:
            raise FileNotFoundError(message)
        warnings.warn(
            f"{message}; falling back to MOCK_S_TABLE. Do not use these "
            "numbers in paper artifacts.",
            stacklevel=2,
        )
        return dict(MOCK_S_TABLE)

    table: dict[tuple[str, str, int], float] = {}
    table.update(_read_state_csv(off_file, state=0, wavelength_nm=wavelength_nm))
    table.update(_read_state_csv(on_file, state=1, wavelength_nm=wavelength_nm))
    missing = set(MOCK_S_TABLE) - set(table)
    if missing:
        message = (
            f"S-parameter library {library_dir} is missing {len(missing)} "
            f"entries ({sorted(missing)})"
        )
        if strict:
            raise ValueError(message)
        warnings.warn(
            f"{message}; backfilling them from MOCK_S_TABLE.",
            stacklevel=2,
        )
    for key in missing:
        table[key] = MOCK_S_TABLE[key]
    return table


def _read_state_csv(
    csv_path: Path, state: int, wavelength_nm: float
) -> dict[tuple[str, str, int], float]:
    by_pair: dict[tuple[str, str], tuple[float, float]] = {}
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pair = (row["in_port"], row["out_port"])
            if pair not in CSV_PORT_MAP:
                continue
            delta = abs(float(row["wavelength_nm"]) - wavelength_nm)
            power = float(row["abs_S"]) ** 2
            if pair not in by_pair or delta < by_pair[pair][0]:
                by_pair[pair] = (delta, power)

    table: dict[tuple[str, str, int], float] = {}
    for csv_pair, (_, power) in by_pair.items():
        port_in, port_out = CSV_PORT_MAP[csv_pair]
        table[(port_in, port_out, state)] = max(min(power, 1.0), 1e-12)
    return table
