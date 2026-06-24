"""EV2 Label Binner

Merges a per-trial physiology flat table with DEAP labels and bins each trial
into valence / arousal groups (high vs. low, split at a configurable threshold).
The resulting parquet has 4 conditions (neutral_band=0) or 6 conditions (neutral_band>0):
    valence_high | valence_low [| valence_neutral]
    arousal_high | arousal_low [| arousal_neutral]
Each condition row has:
    condition   – one of the four labels above
    epoch_id    – the original trial_id (trial_01 … trial_40)
    value       – the physiology scalar for that trial

This output is directly consumable by bootstrap_analyzer(group_col='condition',
sample_col='epoch_id') to produce per-condition mean ± CI bar plots comparable
to the EV pilot bootstrap results.

Accepted physiology input formats
----------------------------------
1. Flat table  — columns: condition/trial_id, epoch_id (optional), value/RMSSD/fai_*
   (output of interval_analyzer in flat_table mode, or asymmetry_analyzer with epoch_output)
2. Signal pointer — parquet with folder_path pointing to a folder of per-condition
   parquets (output of amplitude_analyzer with y_lim=None)
   Each per-condition file must have columns: condition, epoch_id, value

Usage
-----
    EV2_label_binner.py <physiology.parquet> <labels.parquet> \\
        [valence_threshold=5] [arousal_threshold=5] [value_col=auto] [neutral_band=0] [y_label=Value]

Outputs
-------
    <base>_binned.parquet   — flat table with 4 or 6 binned conditions (written to cwd)
"""

from __future__ import annotations

import os
import sys
import glob
from pathlib import Path

import polars as pl


def log_info(msg):    print(f"[label_binner] INFO: {msg}")
def log_warning(msg): print(f"[label_binner] WARNING: {msg}")
def log_error(msg):   print(f"[label_binner] ERROR: {msg}")


def _load_physiology(phys_path: str) -> pl.DataFrame:
    """Load physiology parquet; handles both flat tables and signal pointers."""
    df = pl.read_parquet(phys_path)

    # Signal pointer: has folder_path but no real data columns
    if 'folder_path' in df.columns and 'signal' in df.columns:
        folder = str(df['folder_path'][0])
        log_info(f"Signal pointer detected — loading per-condition files from {folder}")
        files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
        if not files:
            log_error(f"No parquet files in signal pointer folder: {folder}")
            sys.exit(1)
        dfs = [pl.read_parquet(f) for f in files]
        df = pl.concat(dfs, how='diagonal')
        log_info(f"Loaded {len(files)} per-condition file(s), {len(df)} total rows")

    return df


def _normalise_physiology(df: pl.DataFrame, value_col: str | None) -> pl.DataFrame:
    """Normalise column names to (trial_id, epoch_id, value)."""
    cols = set(df.columns)

    # Normalise trial identifier → trial_id
    if 'trial_id' not in cols and 'condition' in cols:
        df = df.rename({'condition': 'trial_id'})
        cols = set(df.columns)

    if 'trial_id' not in cols:
        log_error(f"No trial identifier column found. Available: {list(df.columns)}")
        sys.exit(1)

    # Normalise epoch_id (may already exist or == trial_id)
    if 'epoch_id' not in cols:
        df = df.with_columns(pl.col('trial_id').alias('epoch_id'))

    _META = {'trial_id', 'epoch_id', 'condition', 'metric', 'source', 'signal',
             'folder_path', 'conditions', 'time', 'sfreq', 'region'}

    # Multi-column mode: keep all non-meta numeric columns (e.g., EEG alpha/beta/theta)
    if value_col == 'auto_multi':
        num_cols = [
            c for c in df.columns
            if c not in _META and df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
        ]
        if not num_cols:
            log_error(f"No numeric value columns found for auto_multi mode. Columns: {list(df.columns)}")
            sys.exit(1)
        log_info(f"auto_multi mode: keeping columns {num_cols}")
        df = df.select(
            [pl.col('trial_id').cast(pl.Utf8), pl.col('epoch_id').cast(pl.Utf8)]
            + [pl.col(c).cast(pl.Float64) for c in num_cols]
        )
        return df

    # Normalise value column → 'value'
    if value_col is None or value_col == 'auto':
        candidates = [c for c in df.columns if c not in _META]
        if not candidates:
            log_error(f"No value column found. Columns: {list(df.columns)}")
            sys.exit(1)
        value_col = candidates[0]
        log_info(f"Auto-detected value column: {value_col}")

    if value_col not in df.columns:
        log_error(f"Value column '{value_col}' not found. Available: {list(df.columns)}")
        sys.exit(1)

    df = df.select([
        pl.col('trial_id').cast(pl.Utf8),
        pl.col('epoch_id').cast(pl.Utf8),
        pl.col(value_col).cast(pl.Float64).alias('value'),
    ])

    return df


