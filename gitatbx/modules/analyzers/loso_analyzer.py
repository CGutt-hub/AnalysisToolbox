#!/usr/bin/env python3
"""
Atomic Analyzer: Evaluates Leave-One-Subject-Out (LOSO) classification metrics 
and computes group-aware permutation p-values in a single unified workflow.
Saves consolidated parquet with significance notation and writes destination file path to stdout.
"""
import sys
import os
import polars as pl
import numpy as np
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, precision_recall_fscore_support
from joblib import Parallel, delayed


def log_info(msg: str) -> None:
    print(f"[loso_analyzer] INFO: {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"[loso_analyzer] ERROR: {msg}", file=sys.stderr)


def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df


def get_significance_stars(p_value: float) -> str:
    """Translates p-value into standard scientific/APA star notation."""
    if p_value < 0.001:
        return "***"
    elif p_value < 0.01:
        return "**"
    elif p_value < 0.05:
        return "*"
    elif p_value < 0.1:
        return "."
    else:
        return "n.s."


def _evaluate_perm(X: np.ndarray, y_perm: np.ndarray, groups: np.ndarray) -> float:
    logo = LeaveOneGroupOut()
    preds = np.empty_like(y_perm, dtype=object)
    for train_idx, test_idx in logo.split(X, y_perm, groups):
        clf = RandomForestClassifier(n_estimators=30, n_jobs=1, random_state=42)
        clf.fit(X[train_idx], y_perm[train_idx])
        preds[test_idx] = clf.predict(X[test_idx])
    return float(balanced_accuracy_score(y_perm, preds))


def main() -> None:
    if len(sys.argv) < 4:
        log_error("CRITICAL Usage: <input.parquet> <target_cols_comma_str> <group_col> <feature_cols_comma_str> [n_perms]")
        sys.exit(1)

    ip = sys.argv[1]
    target_cols = [c.strip(" '\"\\[]") for c in sys.argv[2].split(',') if c.strip(" '\"\\[]")]
    group_col = sys.argv[3].strip(" '\"\\[]")
    feature_cols = [c.strip(" '\"\\[]") for c in sys.argv[4].split(',') if c.strip(" '\"\\[]")]
    n_perms = int(sys.argv[5]) if len(sys.argv) > 5 else 1000

    log_info(f"Running unified LOSO classification & permutation test (n={n_perms}) on: {ip}")

    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"CRITICAL: Input file missing or empty: {ip}")
        sys.exit(1)

    try:
        raw_df = pl.read_parquet(ip)
        raw_df = ensure_flat_dataframe(raw_df)
    except Exception as e:
        log_error(f"CRITICAL: Parquet parsing failure: {e}")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    summary_rows = []
    rng = np.random.default_rng(42)

    for target_col in target_cols:
        if target_col not in raw_df.columns or group_col not in raw_df.columns:
            log_error(f"CRITICAL: Column '{target_col}' or '{group_col}' missing from dataset.")
            sys.exit(1)

        missing_features = [f for f in feature_cols if f not in raw_df.columns]
        if missing_features:
            log_error(f"CRITICAL: Feature columns missing: {missing_features}")
            sys.exit(1)

        sub_df = raw_df.drop_nulls(subset=feature_cols + [target_col, group_col])
        X = sub_df.select(feature_cols).to_numpy()
        y_raw = sub_df[target_col].to_numpy()
        groups = sub_df[group_col].to_numpy()

        unique_groups = np.unique(groups)
        if len(unique_groups) < 2:
            log_error(f"CRITICAL: Insufficient groups ({len(unique_groups)}) for LOSO evaluation on '{target_col}'.")
            sys.exit(1)

        # Retain original discrete condition categories/IDs without forcing median split
        y = y_raw.astype(str)

        unique_classes = np.unique(y).tolist()
        if len(unique_classes) < 2:
            log_error(f"CRITICAL: Fewer than 2 unique classes found for target '{target_col}'.")
            sys.exit(1)

        # 1. Primary LOSO Model Evaluation
        logo = LeaveOneGroupOut()
        clf = RandomForestClassifier(n_estimators=100, n_jobs=-1, random_state=42)
        preds = np.empty_like(y, dtype=object)

        for train_idx, test_idx in logo.split(X, y, groups):
            clf.fit(X[train_idx], y[train_idx])
            preds[test_idx] = clf.predict(X[test_idx])

        obs_acc = float(balanced_accuracy_score(y, preds))

        p_raw, r_raw, f_raw, s_raw = precision_recall_fscore_support(
            y, preds, labels=unique_classes, zero_division=0
        )
        precisions: np.ndarray = np.asarray(p_raw)
        recalls: np.ndarray = np.asarray(r_raw)
        f1_scores: np.ndarray = np.asarray(f_raw)
        supports: np.ndarray = np.asarray(s_raw)

        # 2. Group-Aware Permutation Significance Test
        perm_targets = []
        for _ in range(n_perms):
            y_p = y.copy()
            for g in unique_groups:
                idx = np.where(groups == g)[0]
                y_p[idx] = rng.permutation(y_p[idx])
            perm_targets.append(y_p)

        perm_scores = Parallel(n_jobs=-1, batch_size='auto')(
            delayed(_evaluate_perm)(X, y_p, groups) for y_p in perm_targets
        )

        k = np.sum(np.array(perm_scores) >= obs_acc)
        p_val = float((k + 1.0) / (n_perms + 1.0))
        sig_stars = get_significance_stars(p_val)

        # 3. Build Unified Row Output
        for cls_name, p, r, f, s in zip(unique_classes, precisions, recalls, f1_scores, supports):
            summary_rows.append({
                'target': str(target_col),
                'class_label': str(cls_name),
                'n_samples': int(s),
                'n_subjects': int(len(unique_groups)),
                'precision': float(p),
                'recall': float(r),
                'f1_score': float(f),
                'balanced_accuracy': float(obs_acc),
                'n_permutations': int(n_perms),
                'p_value_permutation': float(p_val),
                'significance': str(sig_stars)
            })

    if not summary_rows:
        log_error("CRITICAL: Classification generated no summary rows.")
        sys.exit(1)

    out_file = os.path.join(os.getcwd(), f"{base}_loso_classification_results.parquet")
    pl.DataFrame(summary_rows).write_parquet(out_file, compression='zstd')

    # Output file location MUST be printed last on stdout for Nextflow capture
    print(out_file)


if __name__ == '__main__':
    main()