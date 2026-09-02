#!/usr/bin/env python3
"""Aggregation Processor Module - Generic Multi-File Summary Aggregator."""
import sys, os, polars as pl

def log_info(msg: str) -> None:  print(f"[aggregator] INFO: {msg}")
def log_error(msg: str) -> None: print(f"[aggregator] ERROR: {msg}", file=sys.stderr)

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def run_aggregation(group_by_cols: list[str], files: list[str]) -> str:
    log_info(f"Running aggregation processor on {len(files)} file(s) with group_by={group_by_cols}")

    if not group_by_cols:
        log_error("CRITICAL: Grouping columns (group_by_cols) must be explicitly provided.")
        sys.exit(1)

    if not files:
        log_error("CRITICAL: At least one input parquet file must be provided.")
        sys.exit(1)

    dfs = []
    for f in files:
        if not os.path.exists(f) or os.path.getsize(f) <= 12:
            log_error(f"CRITICAL: Input file missing or invalid: {f}")
            sys.exit(1)
        try:
            pl_df = pl.read_parquet(f)
            pl_df = ensure_flat_dataframe(pl_df)
            dfs.append(pl_df)
        except Exception as e:
            log_error(f"CRITICAL: Failed to read parquet file {f}: {e}")
            sys.exit(1)

    combined_df = pl.concat(dfs, how='diagonal_relaxed')

    missing_group_cols = [c for c in group_by_cols if c not in combined_df.columns]
    if missing_group_cols:
        log_error(f"CRITICAL: Declared group_by columns missing from combined dataset: {missing_group_cols}")
        sys.exit(1)

    agg_exprs = []
    for col, dtype in combined_df.schema.items():
        if col in group_by_cols:
            continue

        if dtype == pl.Boolean or (dtype in [pl.Int8, pl.Int16, pl.Int32, pl.Int64] and col.startswith('is_')):
            agg_exprs.append(pl.col(col).count().alias("total_count"))
            agg_exprs.append(pl.col(col).mean().round(4).alias(f"mean_{col}"))
        elif dtype in [pl.Float32, pl.Float64]:
            agg_exprs.append(pl.col(col).mean().round(4).alias(f"mean_{col}"))
        elif dtype in [pl.Int8, pl.Int16, pl.Int32, pl.Int64]:
            agg_exprs.append(pl.col(col).n_unique().alias(f"unique_{col}"))
        elif dtype == pl.Utf8:
            agg_exprs.append(pl.col(col).first().alias(col))

    if not agg_exprs:
        agg_exprs.append(pl.len().alias("count"))

    summary_df = (
        combined_df.group_by(group_by_cols)
        .agg(agg_exprs)
        .sort(group_by_cols)
    )

    base = os.path.splitext(os.path.basename(files[0]))[0]
    out_path = os.path.join(os.getcwd(), f"{base}_aggregation_summary.parquet")
    summary_df.write_parquet(out_path, compression='gzip')

    log_info(f"Aggregated summary written: {out_path}")
    print(out_path)
    return out_path

if __name__ == '__main__':
    if len(sys.argv) < 2:
        log_error("CRITICAL: Arguments required.")
        sys.exit(1)

    raw_args = sys.argv[1:]
    input_files = []
    group_cols = []

    for arg in raw_args:
        cleaned = arg.strip(" '\"\\[]")
        if not cleaned:
            continue
        if cleaned.endswith('.parquet') or os.path.exists(cleaned):
            input_files.append(cleaned)
        else:
            group_cols.extend([c.strip(" '\"\\[]") for c in cleaned.split(',') if c.strip(" '\"\\[]")])

    if not input_files:
        cwd_parquets = [os.path.join(os.getcwd(), f) for f in os.listdir(os.getcwd()) if f.endswith('.parquet')]
        input_files = [p for p in cwd_parquets if '_aggregation_summary' not in p]

    run_aggregation(group_cols, input_files)