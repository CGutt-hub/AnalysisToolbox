"""Multimodal Classifier — Generic feature combination for classification tasks.

Combines features from multiple modalities (e.g., EEG, EDA, HRV, FAI) into a
unified feature matrix for classification. This module is designed to be
fully generic and reusable across any multimodal analysis pipeline.

Supports two modes:
1. Raw EEG mode: Computes covariance matrices from raw EEG signals + non-EEG features
2. Binned mode: Combines pre-binned EEG features (alpha/beta/theta) with non-EEG features

Usage:
    # Mode 1: Raw EEG + covariance computation
    multimodal_classifier.py EV2_01_eeg_epochs.parquet EV2_01_eda_binned.parquet \
        EV2_01_hrv_binned.parquet EV2_01_fai_binned.parquet \
        --output-domain-id participant_id \
        --label-col condition \
        --eeg-features F3,F4,Fz,P3,P4,Pz \
        --non-eeg-features eda,hrv,fai \
        --window-sec 4 \
        --step-sec 2 \
        table

    # Mode 2: Binned EEG features (no covariance, direct feature selection)
    multimodal_classifier.py EV2_01_eeg_frontal_binned.parquet EV2_01_eeg_parietal_binned.parquet \
        EV2_01_eda_amplitude.parquet EV2_01_hrv_rmssd.parquet EV2_01_fai.parquet \
        --output-domain-id participant_id \
        --label-col condition \
        --eeg-features frontal_alpha,frontal_beta,frontal_theta,parietal_alpha,parietal_beta,parietal_theta \
        --non-eeg-features amplitude,rmssd,fai \
        table
"""

import os, sys, json, warnings, math
import numpy as np
import polars as pl
from pathlib import Path
from typing import Iterable, Optional


def log_info(msg: str) -> None:
    print(f"[multimodal_classifier] INFO: {msg}")

def log_warning(msg: str) -> None:
    print(f"[multimodal_classifier] WARNING: {msg}")

def log_error(msg: str) -> None:
    print(f"[multimodal_classifier] ERROR: {msg}")


def _participant_id_from_path(path: str) -> str:
    """Extract participant ID from filename (e.g., EV2_01_labels_wide.parquet -> EV2_01)."""
    base = Path(path).stem
    parts = base.split('_')
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return base


def _parse_list(value: str) -> list[str]:
    """Parse a comma-separated list of strings."""
    if value in ("None", "", None):
        return []
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _extract_eeg_windows(
    df: pl.DataFrame,
    eeg_features: list[str],
    sfreq: int,
    window_sec: float,
    step_sec: float,
) -> Iterable[tuple[str, str, np.ndarray]]:
    """Extract sliding windows from EEG data for covariance computation."""
    win_len = int(round(window_sec * sfreq))
    step_len = int(round(step_sec * sfreq))
    if win_len <= 0 or step_len <= 0:
        raise ValueError("window-sec and step-sec must be > 0")

    if "epoch_id" not in df.columns:
        log_warning("No epoch_id column found in EEG data — returning full epochs as single windows")
        for epoch_id in df["condition"].unique().to_list() if "condition" in df.columns else ["0"]:
            epoch = df.filter(pl.col("condition") == epoch_id) if "condition" in df.columns else df
            if len(epoch) == 0:
                continue
            mat = epoch.select(eeg_features).to_numpy()
            if mat.size == 0:
                continue
            # Treat the entire epoch as a single window
            yield str(epoch_id), str(epoch_id), np.asarray(mat.T, dtype=np.float32)
        return

    for epoch_id in df["epoch_id"].unique().to_list():
        epoch = df.filter(pl.col("epoch_id") == epoch_id).sort("time")
        if len(epoch) == 0:
            continue
        condition = str(epoch["condition"][0]) if "condition" in epoch.columns else str(epoch_id)

        mat = epoch.select(eeg_features).to_numpy()
        if mat.size == 0:
            continue
        signal_ct = np.asarray(mat.T, dtype=np.float32)  # (C, T)
        t_len = signal_ct.shape[1]
        if t_len < win_len:
            continue

        for start in range(0, t_len - win_len + 1, step_len):
            yield condition, str(epoch_id), signal_ct[:, start:start + win_len]


