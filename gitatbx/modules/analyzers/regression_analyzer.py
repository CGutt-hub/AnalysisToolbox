#!/usr/bin/env python3
"""Regression Analyzer Module - Framework-aligned execution matching NATIVE_MODULE conventions."""
import polars as pl, sys, os, pandas as pd, numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

def log_info(msg: str) -> None:  print(f"[regression] INFO: {msg}")
def log_error(msg: str) -> None: print(f"[regression] ERROR: {msg}", file=sys.stderr)

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def regression_analyze(ip: str, target_col: str, group_col: str, alpha: float) -> str:
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

    if not target_col or target_col not in df.columns:
        log_error(f"Required target column '{target_col}' missing from dataset columns: {list(df.columns)}")
        sys.exit(1)

    # Attempt dynamic numeric conversion for categorical/string target columns (e.g., y_true)
    if not pd.api.types.is_numeric_dtype(df[target_col]):
        df[target_col] = pd.to_numeric(df[target_col], errors='coerce')

    if not pd.api.types.is_numeric_dtype(df[target_col]):
        log_error(f"Target column '{target_col}' must be numeric or coercible to float.")
        sys.exit(1)

    if df[target_col].isna().any():
        log_error(f"NaN values detected in target column '{target_col}'. Imputation disabled.")
        sys.exit(1)

    non_feature_cols = {target_col, group_col, 'id', 'participant_id', 'subject', 'condition', 'plot_type', 'level', 'level_tag'}
    feature_cols = [c for c in df.columns if c not in non_feature_cols and pd.api.types.is_numeric_dtype(df[c])]

    if not feature_cols:
        log_error(f"No valid numeric feature columns found to predict target '{target_col}'.")
        sys.exit(1)

    if df[feature_cols].isna().any().any():
        log_error("NaN values detected in feature columns. Imputation disabled.")
        sys.exit(1)

    X = df[feature_cols]
    y = df[target_col]

    if len(df) <= len(feature_cols):
        log_error(f"Insufficient samples ({len(df)}) for feature count ({len(feature_cols)}) in regression fit.")
        sys.exit(1)

    try:
        model = Ridge(alpha=alpha)
        model.fit(X, y)
        predictions = model.predict(X)
    except Exception as e:
        log_error(f"Ridge regression fit calculation failed: {e}")
        sys.exit(1)

    mse = float(mean_squared_error(y, predictions))
    rmse = float(np.sqrt(mse))
    mae = float(mean_absolute_error(y, predictions))
    r2 = float(r2_score(y, predictions))

    row: dict[str, float | str | int] = {
        'target': target_col,
        'n_samples': len(df),
        'n_features': len(feature_cols),
        'rmse': rmse,
        'mae': mae,
        'r2': r2,
        'features_used': ",".join(feature_cols),
        'intercept': float(model.intercept_)
    }

    for idx, col in enumerate(feature_cols):
        row[f'coef_{col}'] = float(model.coef_[idx])

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_regression_{target_col}.parquet")
    pl.DataFrame([row]).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log_error("CRITICAL: Minimum parameters required: <input.parquet> <target_col> [group_col] [alpha]")
        sys.exit(1)

    ip = sys.argv[1]
    target_col = sys.argv[2].strip(" '\"\\")
    
    group_col = "id"
    if len(sys.argv) >= 4:
        group_col = sys.argv[3].strip(" '\"\\")

    alpha = 1.0
    if len(sys.argv) >= 5:
        try:
            alpha = float(sys.argv[4].strip(" '\"\\"))
        except ValueError:
            log_error(f"Alpha parameter must be a valid float. Received: '{sys.argv[4]}'")
            sys.exit(1)

    regression_analyze(ip, target_col=target_col, group_col=group_col, alpha=alpha)