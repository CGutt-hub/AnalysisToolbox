"""Correlation Analyzer - Compute pairwise Pearson correlations, output as heatmap.

Single-file mode:  all numeric columns in the file are correlated directly.

Multi-file mode:   each file is aggregated to condition-level means, columns are
                   renamed to avoid clashes (using the file stem), then all tables
                   are joined on 'condition' before correlating.  Useful for
                   cross-modal correlations without a dedicated join processor.

Consolidate-L2 mode (--consolidate-l2):
                   Takes per-participant correlation heatmap parquets produced by
                   this module's standard mode.  For every off-diagonal pair
                   extracts the r values across participants, then computes a
                   group-level mean r, SE, bootstrap 95% CI, and one-sample
                   t-test vs 0.  Outputs a group-level heatmap (mean r) plus a
                   flat summary table.  Use this to aggregate within-participant
                   correlation results up to the group level (L2).

Correlate-ratings mode (--correlate-ratings):
                   Correlates per-trial physiology metrics against continuous
                   ratings (e.g. DEAP valence/arousal) using Spearman (default)
                   or Pearson.  Accepts flat tidy tables or plot-spec parquets
                   (amplitude_analyzer / hrv_analyzer output) as the metrics
                   file, and resolves signal pointer files automatically.
                   Outputs a table plot-spec parquet.

Usage:
    correl_analyzer.py <file1.parquet> [file2.parquet ...] [y_lim]
    correl_analyzer.py --consolidate-l2 <out_base> <file1.parquet> ...
    correl_analyzer.py <metrics.parquet> <labels.parquet> \\
        --correlate-ratings [rating_cols] [metric_cols] [method=spearman]
"""
import polars as pl, polars.selectors as cs, sys, os, numpy as np, glob as _glob
from scipy.stats import pearsonr, ttest_1samp, spearmanr
from scipy import stats as _scipy_stats

def log_info(msg):    print(f"[correl] INFO: {msg}")
def log_warning(msg): print(f"[correl] WARNING: {msg}")
def log_error(msg):   print(f"[correl] ERROR: {msg}")

_EXCLUDE = {'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id', 'condition', 'region'}


def _build_df(files: list) -> pl.DataFrame:
    """Return a wide correlation-ready DataFrame from one or more input parquets."""
    if len(files) == 1:
        return pl.read_parquet(files[0])

    # Multi-file: aggregate each to condition-level means, rename cols, join
    agg_tables = []
    for f in files:
        df = pl.read_parquet(f)
        stem = os.path.splitext(os.path.basename(f))[0]
        parts = stem.split('_')
        label = '_'.join(parts[2:]) if len(parts) > 2 else stem

        num_cols = [c for c in df.select(cs.numeric()).columns if c not in _EXCLUDE]
        agg = df.group_by('condition').agg([pl.col(c).mean() for c in num_cols])
        renames = {c: f"{label}_{c}" if c == 'value' else c for c in num_cols}
        agg = agg.rename(renames)
        agg_tables.append(agg)

    joined = agg_tables[0]
    for t in agg_tables[1:]:
        joined = joined.join(t, on='condition', how='inner')

    if joined.height == 0:
        log_error("No shared conditions across input files"); sys.exit(1)
    log_info(f"Multi-file join: {joined.height} conditions, columns: {joined.columns}")
    return joined


