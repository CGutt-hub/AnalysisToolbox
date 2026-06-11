"""Effect-Size Analyzer — Cohen's d, eta-squared, and partial eta-squared.

Input:  parquet with [condition, epoch_id, channel_cols...]
Output: parquet with [channel, metric, value, ci_low, ci_high] per condition pair

Supports:
  cohens_d   — standardised mean difference (2 conditions)
  eta_sq     — proportion of variance explained (2+ conditions, from ANOVA SS)
"""
from __future__ import annotations
import os, sys, numpy as np, polars as pl

_META = {'condition', 'epoch_id', 'time', 'sfreq'}


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    pooled = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2)
    return float((np.mean(a) - np.mean(b)) / pooled) if pooled > 0 else 0.0


def _eta_squared(groups: list[np.ndarray]) -> float:
    grand = np.concatenate(groups)
    grand_mean = np.mean(grand)
    ss_between = sum(len(g) * (np.mean(g) - grand_mean) ** 2 for g in groups)
    ss_total = np.sum((grand - grand_mean) ** 2)
    return float(ss_between / ss_total) if ss_total > 0 else 0.0


def _bootstrap_ci(a: np.ndarray, b: np.ndarray, n_boot: int = 2000,
                   alpha: float = 0.05) -> tuple[float, float]:
    rng = np.random.default_rng(42)
    ds = np.empty(n_boot)
    for i in range(n_boot):
        ai = rng.choice(a, len(a), replace=True)
        bi = rng.choice(b, len(b), replace=True)
        ds[i] = _cohens_d(ai, bi)
    lo, hi = np.percentile(ds, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def effectsize_analyze(ip: str, metric: str = 'cohens_d') -> str:
    if not os.path.exists(ip):
        print(f"[effectsize] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[effectsize] Effect size ({metric}): {ip}")

    df = pl.read_parquet(ip)
    conditions = sorted(df['condition'].unique().to_list())
    num_cols = [c for c in df.columns if c not in _META and df[c].dtype.is_numeric()]
    if not num_cols:
        print("[effectsize] ERROR: No numeric channel columns"); sys.exit(1)

    results = []
    for ch in num_cols:
        groups = []
        for cond in conditions:
            vals = (df.filter(pl.col('condition') == cond)
                    .group_by('epoch_id').agg(pl.col(ch).mean())[ch].to_numpy())
            groups.append(vals)

        if metric == 'cohens_d':
            if len(conditions) != 2:
                print(f"[effectsize] ERROR: cohens_d requires 2 conditions, got {len(conditions)}")
                sys.exit(1)
            d = _cohens_d(groups[0], groups[1])
            ci_lo, ci_hi = _bootstrap_ci(groups[0], groups[1])
            results.append({'channel': ch, 'metric': 'cohens_d',
                            'condition_a': conditions[0], 'condition_b': conditions[1],
                            'value': round(d, 4), 'ci_low': round(ci_lo, 4), 'ci_high': round(ci_hi, 4)})
        elif metric == 'eta_sq':
            eta = _eta_squared(groups)
            results.append({'channel': ch, 'metric': 'eta_squared',
                            'condition_a': 'all', 'condition_b': 'all',
                            'value': round(eta, 4), 'ci_low': None, 'ci_high': None})

    result_df = pl.DataFrame(results)
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_effectsize.parquet"
    result_df.write_parquet(out_file, compression='snappy')
    print(f"[effectsize] Output: {out_file} ({len(results)} rows)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        effectsize_analyze(a[1], a[2] if len(a) > 2 else 'cohens_d')
    else:
        print('[effectsize] Compute effect sizes per channel.\n'
              'Usage: effectsize_analyzer.py <input.parquet> [cohens_d|eta_sq]')
        sys.exit(1)
