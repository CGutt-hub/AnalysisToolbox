"""Correlation Analyzer Module - Generic feature matrix correlation (Strict fail-fast implementation)."""
import numpy as np, pandas as pd, sys, os, ast, polars as pl
from typing import Literal

CorrMethod = Literal['pearson', 'kendall', 'spearman']

def log_error(msg: str): print(f"[correlation] ERROR: {msg}")
def log_info(msg: str):  print(f"[correlation] INFO: {msg}")

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    """Explodes nested list columns (from NATIVE_CONCAT) into flat observation rows."""
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def compute_correlation_matrix(ip: str, target_cols: list[str], method: CorrMethod = 'pearson') -> str:
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    if method not in ('pearson', 'kendall', 'spearman'):
        log_error(f"Unsupported correlation method '{method}'. Must be one of: pearson, kendall, spearman.")
        sys.exit(1)

    try:
        pl_df = pl.read_parquet(ip)
        pl_df = ensure_flat_dataframe(pl_df)
        df = pl_df.to_pandas()
    except Exception as e:
        log_error(f"Failed to read parquet file: {e}")
        sys.exit(1)

    if not target_cols or len(target_cols) < 2:
        log_error("At least 2 target feature columns must be explicitly declared for correlation analysis.")
        sys.exit(1)

    missing_cols = [c for c in target_cols if c not in df.columns]
    if missing_cols:
        log_error(f"Declared feature columns missing from dataset: {missing_cols}. Available columns: {list(df.columns)}")
        sys.exit(1)

    numeric_df = df[target_cols].select_dtypes(include=[np.number])
    if numeric_df.shape[1] < 2:
        log_error(f"Fewer than 2 numeric feature columns available after numeric filtering. Found {numeric_df.shape[1]}.")
        sys.exit(1)

    corr_matrix = numeric_df.corr(method=method)
    
    if corr_matrix.isna().all().all():
        log_error("Computed correlation matrix contains only NaN values. Check for zero variance in input features.")
        sys.exit(1)

    corr_df = corr_matrix.reset_index().rename(columns={'index': 'feature'})

    base = os.path.splitext(os.path.basename(ip))[0]
    out_path = os.path.join(os.getcwd(), f"{base}_correlation.parquet")
    pl.from_pandas(corr_df).write_parquet(out_path, compression='gzip')
    
    log_info(f"Output generated: {out_path}")
    print(out_path)
    return out_path

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log_error("Usage: python correlation_analyzer.py <input.parquet> <target_cols_list_or_comma_str> [method]")
        sys.exit(1)

    ip_file = sys.argv[1]
    
    raw_targets = sys.argv[2]
    if raw_targets.startswith('[') and raw_targets.endswith(']'):
        t_cols = ast.literal_eval(raw_targets)
    else:
        t_cols = [c.strip() for c in raw_targets.split(',') if c.strip()]

    raw_method = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] not in ('None', '') else 'pearson'
    if raw_method not in ('pearson', 'kendall', 'spearman'):
        log_error(f"Invalid correlation method '{raw_method}'. Expected 'pearson', 'kendall', or 'spearman'.")
        sys.exit(1)

    compute_correlation_matrix(ip_file, target_cols=t_cols, method=raw_method)