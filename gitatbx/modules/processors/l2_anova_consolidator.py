"""Consolidate modality-specific L2 ANOVA tables into one long-format summary.

Usage:
    python l2_anova_consolidator.py <anova1.parquet> <anova2.parquet> ... [--modality-map <json>] <out_basename>

Input tables are expected to come from anova_analyzer.py and contain one row with:
- x_data: column names (e.g., DV, F, df1, df2, p, eta_sq/eta2, sig)
- y_data: column vectors aligned with x_data

Output is a plain long-format parquet with one row per (modality, DV), including
eta-squared interpretation for quick hypothesis readout.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import polars as pl


def log_info(msg: str) -> None:
    print(f"[l2_consolidator] INFO: {msg}")


def log_warning(msg: str) -> None:
    print(f"[l2_consolidator] WARNING: {msg}")


def parse_cli(argv: List[str]) -> tuple[List[str], str, Dict[str, List[str]]]:
    if len(argv) < 3:
        raise ValueError(
            "Usage: l2_anova_consolidator.py <anova1.parquet> <anova2.parquet> ... [--modality-map <json>] <out_basename>"
        )

    args = argv[1:]
    mapping: Dict[str, List[str]] = {}

    i = 0
    while i < len(args):
        if args[i] == "--modality-map":
            if i + 1 >= len(args):
                raise ValueError("Missing value for --modality-map")
            try:
                raw_map = json.loads(args[i + 1])
                mapping = {
                    str(k): [str(x).lower() for x in (v if isinstance(v, list) else [v])]
                    for k, v in raw_map.items()
                }
            except Exception as exc:
                raise ValueError(f"Invalid JSON in --modality-map: {exc}") from exc
            del args[i : i + 2]
            continue
        i += 1

    in_files = [a for a in args if a.endswith(".parquet") and os.path.exists(a)]
    non_files = [a for a in args if not (a.endswith(".parquet") and os.path.exists(a))]

    if not in_files:
        raise ValueError("No input parquet files found.")
    if not non_files:
        raise ValueError("Missing out_basename.")

    out_base = non_files[-1]
    return in_files, out_base, mapping


def modality_from_path(path: str, mapping: Dict[str, List[str]]) -> str:
    name = os.path.basename(path).lower()
    if mapping:
        for modality, patterns in mapping.items():
            if any(pattern in name for pattern in patterns):
                return modality

    if "eeg_frontal" in name:
        return "eeg_frontal"
    if "eeg_parietal" in name:
        return "eeg_parietal"
    if "eda" in name:
        return "eda"
    if "hrv" in name:
        return "hrv"
    if "fai" in name:
        return "fai"
    return "unknown"


def normalize_column(name: str) -> str:
    raw = str(name).strip()
    mapping = {
        "DV": "dv",
        "dv": "dv",
        "F": "F",
        "df1": "df1",
        "df2": "df2",
        "p": "p",
        "p (FDR-corrected)": "p",
        "eta_sq": "eta_sq",
        "eta2": "eta_sq",
        "sig": "sig",
        "eta_sq_label": "eta_sq_label",
    }
    # Keep backward compatibility with the unicode eta-squared label.
    if raw == "η²":
        return "eta_sq"
    return mapping.get(raw, raw.lower().replace(" ", "_"))


def eta_sq_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.01:
        return "negligible"
    if value < 0.06:
        return "small"
    if value < 0.14:
        return "medium"
    return "large"


def parse_anova_table(path: str, modality_map: Dict[str, List[str]]) -> pl.DataFrame:
    df = pl.read_parquet(path)
    if df.height == 0:
        log_warning(f"Skipping empty file: {path}")
        return pl.DataFrame([])

    row = df.to_dicts()[0]
    x_data = row.get("x_data", [])
    y_data = row.get("y_data", [])

    if not isinstance(x_data, list) or not isinstance(y_data, list) or len(x_data) == 0:
        log_warning(f"Skipping unexpected format: {path}")
        return pl.DataFrame([])

    columns: Dict[str, List] = {}
    for idx, col_name in enumerate(x_data):
        key = normalize_column(col_name)
        if idx < len(y_data) and isinstance(y_data[idx], list):
            columns[key] = y_data[idx]

    if "dv" not in columns:
        log_warning(f"Skipping table without DV column: {path}")
        return pl.DataFrame([])

    table = pl.DataFrame(columns)

    keep = [c for c in ["dv", "F", "df1", "df2", "p", "eta_sq", "sig"] if c in table.columns]
    table = table.select(keep)

    for col, dtype in [("F", pl.Float64), ("p", pl.Float64), ("eta_sq", pl.Float64), ("df1", pl.Int64), ("df2", pl.Int64)]:
        if col in table.columns:
            table = table.with_columns(pl.col(col).cast(dtype, strict=False))

    modality = modality_from_path(path, modality_map)
    table = table.with_columns([
        pl.lit(modality).alias("modality"),
        pl.lit(os.path.basename(path)).alias("source_file"),
    ])

    if "eta_sq" in table.columns:
        table = table.with_columns(
            pl.col("eta_sq").map_elements(eta_sq_label, return_dtype=pl.String).alias("eta_sq_label")
        )

    if "p" in table.columns:
        table = table.with_columns((pl.col("p") < 0.05).alias("reject_h0_p_lt_0_05"))

    return table.select(
        [
            c
            for c in [
                "modality",
                "dv",
                "F",
                "df1",
                "df2",
                "p",
                "sig",
                "eta_sq",
                "eta_sq_label",
                "reject_h0_p_lt_0_05",
                "source_file",
            ]
            if c in table.columns
        ]
    )


def main(argv: List[str]) -> int:
    try:
        in_files, out_base, modality_map = parse_cli(argv)
    except ValueError as exc:
        print(str(exc))
        return 1

    frames = [parse_anova_table(path, modality_map) for path in in_files]
    frames = [f for f in frames if f.height > 0]

    if not frames:
        log_warning("No valid ANOVA tables could be parsed.")
        return 1

    result = pl.concat(frames, how="diagonal")
    if "modality" in result.columns and "dv" in result.columns:
        result = result.sort(["modality", "dv"])

    out_path = os.path.join(os.getcwd(), f"{out_base}.parquet")
    result.write_parquet(out_path, compression="snappy")
    log_info(f"Wrote consolidated L2 ANOVA summary: {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
