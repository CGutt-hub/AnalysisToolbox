"""Correlation Analyzer

Spearman/Pearson correlation between per-trial physiology metrics and
continuous ratings (e.g. DEAP valence/arousal).

Accepts two input formats for the metrics parquet:
  1. Flat tidy table  — columns: condition|trial_id, metric_col1, metric_col2, …
  2. Plot-spec table  — columns: condition, x_data (list of trial ids), y_data (list of values)
     (output of amplitude_analyzer, hrv_analyzer, etc.)  ← auto-detected

Joins on trial_id / condition, runs Spearman (default) or Pearson for each
metric × each rating column, outputs a table plot-spec parquet.

Usage:
    correlation_analyzer.py <metrics.parquet> <labels.parquet> \
        [rating_cols] [metric_cols] [method=spearman]

Arguments:
    metrics.parquet   Per-trial physiology (condition/trial_id + metric columns,
                      or plot-spec format)
    labels.parquet    Ratings parquet with trial_id column
    rating_cols       Comma-separated rating names (default: valence,arousal)
    metric_cols       Comma-separated metric names (default: auto)
    method            spearman (default) | pearson
"""
import polars as pl, numpy as np, sys, os
from scipy import stats


def log_info(msg):    print(f"[correlation] INFO: {msg}")
def log_warning(msg): print(f"[correlation] WARNING: {msg}")


def _is_plot_spec(df: pl.DataFrame) -> bool:
    return 'x_data' in df.columns and 'y_data' in df.columns


def _plot_spec_to_tidy(df: pl.DataFrame, metric_name: str | None = None) -> pl.DataFrame:
    """Convert a plot-spec parquet (bar type, one row per condition) to a tidy table.

    Handles two common shapes:
      - One row per condition, x_data = list of x labels, y_data = list of floats
        (amplitude_analyzer, hrv_analyzer per-condition output)
      - One row per condition where x_data[0] is the condition name and y_data[0] is the scalar
    """
    rows = []
    for record in df.to_dicts():
        cond  = record.get('condition', '')
        x_lst = record.get('x_data', [])
        y_lst = record.get('y_data', [])
        if not x_lst and not y_lst:
            continue
        # Case: x_data = list of sub-condition labels, y_data = list of values
        if isinstance(x_lst, list) and isinstance(y_lst, list) and len(x_lst) == len(y_lst):
            for x_val, y_val in zip(x_lst, y_lst):
                try:
                    rows.append({'trial_id': str(x_val), metric_name or 'value': float(y_val)})
                except (TypeError, ValueError):
                    pass
        # Case: scalar value stored as single-element list
        elif isinstance(y_lst, list) and len(y_lst) == 1:
            try:
                rows.append({'trial_id': cond, metric_name or 'value': float(y_lst[0])})
            except (TypeError, ValueError):
                pass
        # Case: y_data is a flat scalar
        elif not isinstance(y_lst, list):
            try:
                rows.append({'trial_id': cond, metric_name or 'value': float(y_lst)})
            except (TypeError, ValueError):
                pass
    return pl.DataFrame(rows) if rows else pl.DataFrame({'trial_id': [], metric_name or 'value': []})


