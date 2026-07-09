"""Multimodal Classifier — Generic feature combination for classification tasks.

Combines features from multiple modalities (e.g., EEG, EDA, HRV, FAI) into a
unified feature matrix for classification. This module is designed to be
fully generic and reusable across any multimodal analysis pipeline.

Usage:
    multimodal_classifier.py <input_file1.parquet> [<input_file2.parquet> ...]
        --output-domain-id <domain_col>
        --label-col <label_col>
        --eeg-features <eeg_col1,eeg_col2,...>
        --non-eeg-features <eda_col,hrv_col,...>
        [--window-sec <sec>]
        [--step-sec <sec>]
        [table|terminal]

Example:
    # Combine EEG covariance + EDA/HRV/FAI features for classification
    multimodal_classifier.py EV2_01_eeg_epochs.parquet EV2_01_eda_binned.parquet \
        EV2_01_hrv_binned.parquet EV2_01_fai_binned.parquet \
        --output-domain-id participant_id \
        --label-col condition \
        --eeg-features F3,F4,Fz,P3,P4,Pz \
        --non-eeg-features eda,hrv,fai \
        --window-sec 4 \
        --step-sec 2 \
        table
"""

import os, sys, json, warnings
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
            features.append(float(val) if not pl.is_null(val) else 0.0)
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
    # Separate EEG and non-EEG files by checking for EEG-specific columns
    eeg_files = []
    non_eeg_files = {}
    
    for f in files:
        df = pl.read_parquet(f)
        has_eeg = any(col in df.columns for col in eeg_features)
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


def _participant_id_from_path(path: str) -> str:
    """Extract participant ID from filename (e.g., EV2_01_eda_binned.parquet -> EV2_01)."""
    base = Path(path).stem
    parts = base.split('_')
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return base


def analyze_multimodal(
    files: list[str],
    output_domain_id: str,
    label_col: str,
    eeg_features: list[str],
    non_eeg_features: list[str],
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
        eeg_features: List of EEG channel columns.
        non_eeg_features: List of non-EEG feature columns.
        sfreq: Sampling frequency (Hz).
        window_sec: Window length for EEG covariance (seconds).
        step_sec: Step size for sliding windows (seconds).
        output_type: 'table' or 'terminal' (output format).
    
    Returns:
        Output file path (parquet).
    """
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
            sys.exit(1)

    log_info(f"Processing {len(files)} files with EEG features: {eeg_features}, non-EEG features: {non_eeg_features}")

    result = build_multimodal_dataset(
        files=files,
        domain_col=output_domain_id,
        label_col=label_col,
        eeg_features=eeg_features,
        non_eeg_features=non_eeg_features,
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
    parser.add_argument('--eeg-features', default='', help='Comma-separated list of EEG channel columns')
    parser.add_argument('--non-eeg-features', default='', help='Comma-separated list of non-EEG feature columns')
    parser.add_argument('--sfreq', type=int, default=128, help='Sampling frequency (Hz)')
    parser.add_argument('--window-sec', type=float, default=4.0, help='Window length for EEG covariance (seconds)')
    parser.add_argument('--step-sec', type=float, default=2.0, help='Step size for sliding windows (seconds)')
    parser.add_argument('--output-type', default='table', choices=['table', 'terminal'], help='Output type')
    
    args = parser.parse_args()
    
    # Parse feature lists
    eeg_features = _parse_list(args.eeg_features)
    non_eeg_features = _parse_list(args.non_eeg_features)
    
    if not eeg_features:
        log_error("--eeg-features must be specified for EEG covariance computation")
        sys.exit(1)
    
    output_file = analyze_multimodal(
        files=args.input_files,
        output_domain_id=args.output_domain_id,
        label_col=args.label_col,
        eeg_features=eeg_features,
        non_eeg_features=non_eeg_features,
        sfreq=args.sfreq,
        window_sec=args.window_sec,
        step_sec=args.step_sec,
        output_type=args.output_type,
    )
    
    print(output_file)