"""Classification Analyzer - Generic Machine Learning Module (Strict fail-fast implementation)."""
import sys, os, polars as pl, numpy as np
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.ensemble import RandomForestClassifier

def log_info(msg: str) -> None:  print(f"[classifier] INFO: {msg}")
def log_error(msg: str) -> None: print(f"[classifier] ERROR: {msg}")

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def run_classification(ip: str, target_col: str, group_col: str, feature_cols: list[str]) -> str:
    log_info(f"Running classification execution: file={ip}, target={target_col}, group={group_col}")

    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    try:
        df = pl.read_parquet(ip)
        df = ensure_flat_dataframe(df)
    except Exception as e:
        log_error(f"Failed to read parquet dataset: {e}")
        sys.exit(1)

    if df.height == 0:
        log_error("Input dataset contains zero rows.")
        sys.exit(1)

    if target_col not in df.columns:
        log_error(f"Target label column '{target_col}' missing from columns: {list(df.columns)}")
        sys.exit(1)

    if group_col not in df.columns:
        log_error(f"Required group column '{group_col}' missing from dataset columns: {list(df.columns)}")
        sys.exit(1)

    if not feature_cols:
        log_error("Feature columns (feature_cols) must be explicitly specified.")
        sys.exit(1)

    missing_feats = [c for c in feature_cols if c not in df.columns]
    if missing_feats:
        log_error(f"Declared feature columns missing from dataset: {missing_feats}")
        sys.exit(1)

    # STRICT CHECK: FAIL FAST ON MISSING DATA. NO SILENT IMPUTATION (fill_null(0)) PERMITTED!
    null_counts = df.select(feature_cols).null_count()
    total_nulls = sum(null_counts.row(0))
    if total_nulls > 0:
        log_error(f"CRITICAL: Dataset contains {total_nulls} null/missing feature values. Silent imputation (fill_null) is disabled.")
        sys.exit(1)

    if df[target_col].null_count() > 0:
        log_error(f"CRITICAL: Target column '{target_col}' contains null values.")
        sys.exit(1)

    X = df.select(feature_cols).to_numpy()
    y = df[target_col].to_numpy()
    groups = df[group_col].to_numpy()

    if len(set(groups)) < 2:
        log_error(f"Classification requires at least 2 distinct groups. Found: {len(set(groups))}")
        sys.exit(1)

    logo = LeaveOneGroupOut()
    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    prediction_records = []

    for train_idx, test_idx in logo.split(X, y, groups):
        clf.fit(X[train_idx], y[train_idx])
        preds = clf.predict(X[test_idx])
        for idx, pred, true_val in zip(test_idx, preds, y[test_idx]):
            prediction_records.append({
                'group': str(groups[idx]),
                'target_label': str(target_col),
                'y_true': str(true_val),
                'y_pred': str(pred),
                'is_correct': int(pred == true_val)
            })

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_classified_{target_col}.parquet")
    pl.DataFrame(prediction_records).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) != 5:
        log_error("CRITICAL: Exact parameters required: <input.parquet> <target_col> <group_col> <feature_cols_comma_str>")
        sys.exit(1)

    ip = sys.argv[1]
    t_col = sys.argv[2].strip(" '\"\\")
    g_col = sys.argv[3].strip(" '\"\\")
    f_cols = [c.strip(" '\"\\") for c in sys.argv[4].split(',') if c.strip(" '\"\\")]

    run_classification(ip, target_col=t_col, group_col=g_col, feature_cols=f_cols)