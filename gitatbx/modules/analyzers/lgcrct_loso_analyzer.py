"""LGC-RCT Leave-One-Subject-Out (LOSO) Analyzer.

Builds EEG sliding windows from epoch parquets, derives binary labels,
and runs cross-subject LOSO classification via the lgcrct package.

Usage:
    lgcrct_loso_analyzer.py <eeg_epoch_1.parquet> [<eeg_epoch_2.parquet> ...] \
        [--mode auto|pilot|study] \
        [--targets valence,arousal] \
        [--sfreq 128] [--window-sec 4] [--step-sec 2] \
        [--band 15,36] [--threshold 5.0] \
        [--use-lgc true] [--half-window 10] \
        [--cov-estimator lwf] [--lgc-mean riemann]

Output: lgcrct_loso_<mode>_result.parquet
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import polars as pl

try:
    from scipy.signal import butter, filtfilt
except Exception:
    butter = None
    filtfilt = None


def log_info(msg: str) -> None:
    print(f"[lgcrct_loso] INFO: {msg}")


def log_warning(msg: str) -> None:
    print(f"[lgcrct_loso] WARNING: {msg}")


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_band(value: str) -> tuple[float, float] | None:
    if value in ("None", "", None):
        return None
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    if len(parts) != 2:
        raise ValueError(f"Invalid band format '{value}', expected 'lo,hi'")
    lo, hi = float(parts[0]), float(parts[1])
    if lo <= 0 or hi <= lo:
        raise ValueError(f"Invalid band values '{value}'")
    return lo, hi


def _participant_id_from_path(path: str) -> str:
    base = Path(path).stem
    m = re.search(r"^[A-Za-z]+_[0-9]+", base)
    if m:
        return m.group(0)
    m = re.search(r"([A-Za-z]+_[0-9]+)", base)
    if m:
        return m.group(1)
    return base.split("_")[0]


def _ordered_channels(columns: list[str]) -> list[str]:
    canonical_32 = [
        "Fp1", "AF3", "F3", "F7", "FC5", "FC1", "C3", "T7",
        "CP5", "CP1", "P3", "P7", "PO3", "O1", "Oz", "Pz",
        "P4", "P8", "PO4", "O2", "T8", "CP2", "CP6", "C4",
        "FC2", "FC6", "F4", "F8", "AF4", "Fp2", "Fz", "Cz",
    ]
    selected = [ch for ch in canonical_32 if ch in columns]
    if selected:
        return selected

    blacklist = {"condition", "epoch_id", "trial_id", "time", "signal", "folder_path", "data"}
    return [c for c in columns if c not in blacklist]


def _find_labels_file(eeg_file: str, pid: str) -> str | None:
    parent = Path(eeg_file).parent
    direct = parent / f"{pid}_labels.parquet"
    if direct.exists():
        return str(direct)

    matches = sorted(parent.glob("*labels.parquet"))
    if matches:
        return str(matches[0])
    return None


def _build_trial_label_map(labels_file: str, target: str, threshold: float) -> dict[str, int]:
    labels = pl.read_parquet(labels_file)
    if "trial_id" not in labels.columns:
        return {}

    if target not in labels.columns:
        return {}

    out: dict[str, int] = {}
    for row in labels.select(["trial_id", target]).to_dicts():
        try:
            out[str(row["trial_id"])] = int(float(row[target]) >= threshold)
        except Exception:
            continue
    return out


def _label_from_condition(condition: str) -> int | None:
    cond_u = str(condition).upper()
    if "POS" in cond_u:
        return 1
    if "NEG" in cond_u:
        return 0
    return None


def _bandpass(signal_ct: np.ndarray, sfreq: float, band: tuple[float, float] | None) -> np.ndarray:
    if band is None:
        return signal_ct

    if butter is None or filtfilt is None:
        log_warning("scipy not available, skipping bandpass filtering")
        return signal_ct

    lo, hi = band
    nyq = sfreq / 2.0
    if hi >= nyq:
        log_warning(f"Band upper edge {hi} >= Nyquist {nyq}, skipping bandpass")
        return signal_ct

    b, a = butter(4, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, signal_ct, axis=1)


def _iter_epoch_windows(
    eeg_df: pl.DataFrame,
    channels: list[str],
    sfreq: int,
    window_sec: float,
    step_sec: float,
) -> Iterable[tuple[str, str, np.ndarray]]:
    win_len = int(round(window_sec * sfreq))
    step_len = int(round(step_sec * sfreq))
    if win_len <= 0 or step_len <= 0:
        raise ValueError("window-sec and step-sec must be > 0")

    if "epoch_id" not in eeg_df.columns:
        return

    for epoch_id in eeg_df["epoch_id"].unique().to_list():
        epoch = eeg_df.filter(pl.col("epoch_id") == epoch_id).sort("time")
        if len(epoch) == 0:
            continue
        condition = str(epoch["condition"][0]) if "condition" in epoch.columns else str(epoch_id)

        mat = epoch.select(channels).to_numpy()
        if mat.size == 0:
            continue
        signal_ct = np.asarray(mat.T, dtype=np.float32)  # (C, T)
        t_len = signal_ct.shape[1]
        if t_len < win_len:
            continue

        for start in range(0, t_len - win_len + 1, step_len):
            yield condition, str(epoch_id), signal_ct[:, start:start + win_len]


def build_dataset(
    eeg_files: list[str],
    mode: str,
    targets: list[str],
    sfreq: int,
    window_sec: float,
    step_sec: float,
    band: tuple[float, float] | None,
    threshold: float,
) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    per_target = {t: {"X": [], "y": [], "domains": []} for t in targets}

    for eeg_file in eeg_files:
        pid = _participant_id_from_path(eeg_file)
        df = pl.read_parquet(eeg_file)
        channels = _ordered_channels(df.columns)
        if not channels:
            log_warning(f"{os.path.basename(eeg_file)} has no channel columns, skipping")
            continue

        mode_local = mode
        if mode_local == "auto":
            if "trial_" in " ".join(df.get_column("condition").cast(pl.String).head(5).to_list()).lower():
                mode_local = "study"
            else:
                mode_local = "pilot"

        label_maps: dict[str, dict[str, int]] = {}
        if mode_local == "study":
            labels_file = _find_labels_file(eeg_file, pid)
            if not labels_file:
                log_warning(f"No labels parquet found next to {os.path.basename(eeg_file)}, skipping participant")
                continue
            for target in targets:
                label_maps[target] = _build_trial_label_map(labels_file, target, threshold)

        n_before = {t: len(per_target[t]["X"]) for t in targets}

        for condition, epoch_id, window_ct in _iter_epoch_windows(df, channels, sfreq, window_sec, step_sec):
            window_ct = _bandpass(window_ct, sfreq, band)
            trial_key = str(condition)
            for target in targets:
                y_val: int | None
                if mode_local == "study":
                    y_val = label_maps[target].get(trial_key)
                else:
                    y_val = _label_from_condition(condition)

                if y_val is None:
                    continue

                per_target[target]["X"].append(window_ct.astype(np.float32, copy=False))
                per_target[target]["y"].append(int(y_val))
                per_target[target]["domains"].append(pid)

        added = {t: len(per_target[t]["X"]) - n_before[t] for t in targets}
        log_info(f"{pid}: added windows {added}")

    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for target in targets:
        X_list = per_target[target]["X"]
        y_list = per_target[target]["y"]
        d_list = per_target[target]["domains"]
        if not X_list:
            log_warning(f"No windows collected for target '{target}'")
            continue
        X = np.stack(X_list).astype(np.float32)
        y = np.asarray(y_list, dtype=np.int64)
        domains = np.asarray(d_list, dtype=object)
        out[target] = (X, y, domains)
        cls0 = int((y == 0).sum())
        cls1 = int((y == 1).sum())
        log_info(f"Target={target} | shape={X.shape} | class0={cls0} class1={cls1} | subjects={len(np.unique(domains))}")
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="LGC-RCT Leave-One-Subject-Out (LOSO) Analyzer")
    parser.add_argument("eeg_files", nargs="+", help="EEG epoch parquet files from pilot/study")
    parser.add_argument("--mode", default="auto", choices=["auto", "pilot", "study"])
    parser.add_argument("--targets", default="valence,arousal")
    parser.add_argument("--sfreq", type=int, default=128)
    parser.add_argument("--window-sec", type=float, default=4.0)
    parser.add_argument("--step-sec", type=float, default=2.0)
    parser.add_argument("--band", default="15,36")
    parser.add_argument("--threshold", type=float, default=5.0)
    parser.add_argument("--use-lgc", default="true")
    parser.add_argument("--half-window", type=int, default=10)
    parser.add_argument("--cov-estimator", default="lwf")
    parser.add_argument("--lgc-mean", default="riemann", choices=["riemann", "euclid"])

    args = parser.parse_args()

    try:
        from lgcrct import run_loso
    except Exception as exc:
        log_warning("Failed to import lgcrct package")
        print("Install in the pipeline venv: pip install lgc-rct")
        raise SystemExit(f"lgcrct import error: {exc}")

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    band = _parse_band(args.band)
    use_lgc = _parse_bool(args.use_lgc)
    half_window = max(0, int(args.half_window if use_lgc else 0))

    datasets = build_dataset(
        eeg_files=args.eeg_files,
        mode=args.mode,
        targets=targets,
        sfreq=args.sfreq,
        window_sec=args.window_sec,
        step_sec=args.step_sec,
        band=band,
        threshold=float(args.threshold),
    )

    results_frames: list[pl.DataFrame] = []
    for target, (X, y, domains) in datasets.items():
        if len(np.unique(domains)) < 2:
            log_warning(f"Skipping target '{target}' because fewer than 2 subjects are available")
            continue

        df_res = run_loso(
            X,
            y,
            domains,
            half_window=half_window,
            cov_estimator=args.cov_estimator,
            lgc_mean=args.lgc_mean,
        )
        res = pl.from_pandas(df_res)
        method_name = "LGC-RCT" if half_window > 0 else "RCT"
        res = res.with_columns([
            pl.lit(target).alias("target_label"),
            pl.lit(method_name).alias("method"),
            pl.lit(half_window).alias("half_window"),
            pl.lit(args.cov_estimator).alias("cov_estimator"),
            pl.lit(args.lgc_mean).alias("lgc_mean"),
            pl.lit(args.sfreq).alias("sfreq"),
            pl.lit(args.window_sec).alias("window_sec"),
            pl.lit(args.step_sec).alias("step_sec"),
            pl.lit(args.band if args.band else "None").alias("band_hz"),
            pl.lit(float(args.threshold)).alias("label_threshold"),
        ])
        results_frames.append(res)

    if not results_frames:
        raise SystemExit("No LOSO results were produced (no valid windows/labels).")

    output = pl.concat(results_frames, how="vertical")
    out_name = f"lgcrct_loso_{args.mode}_result.parquet"
    output.write_parquet(out_name, compression="gzip")
    log_info(f"Wrote {out_name} ({len(output)} rows)")
    print(out_name)
    sys.exit(0)