def correlation_analyze(
    metrics_path: str,
    labels_path: str,
    rating_cols: list[str] | None = None,
    metric_cols: list[str] | None = None,
    method: str = 'spearman',
) -> str:
    log_info(f"Correlating {os.path.basename(metrics_path)} × {os.path.basename(labels_path)}")
    metrics_df = pl.read_parquet(metrics_path)
    labels_df  = pl.read_parquet(labels_path)

    # ── Resolve signal pointer files (folder of per-condition epoch parquets) ──
    if 'signal' in metrics_df.columns and 'folder_path' in metrics_df.columns:
        import glob as _glob
        folder = str(metrics_df['folder_path'][0])
        log_info(f"Detected signal file — resolving from folder: {folder}")
        data_files = sorted(_glob.glob(os.path.join(folder, "*.parquet")))
        if not data_files:
            log_warning(f"Signal folder is empty: {folder}, no data to correlate")
        else:
            metrics_df = pl.concat([pl.read_parquet(f) for f in data_files], how='diagonal')
            log_info(f"Loaded {len(data_files)} files from signal folder ({len(metrics_df)} rows)")

    # ── Normalise input format ──────────────────────────────────────────
    if _is_plot_spec(metrics_df):
        log_info("Detected plot-spec format — converting to tidy table")
        base_metric = os.path.splitext(os.path.basename(metrics_path))[0]
        metrics_df = _plot_spec_to_tidy(metrics_df, base_metric)

    # Normalise ID column: accept 'condition' as alias for 'trial_id' (metrics and labels)
    if 'condition' in metrics_df.columns and 'trial_id' not in metrics_df.columns:
        metrics_df = metrics_df.rename({'condition': 'trial_id'})
    if 'trial_id' not in metrics_df.columns:
        log_warning("No 'trial_id' or 'condition' column in metrics — cannot join")

    if 'condition' in labels_df.columns and 'trial_id' not in labels_df.columns:
        labels_df = labels_df.rename({'condition': 'trial_id'})

    # ── Join ──────────────────────────────────────────────────────────
    # Labels uses 'trial_id'; deduplicate to avoid cartesian product
    labels_df = labels_df.unique(subset=['trial_id'])
    merged = metrics_df.join(labels_df, on='trial_id', how='inner')
    log_info(f"Joined {len(merged)} rows (metrics n={len(metrics_df)}, labels n={len(labels_df)})")
    if len(merged) == 0:
        log_warning("No matching trial_ids — check that conditions are named 'trial_NN'")

    # ── Resolve column lists ──────────────────────────────────────────
    if rating_cols is None:
        rating_cols = [c for c in ('valence', 'arousal', 'dominance', 'liking')
                       if c in merged.columns]
    else:
        rating_cols = [c for c in rating_cols if c in merged.columns]

    id_cols = {'trial_id', 'condition', 'epoch_id', 'source',
               'valence', 'arousal', 'dominance', 'liking'}
    numeric_types = (pl.Float32, pl.Float64, pl.Int32, pl.Int64, pl.Int16, pl.Int8,
                     pl.UInt32, pl.UInt64, pl.UInt16, pl.UInt8)
    if metric_cols is None:
        metric_cols = [c for c in metrics_df.columns
                       if c not in id_cols and merged[c].dtype in numeric_types]
    else:
        metric_cols = [c for c in metric_cols if c in merged.columns]

    log_info(f"Metrics: {metric_cols}  |  Ratings: {rating_cols}  |  N={len(merged)}  |  method={method}")

    # ── Correlate ─────────────────────────────────────────────────────
    corr_fn = stats.spearmanr if method == 'spearman' else stats.pearsonr
    records = []
    for mc in metric_cols:
        row: dict = {'metric': mc}
        valid_mask = merged[mc].is_not_null()
        x = merged.filter(valid_mask)[mc].to_numpy()
        for rc in rating_cols:
            y = merged.filter(valid_mask)[rc].to_numpy()
            if len(x) < 5:
                r, p = float('nan'), float('nan')
            else:
                result = corr_fn(x, y)
                r, p = float(result.statistic), float(result.pvalue)
            row[f'{rc}_r'] = round(r, 4)
            row[f'{rc}_p'] = round(p, 4)
            row[f'{rc}_sig'] = '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else ''
        records.append(row)

    result_df = pl.DataFrame(records) if records else pl.DataFrame()

    # ── Output as table plot-spec ─────────────────────────────────────
    col_order = ['metric'] + [
        f for rc in rating_cols
        for f in (f'{rc}_r', f'{rc}_p', f'{rc}_sig')
        if f in (result_df.columns if records else [])
    ]
    available_cols = [c for c in col_order if c in result_df.columns]
    table_rows = result_df.select(available_cols).to_dicts() if records else []

    base = os.path.splitext(os.path.basename(metrics_path))[0]
    out_path = os.path.join(os.getcwd(), f"{base}_correlation.parquet")
    pl.DataFrame({
        'condition': ['correlation'],
        'x_data':    [available_cols],
        'y_data':    [[[str(v) for v in row.values()] for row in table_rows]],
        'y_var':     [[[]]],
        'plot_type': ['table'],
        'x_label':   ['Metric'],
        'y_label':   [f'{method.capitalize()} correlation (N={len(merged)})'],
        'y_ticks':   [''],
    }).write_parquet(out_path, compression='snappy')
    log_info(f"Output: {out_path}")
    print(out_path)
    return out_path


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 3:
        print('[correlation] Usage: correlation_analyzer.py <metrics.parquet> <labels.parquet> '
              '[rating_cols] [metric_cols] [method=spearman]')
        sys.exit(1)
    rating_cols = a[3].split(',') if len(a) > 3 and a[3] not in ('None', '') else None
    metric_cols = a[4].split(',') if len(a) > 4 and a[4] not in ('None', '') else None
    method      = a[5] if len(a) > 5 and a[5] not in ('None', '') else 'spearman'
    correlation_analyze(a[1], a[2], rating_cols, metric_cols, method)