def _load_labels(labels_path: str,
                 valence_threshold: float,
                 arousal_threshold: float,
                 neutral_band: float = 0.0) -> pl.DataFrame:
    """Load labels and add bin columns (2-way or 3-way depending on neutral_band)."""
    df = pl.read_parquet(labels_path)
    if 'trial_id' not in df.columns:
        log_error(f"Labels parquet missing 'trial_id'. Available: {list(df.columns)}")
        sys.exit(1)
    df = df.select([
        pl.col('trial_id').cast(pl.Utf8),
        pl.col('valence').cast(pl.Float64),
        pl.col('arousal').cast(pl.Float64),
    ])
    if neutral_band > 0:
        val_lo = valence_threshold - neutral_band
        val_hi = valence_threshold + neutral_band
        aro_lo = arousal_threshold - neutral_band
        aro_hi = arousal_threshold + neutral_band
        df = df.with_columns([
            pl.when(pl.col('valence') > val_hi)
              .then(pl.lit('valence_high'))
              .when(pl.col('valence') < val_lo)
              .then(pl.lit('valence_low'))
              .otherwise(pl.lit('valence_neutral'))
              .alias('valence_bin'),
            pl.when(pl.col('arousal') > aro_hi)
              .then(pl.lit('arousal_high'))
              .when(pl.col('arousal') < aro_lo)
              .then(pl.lit('arousal_low'))
              .otherwise(pl.lit('arousal_neutral'))
              .alias('arousal_bin'),
        ])
        log_info(f"3-way split: neutral band [{valence_threshold-neutral_band}, {valence_threshold+neutral_band}] "
                 f"(valence) / [{arousal_threshold-neutral_band}, {arousal_threshold+neutral_band}] (arousal)")
    else:
        df = df.with_columns([
            pl.when(pl.col('valence') >= valence_threshold)
              .then(pl.lit('valence_high'))
              .otherwise(pl.lit('valence_low'))
              .alias('valence_bin'),
            pl.when(pl.col('arousal') >= arousal_threshold)
              .then(pl.lit('arousal_high'))
              .otherwise(pl.lit('arousal_low'))
              .alias('arousal_bin'),
        ])
    return df


def bin_physiology(
    phys_path: str,
    labels_path: str,
    valence_threshold: float = 5.0,
    arousal_threshold: float = 5.0,
    value_col: str | None = None,
    neutral_band: float = 0.0,
) -> str:
    """Merge per-trial physiology with binned labels and write combined parquet.

    Returns the path to the output *_binned.parquet file.
    """
    log_info(f"Binning {os.path.basename(phys_path)} with labels {os.path.basename(labels_path)}")
    log_info(f"Thresholds — valence: {valence_threshold}, arousal: {arousal_threshold}")

    phys_df   = _load_physiology(phys_path)
    phys_df   = _normalise_physiology(phys_df, value_col)
    labels_df = _load_labels(labels_path, valence_threshold, arousal_threshold, neutral_band)

    merged = phys_df.join(
        labels_df.select(['trial_id', 'valence_bin', 'arousal_bin']),
        on='trial_id',
        how='inner',
    )

    n_matched = len(merged)
    n_total   = len(phys_df)
    if n_matched == 0:
        log_error("No rows matched after join — check that trial_id values align "
                  "between physiology and labels files.")
        sys.exit(1)
    if n_matched < n_total:
        log_warning(f"Only {n_matched}/{n_total} physiology rows matched label trial_ids")

    # Detect value columns (single 'value' or auto_multi with named columns)
    _BIN_META = {'trial_id', 'epoch_id', 'valence_bin', 'arousal_bin', 'valence', 'arousal'}
    value_cols = [c for c in merged.columns if c not in _BIN_META]

    # Build two views: valence-binned and arousal-binned, then stack them.
    # Each trial appears once in the valence view and once in the arousal view.
    valence_rows = merged.select(
        [pl.col('valence_bin').alias('condition'), pl.col('trial_id').alias('epoch_id')]
        + [pl.col(c) for c in value_cols]
    )
    arousal_rows = merged.select(
        [pl.col('arousal_bin').alias('condition'), pl.col('trial_id').alias('epoch_id')]
        + [pl.col(c) for c in value_cols]
    )
    combined = pl.concat([valence_rows, arousal_rows])

    # Log condition counts
    for cond, n in combined.group_by('condition').agg(pl.len().alias('n')).sort('condition').to_dicts():
        # to_dicts() returns list of dicts; iterate properly
        pass
    for row in combined.group_by('condition').agg(pl.len().alias('n')).sort('condition').to_dicts():
        log_info(f"  {row['condition']}: {row['n']} trial(s)")

    base      = os.path.splitext(os.path.basename(phys_path))[0]
    out_path  = os.path.join(os.getcwd(), f"{base}_binned.parquet")
    combined.write_parquet(out_path, compression='gzip')
    n_conds = 6 if neutral_band > 0 else 4
    log_info(f"Output ({len(combined)} rows, {n_conds} conditions): {out_path}")
    print(out_path)
    return out_path


if __name__ == '__main__':
    args = sys.argv[1:]
    # Strip Nextflow terminal / log tokens from the end
    # Convention: IOInterface appends a 'terminal' token that shows up as a
    # positional argument; filter it out before parsing
    clean = [a for a in args if a not in ('terminal', 'group_log', 'result')]

    if len(clean) < 2:
        print(
            "[label_binner] Usage: EV2_label_binner.py <physiology.parquet> <labels.parquet> "
            "[valence_threshold=5] [arousal_threshold=5] [value_col=auto]"
        )
        sys.exit(1)

    phys_path    = clean[0]
    labels_path  = clean[1]
    val_thresh   = float(clean[2]) if len(clean) > 2 else 5.0
    aro_thresh   = float(clean[3]) if len(clean) > 3 else 5.0
    vcol         = clean[4] if len(clean) > 4 and clean[4] != 'auto' else None
    nband        = float(clean[5]) if len(clean) > 5 else 0.0

    bin_physiology(phys_path, labels_path, val_thresh, aro_thresh, vcol, nband)
