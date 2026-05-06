"""Correlation Analyzer - Compute pairwise Pearson correlations, output as heatmap."""
import polars as pl, polars.selectors as cs, sys, os
from scipy.stats import pearsonr

def log_info(msg): print(f"[correl] INFO: {msg}")
def log_warning(msg): print(f"[correl] WARNING: {msg}")
def log_error(msg): print(f"[correl] ERROR: {msg}")

def correl_analyze(ip: str, y_lim: float | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    df = pl.read_parquet(ip)
    exclude = {'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id', 'condition', 'region'}
    num_cols = [c for c in df.select(cs.numeric()).columns if c not in exclude]
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

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_correl.parquet")
    pl.DataFrame([{
        'x_data': num_cols,    # variable names for both axes
        'y_data': r_matrix,    # 2D list of r values
        'y_var':  p_matrix,    # 2D list of p-values
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
    if len(a) >= 2:
        correl_analyze(a[1], float(a[2]) if len(a) > 2 and a[2].lower() != 'none' else None)
    else:
        print('[correl] Pairwise Pearson correlations as heatmap.\nUsage: correl_analyzer.py <input.parquet> [y_lim]')
        sys.exit(1)