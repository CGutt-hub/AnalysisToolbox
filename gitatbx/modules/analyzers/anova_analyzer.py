"""ANOVA Analyzer - One-way ANOVA per DV (or auto over all numeric cols) with optional FDR correction."""
import polars as pl, sys, os, numpy as np
from scipy.stats import f_oneway
from statsmodels.stats.multitest import fdrcorrection

def log_info(msg): print(f"[anova] INFO: {msg}")
def log_warning(msg): print(f"[anova] WARNING: {msg}")
def log_error(msg): print(f"[anova] ERROR: {msg}")

def _sig(p: float) -> str:
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'

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

    rows = []
    p_vals_raw = []
    for col in dv_cols:
        try:
            group_vals = [df.loc[df[between] == cond, col].dropna().values
                          for cond in df[between].dropna().unique()]
            if len(group_vals) < 2 or any(len(g) == 0 for g in group_vals):
                log_warning(f"Skipping {col}: insufficient groups")
                continue
            F, p = f_oneway(*group_vals)
            k  = len(group_vals)
            N  = sum(len(g) for g in group_vals)
            df_between = k - 1
            df_within  = N - k
            grand_mean = np.concatenate(group_vals).mean()
            ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in group_vals)
            ss_total   = sum(((g - grand_mean) ** 2).sum() for g in group_vals)
            eta_sq = float(ss_between / ss_total) if ss_total > 0 else 0.0
            rows.append({'dv': col, 'F': float(F), 'df1': df_between, 'df2': df_within,
                         'p': float(p), 'eta_sq': eta_sq})
            p_vals_raw.append(float(p))
        except Exception as e:
            log_warning(f"ANOVA failed for {col}: {e}")

    if not rows:
        log_error("All ANOVA runs failed"); sys.exit(1)

    if apply_fdr and len(p_vals_raw) > 1:
        _, p_corrected = fdrcorrection(p_vals_raw)
        for i, row in enumerate(rows):
            row['p'] = float(p_corrected[i])

    for row in rows:
        row['sig'] = _sig(row['p'])

    # Store as table spec: x_data = column headers, y_data = column data (transposed)
    col_names  = ['DV', 'F', 'df1', 'df2', 'p', 'η²', 'sig']
    col_data   = [
        [r['dv']                    for r in rows],
        [round(r['F'],    3)        for r in rows],
        [r['df1']                   for r in rows],
        [r['df2']                   for r in rows],
        [round(r['p'],    4)        for r in rows],
        [round(r['eta_sq'], 3)      for r in rows],
        [r['sig']                   for r in rows],
    ]
    p_label = f"p (FDR-corrected)" if apply_fdr else "p"

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_anova.parquet")
    pl.DataFrame([{
        'x_data':    col_names,
        'y_data':    col_data,
        'y_var':     None,
        'plot_type': 'table',
        'x_label':   between,
        'y_label':   p_label,
        'y_ticks':   y_lim,
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