def correl_analyze(files: list, y_lim=None) -> str:
    for f in files:
        if not os.path.exists(f): log_error(f"File not found: {f}"); sys.exit(1)

    df = _build_df(files)
    num_cols = [c for c in df.select(cs.numeric()).columns if c not in _EXCLUDE]
    if len(num_cols) < 2: log_error("Need at least 2 numeric columns"); sys.exit(1)
    if df.height < 2: log_warning(f"Too few rows ({df.height}) -- skipping"); sys.exit(0)

    n = len(num_cols)
    r_matrix = [[0.0] * n for _ in range(n)]
    p_matrix = [[1.0] * n for _ in range(n)]
    for i, c1 in enumerate(num_cols):
        for j, c2 in enumerate(num_cols):
            if i == j:
                r_matrix[i][j] = 1.0; p_matrix[i][j] = 0.0
            elif i < j:
                a = df[c1].drop_nulls().to_numpy()
                b = df[c2].drop_nulls().to_numpy()
                mn = min(len(a), len(b))
                if mn < 2: continue
                _pr = pearsonr(a[:mn], b[:mn])
                r_matrix[i][j] = r_matrix[j][i] = float(_pr.statistic)  # type: ignore[arg-type]
                p_matrix[i][j] = p_matrix[j][i] = float(_pr.pvalue)  # type: ignore[arg-type]

    base = os.path.splitext(os.path.basename(files[0]))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_correl.parquet")
    pl.DataFrame([{
        'x_data':   num_cols,
        'y_data':   r_matrix,
        'y_var':    p_matrix,
        'plot_type': 'heatmap',
        'x_label':  'Measure',
        'y_label':  'Measure',
        'y_ticks':  y_lim,
    }]).write_parquet(out_file, compression='gzip')
    log_info(f"Output: {out_file} ({n} variables)")
    print(out_file)
    return out_file


def consolidate_l2(files: list, out_base: str = 'l2_correl') -> str:
    """Aggregate per-participant correlation heatmaps to group level.

    Each input parquet must be a standard correl_analyzer output (plot_type=heatmap)
    with x_data=[var_names] and y_data=[[r_matrix rows...]].

    For each off-diagonal variable pair, collects r values across participants,
    computes group mean r, SE, bootstrap 95% CI, and one-sample t-test vs 0.
    Outputs a group-level heatmap parquet (mean r) for plotting, plus a flat
    summary table (one row per pair) with inferential statistics.
    """
    for f in files:
        if not os.path.exists(f): log_error(f"File not found: {f}"); sys.exit(1)

    # Collect per-participant r matrices
    var_names = None
    participant_matrices = []   # list of (n x n) numpy arrays

    for f in files:
        df = pl.read_parquet(f)
        if df.height == 0:
            log_warning(f"Skipping empty file: {f}"); continue
        row = df.to_dicts()[0]
        x = row.get('x_data')
        y = row.get('y_data')
        if not isinstance(x, list) or not isinstance(y, list):
            log_warning(f"Unexpected format in {f}, skipping"); continue
        if var_names is None:
            var_names = x
        elif x != var_names:
            log_warning(f"Variable mismatch in {f} (expected {var_names}, got {x}), skipping")
            continue
        participant_matrices.append(np.array(y, dtype=float))

    if not participant_matrices or var_names is None:
        log_error("No valid heatmap files found"); sys.exit(1)

    n = len(var_names)
    n_part = len(participant_matrices)
    log_info(f"Consolidating {n_part} participants, {n} variables")

    # Group-level mean r matrix and summary rows
    rng = np.random.default_rng(42)
    mean_r_matrix = np.zeros((n, n))
    p_matrix = np.ones((n, n))
    summary_rows = []

    for i in range(n):
        mean_r_matrix[i][i] = 1.0
        p_matrix[i][i] = 0.0
        for j in range(i + 1, n):
            r_vals = np.array([m[i][j] for m in participant_matrices])
            mean_r = float(np.mean(r_vals))
            se_r   = float(np.std(r_vals, ddof=1) / np.sqrt(n_part)) if n_part > 1 else 0.0

            # Bootstrap 95% CI
            boot = np.array([
                np.mean(rng.choice(r_vals, size=n_part, replace=True))
                for _ in range(5000)
            ])
            ci_lo = float(np.percentile(boot, 2.5))
            ci_hi = float(np.percentile(boot, 97.5))

            # One-sample t-test vs 0
            if n_part > 1:
                _tt = ttest_1samp(r_vals, 0.0)
                t_stat: float = float(_tt.statistic)  # type: ignore[arg-type]
                p_val: float = float(_tt.pvalue)  # type: ignore[arg-type]
            else:
                t_stat = float('nan')
                p_val = float('nan')

            mean_r_matrix[i][j] = mean_r_matrix[j][i] = mean_r
            p_matrix[i][j] = p_matrix[j][i] = p_val

            summary_rows.append({
                'var_a':    var_names[i],
                'var_b':    var_names[j],
                'n_part':   n_part,
                'mean_r':   mean_r,
                'se_r':     se_r,
                'ci_lower': ci_lo,
                'ci_upper': ci_hi,
                't':        t_stat,
                'p':        p_val,
                'sig':      p_val < 0.05,
            })

    # Heatmap output (group mean r, p from t-test)
    heatmap_path = os.path.join(os.getcwd(), f"{out_base}_heatmap.parquet")
    pl.DataFrame([{
        'x_data':    var_names,
        'y_data':    mean_r_matrix.tolist(),
        'y_var':     p_matrix.tolist(),
        'plot_type': 'heatmap',
        'x_label':   'Measure',
        'y_label':   'Measure',
        'y_ticks':   None,
    }]).write_parquet(heatmap_path, compression='gzip')

    # Summary table output
    summary_path = os.path.join(os.getcwd(), f"{out_base}_summary.parquet")
    pl.DataFrame(summary_rows).write_parquet(summary_path, compression='gzip')

    log_info(f"Heatmap: {heatmap_path}")
    log_info(f"Summary: {summary_path}")
    print(heatmap_path)
    return heatmap_path


