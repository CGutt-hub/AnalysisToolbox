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
"""
import polars as pl, polars.selectors as cs, sys, os, numpy as np
from scipy.stats import pearsonr, ttest_1samp

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
                r, p = pearsonr(a[:mn], b[:mn])
                r_matrix[i][j] = r_matrix[j][i] = float(r)
                p_matrix[i][j] = p_matrix[j][i] = float(p)

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
    }]).write_parquet(out_file, compression='snappy')
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
                t_stat, p_val = ttest_1samp(r_vals, 0.0)
            else:
                t_stat, p_val = float('nan'), float('nan')

            mean_r_matrix[i][j] = mean_r_matrix[j][i] = mean_r
            p_matrix[i][j] = p_matrix[j][i] = float(p_val)

            summary_rows.append({
                'var_a':    var_names[i],
                'var_b':    var_names[j],
                'n_part':   n_part,
                'mean_r':   mean_r,
                'se_r':     se_r,
                'ci_lower': ci_lo,
                'ci_upper': ci_hi,
                't':        float(t_stat),
                'p':        float(p_val),
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
    }]).write_parquet(heatmap_path, compression='snappy')

    # Summary table output
    summary_path = os.path.join(os.getcwd(), f"{out_base}_summary.parquet")
    pl.DataFrame(summary_rows).write_parquet(summary_path, compression='snappy')

    log_info(f"Heatmap: {heatmap_path}")
    log_info(f"Summary: {summary_path}")
    print(heatmap_path)
    return heatmap_path


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[correl] Pairwise Pearson correlations as heatmap.')
        print('[correl] Usage: correl_analyzer.py <file1.parquet> [file2.parquet ...] [y_lim]')
        print('[correl]        correl_analyzer.py --consolidate-l2 <out_base> <file1.parquet> ...')
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
    else:
        files, rest = [], []
        for arg in a[1:]:
            (files if os.path.exists(arg) else rest).append(arg)
        y_lim = float(rest[0]) if rest and rest[0].lower() != 'none' else None
        correl_analyze(files, y_lim)
