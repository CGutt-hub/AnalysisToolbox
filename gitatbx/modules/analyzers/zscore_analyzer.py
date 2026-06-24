"""Z-Score Analyzer — Generic per-observation z-score computation with outlier flagging.

Usage:
    zscore_analyzer.py <input.parquet> <group_col> [value_cols] [sample_col] [threshold]

Reads any epoch-level parquet and annotates each observation with z-scores
and outlier flags within its grouping, producing a quality-inspection-ready output.

Args:
    input.parquet : Input data (any epoch-level or observation-level parquet)
    group_col     : Column defining groups for z-score computation (e.g. 'condition')
    value_cols    : Columns to inspect (e.g. "['alpha','beta','theta']" or 'None' for auto-detect)
    sample_col    : Column identifying individual observations (e.g. 'epoch_id'; auto-detected if 'None')
    threshold     : Z-score threshold for outlier flagging (default 3.0)

Output columns (per value column):
    <original columns>
    {col}_z     : float  — z-score within group
    {col}_out   : bool   — flagged as outlier (|z| > threshold)
"""
import sys
import os
import ast
import polars as pl
import numpy as np

# Logging helpers
def log_info(msg): print(f"[zscore] INFO: {msg}")
def log_warning(msg): print(f"[zscore] WARNING: {msg}")
def log_error(msg): print(f"[zscore] ERROR: {msg}")


def inspect_quality(ip: str, group_col: str = 'condition', value_cols: list | None = None,
                    sample_col: str | None = None, threshold: float = 3.0) -> str:
    """Compute per-observation z-scores and outlier flags within groups.

    Args:
        ip: Input parquet file
        group_col: Column defining groups for normalization (e.g. 'condition', 'band')
        value_cols: Numeric columns to inspect; auto-detected if None
        sample_col: Column identifying observations; auto-detected if None
        threshold: Z-score cutoff for outlier flagging
    """
    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if len(df) == 0:
        log_error("Empty input DataFrame — nothing to inspect, halting branch.")
        sys.exit(1)

    # Pass through sentinel files from failed upstream processes
    if '_sentinel' in df.columns:
        basename = os.path.basename(ip).replace('.parquet', '')
        df.write_parquet(f"sentinel_quality_{basename}.parquet", compression='gzip')
        return f"sentinel_quality_{basename}.parquet"

    # Auto-detect sample column (first string/categorical column that isn't group_col)
    if sample_col is None:
        for c in df.columns:
            if c != group_col and df[c].dtype in (pl.Utf8, pl.Categorical):
                sample_col = c
                break
    log_info(f"Group: {group_col}, Sample: {sample_col}")

    # Identify which columns carry the data for grouping context
    meta_cols = {group_col}
    if sample_col:
        meta_cols.add(sample_col)

    # Auto-detect numeric value columns if not specified
    if value_cols is None:
        value_cols = [c for c in df.columns if c not in meta_cols
                      and df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)]
    log_info(f"Value columns: {value_cols}")

    if not value_cols:
        log_warning("No numeric columns found to inspect.")
        return ""

    # Compute z-scores and outlier flags per group per value column
    for col in value_cols:
        mean_expr = pl.col(col).mean().over(group_col)
        std_expr = pl.col(col).std().over(group_col)

        df = df.with_columns(
            pl.when(std_expr > 0)
            .then((pl.col(col) - mean_expr) / std_expr)
            .otherwise(0.0)
            .alias(f'{col}_z')
        )
        df = df.with_columns(
            (pl.col(f'{col}_z').abs() > threshold).alias(f'{col}_out')
        )

    # Summary statistics
    n_total = df.height
    for col in value_cols:
        n_out = df.filter(pl.col(f'{col}_out')).height
        pct = (n_out / n_total * 100) if n_total > 0 else 0
        log_info(f"{col}: {n_out}/{n_total} flagged ({pct:.1f}%)")

    # Write output
    basename = os.path.basename(ip).replace('.parquet', '')
    parts = basename.split('_')
    pid = '_'.join(parts[:2]) if len(parts) >= 2 else 'unknown'
    output_name = f"{pid}_quality.parquet"
    df.write_parquet(output_name, compression='gzip')
    log_info(f"{pid} -> {output_name}")
    return output_name


def main():
    if len(sys.argv) < 3:
        print("Usage: zscore_analyzer.py <input.parquet> <group_col> [value_cols] [sample_col] [threshold]")
        sys.exit(1)

    ip = sys.argv[1]
    group_col = sys.argv[2]

    value_cols = None
    if len(sys.argv) > 3 and sys.argv[3] != 'None':
        value_cols = ast.literal_eval(sys.argv[3])

    sample_col = None
    if len(sys.argv) > 4 and sys.argv[4] != 'None':
        sample_col = sys.argv[4]

    threshold = 3.0
    if len(sys.argv) > 5 and sys.argv[5] != 'None':
        threshold = float(sys.argv[5])

    inspect_quality(ip, group_col, value_cols, sample_col, threshold)


if __name__ == '__main__':
    main()
