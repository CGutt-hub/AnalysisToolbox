"""ANOVA Analyzer Module - Generic One-way ANOVA execution (Strict fail-fast implementation)."""
import polars as pl, sys, os, pandas as pd, numpy as np
from scipy.stats import f_oneway
from statsmodels.stats.multitest import fdrcorrection

def log_info(msg: str) -> None:  print(f"[anova] INFO: {msg}")
def log_error(msg: str) -> None: print(f"[anova] ERROR: {msg}")

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def _sig(p: float) -> str:
    if pd.isna(p) or np.isnan(p):
        log_error("ANOVA calculated NaN p-value. Check group variance.")
        sys.exit(1)
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'

def anova_analyze(ip: str, dv: list[str], between: str, apply_fdr: bool) -> str:
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12: 
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    try:
        pl_df = pl.read_parquet(ip)
        pl_df = ensure_flat_dataframe(pl_df)
        df: pd.DataFrame = pl_df.to_pandas()
    except Exception as e:
        log_error(f"Failed to read parquet dataset: {e}")
        sys.exit(1)

    if df.empty:
        log_error(f"Input dataframe is empty: {ip}")
        sys.exit(1)

    if not between or between not in df.columns:
        log_error(f"Required grouping factor column '{between}' missing from dataset columns: {list(df.columns)}")
        sys.exit(1)

    if df[between].nunique() < 2:
        log_error(f"Grouping factor '{between}' must contain at least 2 distinct levels. Found: {df[between].nunique()}")
        sys.exit(1)

    if not dv:
        log_error("Dependent variables (dv) parameter must be explicitly specified.")
        sys.exit(1)

    missing_dvs = [c for c in dv if c not in df.columns]
    if missing_dvs:
        log_error(f"Declared dependent variables not found in dataset: {missing_dvs}")
        sys.exit(1)

    rows: list[dict[str, float | str | int]] = []
    p_vals_raw: list[float] = []

    for col in dv:
        if df[col].isna().any():
            log_error(f"NaN values detected in dependent variable column '{col}'. Imputation disabled.")
            sys.exit(1)

        group_vals: list[np.ndarray] = [
            sub[col].to_numpy(dtype=np.float64) 
            for _, sub in df.groupby(between)
        ]
        
        if len(group_vals) < 2 or any(len(g) <= 1 for g in group_vals):
            log_error(f"Dependent variable '{col}' has insufficient samples per factor level '{between}'.")
            sys.exit(1)

        f_stat, p_stat = f_oneway(*group_vals)
        if np.isnan(f_stat) or np.isnan(p_stat):
            log_error(f"f_oneway calculation returned NaN for variable '{col}'. Check for zero variance across groups.")
            sys.exit(1)
        
        all_vals = np.hstack(group_vals)
        grand_mean = float(np.mean(all_vals))
        ss_between = float(sum(len(g) * float((np.mean(g) - grand_mean) ** 2) for g in group_vals))
        ss_total = float(sum(float(np.sum((g - grand_mean) ** 2)) for g in group_vals))
        
        if ss_total <= 0:
            log_error(f"Total Sum of Squares is zero for variable '{col}'. Zero variance present across dataset.")
            sys.exit(1)

        eta_sq = float(ss_between / ss_total)

        rows.append({
            'dv': col,
            'F': float(f_stat),
            'df1': len(group_vals) - 1,
            'df2': sum(len(g) for g in group_vals) - len(group_vals), 
            'p': float(p_stat),
            'eta_sq': eta_sq,
            'between_factor': between,
            'sig': _sig(float(p_stat))
        })
        p_vals_raw.append(float(p_stat))

    if apply_fdr:
        if len(p_vals_raw) < 2:
            log_error("FDR correction requested, but fewer than 2 p-values exist.")
            sys.exit(1)
        _, p_corrected = fdrcorrection(p_vals_raw)
        for idx, corr_p in enumerate(p_corrected):
            rows[idx]['p'] = float(corr_p)
            rows[idx]['sig'] = _sig(float(corr_p))

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_anova.parquet")
    pl.DataFrame(rows).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) != 5:
        log_error("CRITICAL: Exact parameters required: <input.parquet> <dv_comma_str> <between_col> <apply_fdr_boolean>")
        sys.exit(1)

    ip = sys.argv[1]
    dv_list = [c.strip(" '\"\\") for c in sys.argv[2].split(',') if c.strip(" '\"\\")]
    between_col = sys.argv[3].strip(" '\"\\")
    fdr_str = sys.argv[4].strip(" '\"\\").lower()
    
    if fdr_str not in ('true', 'false', '1', '0'):
        log_error(f"apply_fdr parameter must be explicit boolean ('true' or 'false'). Received: '{fdr_str}'")
        sys.exit(1)
    apply_fdr = fdr_str in ('true', '1')

    anova_analyze(ip, dv=dv_list, between=between_col, apply_fdr=apply_fdr)