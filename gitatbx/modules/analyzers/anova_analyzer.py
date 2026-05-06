"""ANOVA Analyzer - One-way ANOVA per DV (or auto over all numeric cols) with optional FDR correction."""
import polars as pl, sys, os
from scipy.stats import f_oneway
from statsmodels.stats.multitest import fdrcorrection

def log_info(msg): print(f"[anova] INFO: {msg}")
def log_warning(msg): print(f"[anova] WARNING: {msg}")
def log_error(msg): print(f"[anova] ERROR: {msg}")

def anova_analyze(ip: str, dv: str, between: str, apply_fdr: bool = False, y_lim: float | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    df = pl.read_parquet(ip).to_pandas()

    meta_cols = {between, 'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id', 'condition', 'region'}
    if dv.lower() == 'auto':
        dv_cols = [c for c in df.select_dtypes(include='number').columns if c not in meta_cols]
    else:
        dv_cols = [dv]

    if not dv_cols:
        log_error("No numeric DV columns found"); sys.exit(1)
    log_info(f"ANOVA: {ip}, DVs={dv_cols}, between={between}, fdr={apply_fdr}")

    f_stats, p_vals, dv_names = [], [], []
    for col in dv_cols:
        try:
            groups = [df.loc[df[between] == cond, col].dropna().values
                      for cond in df[between].dropna().unique()]
            if len(groups) < 2 or any(len(g) == 0 for g in groups):
                log_warning(f"Skipping {col}: insufficient groups")
                continue
            F, p = f_oneway(*groups)
            f_stats.append(float(F))
            p_vals.append(float(p))
            dv_names.append(col)
        except Exception as e:
            log_warning(f"ANOVA failed for {col}: {e}")

    if not dv_names:
        log_error("All ANOVA runs failed"); sys.exit(1)

    if apply_fdr and len(p_vals) > 1:
        _, p_vals = fdrcorrection(p_vals)
        p_vals = p_vals.tolist()

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_anova.parquet")
    pl.DataFrame([{
        'x_data': dv_names,
        'y_data': f_stats,
        'y_var': p_vals,
        'plot_type': 'bar',
        'x_label': 'Measure',
        'y_label': 'F-statistic',
        'y_ticks': y_lim,
        'between': between,
    }]).write_parquet(out_file, compression='snappy')
    print(f"[anova] Output: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 4:
        anova_analyze(a[1], a[2], a[3],
                      len(a) > 4 and a[4].lower() in ['1', 'true', 'yes'],
                      float(a[5]) if len(a) > 5 and a[5] and a[5].lower() != 'none' else None)
    else:
        print('[anova] One-way ANOVA per DV. Usage: anova_analyzer.py <input.parquet> <dv|auto> <between> [apply_fdr=false] [y_lim]')
        sys.exit(1)