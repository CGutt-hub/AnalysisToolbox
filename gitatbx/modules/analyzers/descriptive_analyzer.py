"""Descriptive-Statistics Analyzer — Per-condition summary statistics for channel data.

Input:  parquet with [condition, epoch_id, channel_cols...]
Output: parquet with [channel, condition, mean, std, sem, median, iqr, min, max,
                       skewness, kurtosis, n, ci_95_low, ci_95_high]
"""
from __future__ import annotations
import os, sys, numpy as np, polars as pl
from scipy.stats import kurtosis, sem as scipy_sem, skew

_META = {'condition', 'epoch_id', 'time', 'sfreq'}


def descriptive_analyze(ip: str) -> str:
    if not os.path.exists(ip):
        print(f"[descriptive] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[descriptive] Descriptive statistics: {ip}")

    df = pl.read_parquet(ip)
    conditions = sorted(df['condition'].unique().to_list())
    num_cols = [c for c in df.columns if c not in _META and df[c].dtype.is_numeric()]
    if not num_cols:
        print("[descriptive] ERROR: No numeric channel columns"); sys.exit(1)

    print(f"[descriptive] {len(num_cols)} channel(s), {len(conditions)} condition(s)")
    results = []
    for cond in conditions:
        sub = df.filter(pl.col('condition') == cond)
        for ch in num_cols:
            epoch_means = sub.group_by('epoch_id').agg(pl.col(ch).mean())[ch].to_numpy()
            if len(epoch_means) == 0:
                continue
            m = float(np.mean(epoch_means))
            s = float(np.std(epoch_means, ddof=1)) if len(epoch_means) > 1 else 0.0
            se = float(scipy_sem(epoch_means)) if len(epoch_means) > 1 else 0.0
            n = len(epoch_means)
            ci_half = 1.96 * se
            results.append({
                'channel': ch, 'condition': cond,
                'mean': round(m, 4), 'std': round(s, 4), 'sem': round(se, 4),
                'median': round(float(np.median(epoch_means)), 4),
                'iqr': round(float(np.percentile(epoch_means, 75) - np.percentile(epoch_means, 25)), 4),
                'min': round(float(np.min(epoch_means)), 4),
                'max': round(float(np.max(epoch_means)), 4),
                'skewness': round(float(skew(epoch_means)), 4) if n > 2 else None,
                'kurtosis': round(float(kurtosis(epoch_means)), 4) if n > 2 else None,
                'n': n,
                'ci_95_low': round(m - ci_half, 4),
                'ci_95_high': round(m + ci_half, 4),
            })

    result_df = pl.DataFrame(results)
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_descriptive.parquet"
    result_df.write_parquet(out_file, compression='gzip')
    print(f"[descriptive] Output: {out_file} ({len(results)} rows)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        descriptive_analyze(a[1])
    else:
        print('[descriptive] Compute descriptive statistics per channel × condition.\n'
              'Usage: descriptive_analyzer.py <input.parquet>')
        sys.exit(1)