def _compute_covariance(window_ct: np.ndarray) -> np.ndarray:
    """Compute covariance matrix for a window of EEG data."""
    if window_ct.shape[1] < window_ct.shape[0]:
        # Not enough samples for covariance — return flattened window
        return window_ct.flatten()
    cov = np.cov(window_ct)
    return cov[np.tril_indices(cov.shape[0])]  # Vectorize to lower triangle


def _extract_non_eeg_features(
    df: pl.DataFrame,
    non_eeg_features: list[str],
    epoch_id: str,
) -> np.ndarray:
    """Extract mean non-EEG features for a given epoch."""
    features = []
    for feature in non_eeg_features:
        if feature in df.columns:
            val = df.filter(pl.col("epoch_id") == epoch_id)[feature].mean()
            try:
                if val is not None:
                    features.append(float(val))  # type: ignore
                else:
                    features.append(0.0)
            except (TypeError, ValueError):
                features.append(0.0)
        else:
            log_warning(f"Non-EEG feature '{feature}' not found in data")
            features.append(0.0)
    return np.array(features, dtype=np.float32)


def build_multimodal_dataset(
    files: list[str],
    domain_col: str,
    label_col: str,
    eeg_features: list[str],
    non_eeg_features: list[str],
    sfreq: int = 128,
    window_sec: float = 4.0,
    step_sec: float = 2.0,
) -> dict:
    """
    Build a multimodal dataset by combining EEG covariance features and non-EEG features.
    
    Args:
        files: List of input parquet files (EEG + non-EEG modalities).
        domain_col: Column name for participant/domain identifier (e.g., 'participant_id').
        label_col: Column name for class labels (e.g., 'condition' or 'valence').
        eeg_features: List of EEG channel columns to include.
        non_eeg_features: List of non-EEG feature columns (e.g., ['eda', 'hrv', 'fai']).
        sfreq: Sampling frequency (Hz).
        window_sec: Window length for EEG covariance computation (seconds).
        step_sec: Step size for sliding windows (seconds).
    
    Returns:
        dict: {'X': feature_matrix, 'y': labels, 'domains': participant_ids, 'feature_names': list}
    """
    # Handle None values for eeg_features and non_eeg_features
    if eeg_features is None:
        eeg_features = []
    if non_eeg_features is None:
        non_eeg_features = []
    
    # Separate EEG and non-EEG files by checking for EEG-specific columns
    eeg_files = []
    non_eeg_files = {}
    
    for f in files:
        df = pl.read_parquet(f)
        has_eeg = any(col in df.columns for col in eeg_features) if eeg_features else False
        if has_eeg:
            eeg_files.append(f)
        else:
            # Group non-EEG files by participant (from filename)
            pid = _participant_id_from_path(f)
            if pid not in non_eeg_files:
                non_eeg_files[pid] = {}
            for feature in non_eeg_features:
                if feature in df.columns:
                    non_eeg_files[pid][feature] = f

    # Load non-EEG data per participant
    non_eeg_data = {}
    for pid, file_map in non_eeg_files.items():
        non_eeg_data[pid] = {}
        for feature, f in file_map.items():
            df = pl.read_parquet(f)
            non_eeg_data[pid][feature] = df

    # Process EEG files
    all_X = []
    all_y = []
    all_domains = []
    feature_names = []

    for eeg_file in eeg_files:
        pid = _participant_id_from_path(eeg_file)
        df = pl.read_parquet(eeg_file)
        
        # Get labels for this participant
        if "condition" in df.columns:
            label_map = dict(zip(df["epoch_id"].to_list(), df["condition"].to_list()))
        else:
            log_warning(f"No condition column in {eeg_file} — using epoch_id as label")
            label_map = dict(zip(df["epoch_id"].to_list(), df["epoch_id"].to_list()))

        # Extract windows and compute covariance features
        for condition, epoch_id, window_ct in _extract_eeg_windows(df, eeg_features, sfreq, window_sec, step_sec):
            cov_features = _compute_covariance(window_ct)
            eeg_feature_names = [f"eeg_cov_{i}" for i in range(len(cov_features))]
            
            # Extract non-EEG features for this epoch
            non_eeg_vals = []
            if pid in non_eeg_data:
                for feature in non_eeg_features:
                    if feature in non_eeg_data[pid]:
                        non_eeg_df = non_eeg_data[pid][feature]
                        val = non_eeg_df.filter(pl.col("epoch_id") == epoch_id)[feature].mean()
                        non_eeg_vals.append(float(val) if not pl.is_null(val) else 0.0)
                    else:
                        non_eeg_vals.append(0.0)
            else:
                non_eeg_vals = [0.0] * len(non_eeg_features)
            
            non_eeg_feature_names = [f"{feature}_mean" for feature in non_eeg_features]
            
            # Combine features
            combined_features = np.concatenate([cov_features, np.array(non_eeg_vals, dtype=np.float32)])
            all_X.append(combined_features)
            all_y.append(condition)
            all_domains.append(pid)
            
            # Track feature names (only once)
            if not feature_names:
                feature_names = eeg_feature_names + non_eeg_feature_names

    if not all_X:
        log_error("No features extracted — check input files and columns")
        return {}

    return {
        'X': np.stack(all_X).astype(np.float32),
        'y': np.array(all_y, dtype=object),
        'domains': np.array(all_domains, dtype=object),
        'feature_names': feature_names,
        'n_features': len(feature_names),
        'n_samples': len(all_X),
        'n_switches': len(all_domains),
    }


