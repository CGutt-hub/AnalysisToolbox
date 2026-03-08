"""Correlation Analyzer - Compute pairwise Pearson correlations between numeric columns."""
import polars as pl, sys, os
from scipy.stats import pearsonr

# Logging helpers
def log_info(msg): print(f"[correl] INFO: {msg}")
def log_warning(msg): print(f"[correl] WARNING: {msg}")
def log_error(msg): print(f"[correl] ERROR: {msg}")

def correl_analyze(ip: str, y_lim: float | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    print(f"[correl] Correlation analysis: {ip}")
    df = pl.read_parquet(ip)
    num_cols = df.select(pl.NUMERIC_DTYPES).columns
    if len(num_cols) < 2: log_error("Need at least 2 numeric columns"); sys.exit(1)
    results = pl.DataFrame([{
        'var1': c1, 'var2': c2, 'correlation': pearsonr(df[c1].to_numpy(), df[c2].to_numpy())[0],
        'p': pearsonr(df[c1].to_numpy(), df[c2].to_numpy())[1], 'plot_type': 'scatter',
        'x_scale': 'nominal', 'y_scale': 'nominal', 'x_data': f"{c1}_vs_{c2}",
        'y_data': pearsonr(df[c1].to_numpy(), df[c2].to_numpy())[0], 'y_label': 'Correlation (r)',
        'y_ticks': y_lim, 'plot_weight': 1
    } for i, c1 in enumerate(num_cols) for c2 in num_cols[i+1:]])
    out_file = f"{os.path.splitext(os.path.basename(ip))[0]}_correl.parquet"
    results.write_parquet(out_file, compression='snappy')
    # Create procedure visualization: single-row bar chart (all pairs × r values)
    # The interactive plotter needs: plot_type, x_data (list), y_data (list), y_var (list), x_label, y_label
    pairs_list   = results['x_data'].to_list()
    r_list       = results['y_data'].to_list()
    vis_df = pl.DataFrame({
        'plot_type': ['bar'],
        'x_data':    [pairs_list],
        'y_data':    [r_list],
        'y_var':     [[0.0] * len(r_list)],
        'x_label':   ['Signal Pair'],
        'y_label':   ['Pearson r'],
        'title':     ['Cross-Modal Correlations'],
    })
    vis_file = out_file.replace('.parquet', '_vis.parquet')
    vis_df.write_parquet(vis_file, compression='snappy')
    print(f"[correl] Output: {out_file} ({len(results)} pairs)")
    print(f"[correl] Created procedure visualization: {vis_file}")
    return out_file

if __name__ == '__main__': (lambda a: correl_analyze(a[1], float(a[2]) if len(a) > 2 and a[2] else None) if len(a) >= 2 else (print('[correl] Compute pairwise Pearson correlations between numeric columns. Plot-ready output.\nUsage: correl_analyzer.py <input.parquet> [y_lim]'), sys.exit(1)))(sys.argv)