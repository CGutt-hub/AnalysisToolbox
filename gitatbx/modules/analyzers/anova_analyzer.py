"""ANOVA Analyzer Module - Generic One-way ANOVA execution (Strict fail-fast implementation)."""
import polars as pl, sys, os, pandas as pd, numpy as np, ast
from scipy.stats import f_oneway
from statsmodels.stats.multitest import fdrcorrection

def log_info(msg: str) -> None:  print(f"[anova] INFO: {msg}")
def log_error(msg: str) -> None: print(f"[anova] ERROR: {msg}")

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    """Explodes nested list columns (from NATIVE_CONCAT) into flat observation rows."""
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def _sig(p: float) -> str:
    if pd.isna(p): return 'ns'
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'

def anova_analyze(ip: str, dv: list[str], between: str, apply_fdr: bool = False) -> str:
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
        log_error(f"Required grouping factor column '{between}' is missing from dataset columns: {list(df.columns)}")
        sys.exit(1)

    if df[between].nunique() < 2:
        log_error(f"Grouping factor '{between}' must contain at least 2 distinct levels. Found: {df[between].nunique()}")
        sys.exit(1)

    if not dv:
        log_error("Dependent variables (dv) parameter must be explicitly specified.")
        sys.exit(1)

    missing_dvs = [c for c in dv if c not in df.columns]
    if missing_dvs:
        log_error(f"Declared dependent variables not found in dataset: {missing_dvs}. Available: {list(df.columns)}")
        sys.exit(1)

    rows: list[dict[str, float | str | int]] = []
    p_vals_raw: list[float] = []

    for col in dv:
        group_vals: list[np.ndarray] = [
            sub[col].dropna().to_numpy(dtype=np.float64) 
            for _, sub in df.groupby(between) 
            if len(sub[col].dropna()) > 0
        ]
        
        if len(group_vals) < 2 or all(len(g) <= 1 for g in group_vals):
            log_error(f"Dependent variable '{col}' has fewer than 2 valid sample groups for factor '{between}'.")
            sys.exit(1)

        f_stat, p_stat = f_oneway(*group_vals)
        
        all_vals = np.hstack(group_vals)
        grand_mean = float(np.mean(all_vals))
        ss_between = float(sum(len(g) * float((np.mean(g) - grand_mean) ** 2) for g in group_vals))
        ss_total = float(sum(float(np.sum((g - grand_mean) ** 2)) for g in group_vals))
        eta_sq = float(ss_between / ss_total) if ss_total > 0 else 0.0

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

    if apply_fdr and len([p for p in p_vals_raw if not pd.isna(p)]) > 1:
        valid_indices = [i for i, p in enumerate(p_vals_raw) if not pd.isna(p)]
        _, p_corrected = fdrcorrection([p_vals_raw[i] for i in valid_indices])
        for idx, corr_p in zip(valid_indices, p_corrected):
            rows[idx]['p'] = float(corr_p)
            rows[idx]['sig'] = _sig(float(corr_p))

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_anova.parquet")
    pl.DataFrame(rows).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) < 4:
        log_error("Usage: python anova_analyzer.py <input.parquet> <dv_list_or_comma_str> <between_col> [apply_fdr]")
        sys.exit(1)

    ip = sys.argv[1]
    raw_dv = sys.argv[2]
    dv_list = ast.literal_eval(raw_dv) if raw_dv.startswith('[') else [c.strip() for c in raw_dv.split(',') if c.strip()]
    between_col = sys.argv[3]
    apply_fdr = len(sys.argv) > 4 and sys.argv[4].lower() in ('1', 'true', 'yes', 'fdr')

    anova_analyze(ip, dv=dv_list, between=between_col, apply_fdr=apply_fdr)