"""FAI Epoch Processor — computes per-epoch FAI from per-condition channel PSD parquets.

Input: 3 per-condition channel-level PSD files (columns: condition, epoch_id, channel, band, power)
Args:  <pairs_str>  <band>  [terminal]
Output: flat table (condition, epoch_id, fai_<left>_<right> per pair)
"""
import polars as pl
import numpy as np
import sys
import ast
import os


def log_info(msg):  print(f"[fai_epoch] INFO: {msg}")
def log_warning(msg): print(f"[fai_epoch] WARNING: {msg}")
def log_error(msg):   print(f"[fai_epoch] ERROR: {msg}"); sys.exit(1)


def fai_epoch_process(input_files: list, pairs: list, band: str) -> str:
    """Compute per-epoch FAI = ln(right_alpha) - ln(left_alpha) from per-condition channel PSD files."""
    dfs = []
    for f in input_files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
        dfs.append(pl.read_parquet(f))
    df = pl.concat(dfs, how='diagonal')

    # Filter to requested band
    if 'band' in df.columns:
        df = df.filter(pl.col('band') == band)
        if df.is_empty():
            log_error(f"No data for band '{band}'. Available bands: {pl.concat(dfs).select('band').unique().to_series().to_list()}")
    else:
        log_warning("No 'band' column found — using all data as single band")

    # Pivot channels wide: (condition, epoch_id) × channel → power
    pivot = df.pivot(values='power', index=['condition', 'epoch_id'], on='channel')
    available_channels = [c for c in pivot.columns if c not in ('condition', 'epoch_id')]
    log_info(f"Available channels after pivot: {available_channels}")

    fai_cols = []
    for left, right in pairs:
        col_name = f'fai_{left}_{right}'
        if left not in pivot.columns:
            log_warning(f"Left channel '{left}' not found — skipping pair ({left}, {right})")
            continue
        if right not in pivot.columns:
            log_warning(f"Right channel '{right}' not found — skipping pair ({left}, {right})")
            continue
        # FAI = ln(right) - ln(left), guard against non-positive values
        pivot = pivot.with_columns(
            pl.when((pl.col(right) > 0) & (pl.col(left) > 0))
              .then(pl.col(right).log() - pl.col(left).log())
              .otherwise(None)
              .alias(col_name)
        )
        fai_cols.append(col_name)

    if not fai_cols:
        log_error(f"No valid electrode pairs found in data. Configured pairs: {pairs}. Available channels: {available_channels}")

    out_df = pivot.select(['condition', 'epoch_id'] + fai_cols)
    log_info(f"FAI epoch table: {len(out_df)} rows, {fai_cols}")

    base = '_'.join(os.path.splitext(os.path.basename(input_files[0]))[0].split('_')[:2])
    out_file = os.path.join(os.getcwd(), f"{base}_fai_epochs.parquet")
    out_df.write_parquet(out_file, compression='snappy')
    print(f"[fai_epoch] Output: {out_file}")
    print(out_file)
    return out_file


if __name__ == '__main__':
    a = sys.argv[1:]
    # Separate input files (.parquet) from remaining args (pairs, band)
    files = []
    rest = a
    for i, v in enumerate(a):
        if v.endswith('.parquet') and os.path.exists(v):
            files.append(v)
        else:
            rest = a[i:]
            break

    if len(files) < 1 or len(rest) < 2:
        print('[fai_epoch] Usage: fai_epoch_processor.py <f1.parquet> [f2 f3] <pairs> <band> [terminal]')
        print('  Example: fai_epoch_processor.py psd1.parquet psd2.parquet psd3.parquet "[('F3','F4')]" alpha terminal')
        sys.exit(1)

    pairs_str = rest[0]
    band_name = rest[1]
    try:
        pairs_parsed = ast.literal_eval(pairs_str)
    except Exception as e:
        log_error(f"Could not parse pairs argument '{pairs_str}': {e}")

    fai_epoch_process(files, pairs_parsed, band_name)
