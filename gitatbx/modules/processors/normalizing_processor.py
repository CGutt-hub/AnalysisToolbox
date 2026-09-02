#!/usr/bin/env python3
"""
Atomic Processor: Normalizes feature columns within participant groups (Z-scoring).
Saves standardized parquet and writes destination file path to stdout.
"""
import sys
import os
import polars as pl


def log_info(msg: str) -> None:
    # Suppress stderr for routine logs to avoid false runner [ERROR] tags
    pass


def log_error(msg: str) -> None:
    print(f"[standardizer] ERROR: {msg}", file=sys.stderr)


def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df


def main() -> None:
    if len(sys.argv) < 4:
        log_error("CRITICAL Usage: <input.parquet> <group_col> <feature_cols_comma_str>")
        sys.exit(1)

    ip = sys.argv[1]
    group_col = sys.argv[2].strip(" '\"\\[]")
    feature_cols = [c.strip(" '\"\\[]") for c in sys.argv[3].split(',') if c.strip(" '\"\\[]")]

    log_info(f"Standardizing features on dataset: {ip}")

    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"CRITICAL: Input file missing or empty: {ip}")
        sys.exit(1)

    try:
        df = pl.read_parquet(ip)
        df = ensure_flat_dataframe(df)
    except Exception as e:
        log_error(f"CRITICAL: Parquet parsing failure: {e}")
        sys.exit(1)

    if group_col not in df.columns:
        log_error(f"CRITICAL: Group column '{group_col}' missing from schema.")
        sys.exit(1)

    missing_features = [f for f in feature_cols if f not in df.columns]
    if missing_features:
        log_error(f"CRITICAL: Feature columns missing from dataset: {missing_features}")
        sys.exit(1)

    exprs = []
    for f in feature_cols:
        mean_col = pl.col(f).mean().over(group_col)
        std_col = pl.col(f).std().over(group_col)
        exprs.append(
            pl.when(std_col > 1e-8)
            .then((pl.col(f) - mean_col) / std_col)
            .otherwise(0.0)
            .alias(f)
        )

    df_scaled = df.with_columns(exprs)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_standardized.parquet")
    df_scaled.write_parquet(out_file, compression='zstd')

    # Output file location MUST be printed last on stdout for Nextflow capture
    print(out_file)


if __name__ == '__main__':
    main()