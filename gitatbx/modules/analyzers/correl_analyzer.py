"""Correlation Analyzer - Compute pairwise Pearson correlations, output as heatmap.

Single-file mode:  all numeric columns in the file are correlated directly.
Multi-file mode:   each file is aggregated to condition-level means, columns are
                   renamed to avoid clashes (using the file stem), then all tables
                   are joined on 'condition' before correlating.  Useful for
                   cross-modal correlations without a dedicated join processor.
"""
import polars as pl, polars.selectors as cs, sys, os
from scipy.stats import pearsonr

def log_info(msg): print(f"[correl] INFO: {msg}")
def log_warning(msg): print(f"[correl] WARNING: {msg}")
def log_error(msg): print(f"[correl] ERROR: {msg}")

_EXCLUDE = {'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id', 'condition', 'region'}


def _build_df(files: list[str]) -> pl.DataFrame:
    """Return a wide correlation-ready DataFrame from one or more input parquets."""
    if len(files) == 1:
        return pl.read_parquet(files[0])

    # Multi-file: aggregate each to condition-level means, rename cols, join
    agg_tables = []
    for f in files:
        df = pl.read_parquet(f)
        stem = os.path.splitext(os.path.basename(f))[0]   # e.g. "EV_003_hrv_interv"
        # Shorten stem to the last meaningful segment (after the participant prefix)
        parts = stem.split('_')
        label = '_'.join(parts[2:]) if len(parts) > 2 else stem   # drop "EV_003"

        num_cols = [c for c in df.select(cs.numeric()).columns if c not in _EXCLUDE]
        agg = df.group_by('condition').agg([pl.col(c).mean() for c in num_cols])

        # Rename numeric cols to avoid clashes across files
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


def correl_analyze(files: list[str], y_lim: float | None = None) -> str:
    for f in files:
        if not os.path.exists(f): log_error(f"File not found: {f}"); sys.exit(1)

    df = _build_df(files)
    num_cols = [c for c in df.select(cs.numeric()).columns if c not in _EXCLUDE]
    if len(num_cols) < 2: log_error("Need at least 2 numeric columns"); sys.exit(1)
    if df.height < 2: log_warning(f"Too few rows ({df.height}) — skipping"); sys.exit(0)

    n = len(num_cols)
    r_matrix = [[0.0] * n for _ in range(n)]
    p_matrix = [[1.0] * n for _ in range(n)]
    for i, c1 in enumerate(num_cols):
        for j, c2 in enumerate(num_cols):
            if i == j:
                r_matrix[i][j] = 1.0; p_matrix[i][j] = 0.0
            elif i < j:
                r, p = pearsonr(df[c1].drop_nulls().to_numpy(), df[c2].drop_nulls().to_numpy())
                r_matrix[i][j] = r_matrix[j][i] = float(r)
                p_matrix[i][j] = p_matrix[j][i] = float(p)

    base = os.path.splitext(os.path.basename(files[0]))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_correl.parquet")
    pl.DataFrame([{
        'x_data': num_cols,
        'y_data': r_matrix,
        'y_var':  p_matrix,
        'plot_type': 'heatmap',
        'x_label': 'Measure',
        'y_label': 'Measure',
        'y_ticks': y_lim,
    }]).write_parquet(out_file, compression='snappy')
    log_info(f"Output: {out_file} ({n} variables)")
    print(out_file)
    return out_file

if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[correl] Pairwise Pearson correlations as heatmap.')
        print('[correl] Usage: correl_analyzer.py <file1.parquet> [file2.parquet ...] [y_lim]')
        sys.exit(1)
    # Collect file args (paths that exist); last non-file arg is optional y_lim
    files, rest = [], []
    for arg in a[1:]:
        (files if os.path.exists(arg) else rest).append(arg)
    y_lim = float(rest[0]) if rest and rest[0].lower() != 'none' else None
    correl_analyze(files, y_lim)