def analyze_multimodal(
    files: list[str],
    output_domain_id: str,
    label_col: str,
    eeg_features: list[str] | None = None,
    non_eeg_features: list[str] | None = None,
    feature_cols: list[str] | None = None,
    sfreq: int = 128,
    window_sec: float = 4.0,
    step_sec: float = 2.0,
    output_type: str = "table",
) -> str:
    """
    Main function: Combine multimodal features and output a feature matrix.
    
    Args:
        files: Input parquet files (EEG + non-EEG).
        output_domain_id: Column name for participant/domain identifier.
        label_col: Column name for class labels.
        eeg_features: List of EEG channel columns (for raw EEG mode).
        non_eeg_features: List of non-EEG feature columns (for raw EEG mode).
        feature_cols: List of feature columns (for feature mode - skips EEG covariance).
        sfreq: Sampling frequency (Hz) (for raw EEG mode).
        window_sec: Window length for EEG covariance (seconds) (for raw EEG mode).
        step_sec: Step size for sliding windows (seconds) (for raw EEG mode).
        output_type: 'table' or 'terminal' (output format).
    
    Returns:
        Output file path (parquet).
    """
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
            sys.exit(1)

    # Check if EEG features are already columns (binned mode) or channel names (raw mode)
    # If all eeg_features exist as columns in any file, assume binned mode
    # Separate files into EEG, non-EEG, and label files
    eeg_dfs = []
    non_eeg_dfs = {}
    label_dfs = []
    
    for f in files:
        df = pl.read_parquet(f)
        
        # Add participant_id from filename if missing (required for merging)
        if 'participant_id' not in df.columns:
            pid = _participant_id_from_path(f)
            df = df.with_columns(pl.lit(pid).alias('participant_id'))
            log_info(f"Added participant_id='{pid}' to {os.path.basename(f)}")
        
        has_eeg = any(col in df.columns for col in eeg_features) if eeg_features else False
        has_non_eeg = any(col in df.columns for col in non_eeg_features) if non_eeg_features else False
        has_label = label_col in df.columns
        
        log_info(f"File: {os.path.basename(f)} - EEG: {has_eeg}, Non-EEG: {has_non_eeg}, Label: {has_label}")
        
        if has_eeg:
            eeg_dfs.append(df)
        if has_non_eeg:
            # Group non-EEG files by participant ID for later merging
            pid = _participant_id_from_path(f)
            if pid not in non_eeg_dfs:
                non_eeg_dfs[pid] = []
            non_eeg_dfs[pid].append(df)
        if has_label:
            label_dfs.append(df)
    
    all_eeg_features_exist = len(eeg_dfs) > 0 and all(f in eeg_dfs[0].columns for f in eeg_features) if eeg_features else False
    all_non_eeg_features_exist = len(non_eeg_dfs) > 0 and any(
        all(f in df.columns for f in non_eeg_features) for dfs in non_eeg_dfs.values() for df in dfs
    ) if non_eeg_features else False
    
    if all_eeg_features_exist and all_non_eeg_features_exist:
        # Binned mode: EEG features are already columns (no covariance computation needed)
        log_info(f"Binned mode: Using pre-computed features from input files. EEG: {eeg_features}, Non-EEG: {non_eeg_features}")
        
        # Concatenate all EEG files
        if eeg_dfs:
            eeg_df = pl.concat(eeg_dfs, how='diagonal')
        else:
            log_error("No EEG files found with the specified EEG features.")
            sys.exit(1)
        
        # Drop 'region' column if present (not needed for final output)
        if 'region' in eeg_df.columns:
            eeg_df = eeg_df.drop('region')
            log_info("Dropped 'region' column from EEG data")
        
        # Merge with non-EEG and label data
        # Extract participant IDs from EEG data for matching
        eeg_pids = eeg_df['participant_id'].unique().to_list() if 'participant_id' in eeg_df.columns else []
        
        # Collect all non-EEG data into a single DataFrame
        non_eeg_rows = []
        for pid, dfs in non_eeg_dfs.items():
            if pid in eeg_pids or not eeg_pids:  # Match by participant or accept all if no EEG IDs
                for df in dfs:
                    non_eeg_rows.append(df)
        
        if non_eeg_rows:
            non_eeg_df = pl.concat(non_eeg_rows, how='diagonal')
            # Drop 'region' column from non-EEG data if present
            if 'region' in non_eeg_df.columns:
                non_eeg_df = non_eeg_df.drop('region')
            # Merge EEG and non-EEG data on participant_id and epoch_id
            if 'participant_id' in eeg_df.columns and 'participant_id' in non_eeg_df.columns:
                merged_df = eeg_df.join(non_eeg_df, on=['participant_id', 'epoch_id'], how='left')
            else:
                merged_df = eeg_df
                log_warning("No participant_id column found for joining EEG and non-EEG data. Attempting to concatenate.")
                merged_df = pl.concat([eeg_df, non_eeg_df], how='diagonal')
        else:
            merged_df = eeg_df
        
        # Merge with label data
        if label_dfs:
            label_df = pl.concat(label_dfs, how='diagonal')
            if label_col not in merged_df.columns:
                # Try to merge on participant_id and epoch_id/condition
                if 'participant_id' in merged_df.columns and 'participant_id' in label_df.columns:
                    if 'epoch_id' in merged_df.columns and 'epoch_id' in label_df.columns:
                        merged_df = merged_df.join(label_df, on=['participant_id', 'epoch_id'], how='left')
                    elif 'condition' in merged_df.columns and 'condition' in label_df.columns:
                        merged_df = merged_df.join(label_df, on=['participant_id', 'condition'], how='left')
                else:
                    # Fallback: try merging on epoch_id or condition only
                    if 'epoch_id' in merged_df.columns and 'epoch_id' in label_df.columns:
                        merged_df = merged_df.join(label_df, on=['epoch_id'], how='left')
                    elif 'condition' in merged_df.columns and 'condition' in label_df.columns:
                        merged_df = merged_df.join(label_df, on=['condition'], how='left')
                    else:
                        log_warning("No matching columns for joining label data. Attempting to concatenate.")
                        merged_df = pl.concat([merged_df, label_df], how='diagonal')
            else:
                log_info("Label column already present in merged data.")

        output_df = merged_df
        
        # Ensure label_col exists
        if 'condition' in output_df.columns and label_col != 'condition':
            output_df = output_df.rename({'condition': label_col})
        
        # Select only the features + label
        all_features = []
        if eeg_features:
            all_features.extend(eeg_features)
        if non_eeg_features:
            all_features.extend(non_eeg_features)
        
        requested_cols = [label_col] + all_features
        missing_cols = [col for col in requested_cols if col not in output_df.columns]
        if missing_cols:
            log_error(f"Missing columns in merged data: {missing_cols}. Available: {list(output_df.columns)}")
            sys.exit(1)
        
        output_df = output_df.select(requested_cols)
        
        # Fill nulls with 0.0
        for col in all_features:
            if col in output_df.columns:
                output_df = output_df.with_columns(pl.col(col).fill_null(0.0))
        
        # Write output (use the first input file's directory)
        first_input_file = files[0] if files else "wp5_multimodal_features"
        output_file = f"{Path(first_input_file).parent / 'wp5_multimodal_features'}.parquet"
        output_df.write_parquet(output_file, compression='snappy')
        
        log_info(f"Binned mode output: {output_file} ({len(output_df)} rows, {len(output_df.columns)} columns)")
        return output_file
    
    # Raw EEG mode: compute covariance matrices
    log_info(f"Raw EEG mode: Processing {len(files)} files with EEG features: {eeg_features}, non-EEG features: {non_eeg_features}")

    result = build_multimodal_dataset(
        files=files,
        domain_col=output_domain_id,
        label_col=label_col,
        eeg_features=eeg_features if eeg_features is not None else [],
        non_eeg_features=non_eeg_features if non_eeg_features is not None else [],
        sfreq=sfreq,
        window_sec=window_sec,
        step_sec=step_sec,
    )

    if not result:
        log_error("No data produced — check inputs and parameters")
        sys.exit(1)

    # Create output DataFrame
    output_file = f"{Path(files[0]).parent / 'multimodal_features'}.parquet"
    
    # Build a flat table with one row per window
    rows = []
    for i in range(result['n_samples']):
        rows.append({
            'participant_id': result['domains'][i],
            'label': result['y'][i],
            **{f"feature_{j}": result['X'][i, j] for j in range(result['n_features'])},
        })
    
    output_df = pl.DataFrame(rows)
    output_df.write_parquet(output_file, compression='snappy')
    
    log_info(f"Output: {output_file} (shape={result['X'].shape}, features={result['feature_names']})")
    return output_file


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description="Multimodal Classifier — Combine EEG + non-EEG features for classification")
    parser.add_argument('input_files', nargs='+', help='Input parquet files (EEG + non-EEG modalities)')
    parser.add_argument('--output-domain-id', default='participant_id', help='Column name for participant/domain identifier')
    parser.add_argument('--label-col', default='condition', help='Column name for class labels')
    parser.add_argument('--eeg-features', default='', help='Comma-separated list of EEG channel columns (for raw EEG mode)')
    parser.add_argument('--non-eeg-features', default='', help='Comma-separated list of non-EEG feature columns (for raw EEG mode)')
    parser.add_argument('--feature-cols', default='', help='Comma-separated list of feature columns (for feature mode - skips EEG covariance)')
    parser.add_argument('--sfreq', type=int, default=128, help='Sampling frequency (Hz) (for raw EEG mode)')
    parser.add_argument('--window-sec', type=float, default=4.0, help='Window length for EEG covariance (seconds) (for raw EEG mode)')
    parser.add_argument('--step-sec', type=float, default=2.0, help='Step size for sliding windows (seconds) (for raw EEG mode)')
    parser.add_argument('--output-type', default='table', choices=['table', 'terminal'], help='Output type')
    
    args = parser.parse_args()
    
    # Parse feature lists
    eeg_features = _parse_list(args.eeg_features) if args.eeg_features else None
    non_eeg_features = _parse_list(args.non_eeg_features) if args.non_eeg_features else None
    feature_cols = _parse_list(args.feature_cols) if args.feature_cols else None
    
    # Validate arguments
    if feature_cols:
        # Feature mode: only need feature_cols
        pass
    else:
        # Raw EEG mode: need eeg_features
        if not eeg_features:
            log_error("--eeg-features must be specified for raw EEG mode")
            sys.exit(1)
    
    output_file = analyze_multimodal(
        files=args.input_files,
        output_domain_id=args.output_domain_id,
        label_col=args.label_col,
        eeg_features=eeg_features,
        non_eeg_features=non_eeg_features,
        feature_cols=feature_cols,
        sfreq=args.sfreq,
        window_sec=args.window_sec,
        step_sec=args.step_sec,
        output_type=args.output_type,
    )
    
    print(output_file)