# ── Ratings-mode helpers ─────────────────────────────────────────────────────

def _is_plot_spec(df: pl.DataFrame) -> bool:
    return 'x_data' in df.columns and 'y_data' in df.columns


def _plot_spec_to_tidy(df: pl.DataFrame, metric_name: str | None = None) -> pl.DataFrame:
    """Convert a plot-spec parquet (bar type) to a tidy table with trial_id + metric columns."""
    rows = []
    for record in df.to_dicts():
        x_lst = record.get('x_data', [])
        y_lst = record.get('y_data', [])
        if not x_lst and not y_lst:
            continue
        if isinstance(x_lst, list) and isinstance(y_lst, list) and len(x_lst) == len(y_lst):
            for x_val, y_val in zip(x_lst, y_lst):
                try:
                    rows.append({'trial_id': str(x_val), metric_name or 'value': float(y_val)})
                except (TypeError, ValueError):
                    pass
        elif isinstance(y_lst, list) and len(y_lst) == 1:
            try:
                cond = record.get('condition', '')
                rows.append({'trial_id': cond, metric_name or 'value': float(y_lst[0])})
            except (TypeError, ValueError):
                pass
        elif not isinstance(y_lst, list):
            try:
                cond = record.get('condition', '')
                rows.append({'trial_id': cond, metric_name or 'value': float(y_lst)})
            except (TypeError, ValueError):
                pass
    return pl.DataFrame(rows) if rows else pl.DataFrame({'trial_id': [], metric_name or 'value': []})


def correlation_analyze(
    metrics_path: str,
    labels_path: str,
    rating_cols: list | None = None,
    metric_cols: list | None = None,
    method: str = 'spearman',
) -> str:
    """Correlate per-trial physiology metrics against continuous ratings.

    Accepts flat tidy tables or plot-spec parquets.  Resolves signal pointer
    files automatically.  Outputs a table plot-spec parquet.
    """
    log_info(f"Correlating {os.path.basename(metrics_path)} x {os.path.basename(labels_path)}")
    metrics_df = pl.read_parquet(metrics_path)
    labels_df  = pl.read_parquet(labels_path)

    # Resolve signal pointer (folder of per-condition epoch parquets)
    if 'signal' in metrics_df.columns and 'folder_path' in metrics_df.columns:
        folder = str(metrics_df['folder_path'][0])
        log_info(f"Detected signal file — resolving from folder: {folder}")
        data_files = sorted(_glob.glob(os.path.join(folder, "*.parquet")))
        if not data_files:
            log_warning(f"Signal folder is empty: {folder}")
        else:
            metrics_df = pl.concat([pl.read_parquet(f) for f in data_files], how='diagonal')
            log_info(f"Loaded {len(data_files)} files from signal folder ({len(metrics_df)} rows)")

    # Normalise plot-spec format to tidy
    if _is_plot_spec(metrics_df):
        log_info("Detected plot-spec format — converting to tidy table")
        base_metric = os.path.splitext(os.path.basename(metrics_path))[0]
        metrics_df = _plot_spec_to_tidy(metrics_df, base_metric)

    # Normalise id column
    if 'condition' in metrics_df.columns and 'trial_id' not in metrics_df.columns:
        metrics_df = metrics_df.rename({'condition': 'trial_id'})
    if 'condition' in labels_df.columns and 'trial_id' not in labels_df.columns:
        labels_df = labels_df.rename({'condition': 'trial_id'})

    labels_df = labels_df.unique(subset=['trial_id'])
    merged = metrics_df.join(labels_df, on='trial_id', how='inner')
    log_info(f"Joined {len(merged)} rows (metrics n={len(metrics_df)}, labels n={len(labels_df)})")
    if len(merged) == 0:
        log_warning("No matching trial_ids — check that conditions are named 'trial_NN'")

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

    corr_fn = _scipy_stats.spearmanr if method == 'spearman' else _scipy_stats.pearsonr
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
                _cr = corr_fn(x, y)
                r, p = float(_cr[0]), float(_cr[1])  # type: ignore[arg-type]
            row[f'{rc}_r']   = round(r, 4)
            row[f'{rc}_p']   = round(p, 4)
            row[f'{rc}_sig'] = '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else ''
        records.append(row)

    result_df = pl.DataFrame(records) if records else pl.DataFrame()
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
    }).write_parquet(out_path, compression='gzip')
    log_info(f"Output: {out_path}")
    print(out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[correl] Pairwise Pearson correlations as heatmap.')
        print('[correl] Usage: correl_analyzer.py <file1.parquet> [file2.parquet ...] [y_lim]')
        print('[correl]        correl_analyzer.py --consolidate-l2 <out_base> <file1.parquet> ...')
        print('[correl]        correl_analyzer.py <metrics.parquet> <labels.parquet> --correlate-ratings [rating_cols] [metric_cols] [method]')
        sys.exit(1)

    if a[1] == '--consolidate-l2':
        if len(a) < 4:
            log_error("--consolidate-l2 requires: <out_base> <file1.parquet> ...")
            sys.exit(1)
        out_base = a[2]
        files = [x for x in a[3:] if os.path.exists(x)]
        if not files:
            log_error("No valid parquet files provided"); sys.exit(1)
        consolidate_l2(files, out_base)
    elif '--correlate-ratings' in a:
        if len(a) < 3:
            log_error("--correlate-ratings requires: <metrics.parquet> <labels.parquet> --correlate-ratings [rating_cols] [metric_cols] [method]")
            sys.exit(1)
        idx   = a.index('--correlate-ratings')
        after = a[idx + 1:]
        r_cols = after[0].split(',') if after and after[0] not in ('None', '') else None
        m_cols = after[1].split(',') if len(after) > 1 and after[1] not in ('None', '') else None
        meth   = after[2] if len(after) > 2 and after[2] not in ('None', '') else 'spearman'
        correlation_analyze(a[1], a[2], r_cols, m_cols, meth)
    else:
        _TOKENS = {'terminal', 'group_log', 'result', 'table'}
        files, rest = [], []
        for arg in a[1:]:
            if arg in _TOKENS:
                continue
            (files if os.path.exists(arg) else rest).append(arg)
        y_lim = float(rest[0]) if rest and rest[0].lower() != 'none' else None
        correl_analyze(files, y_lim)
