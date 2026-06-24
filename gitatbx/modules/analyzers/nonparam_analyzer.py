"""Non-Parametric Analyzer — Rank-based tests for epoched data.

Input:  parquet with [condition, epoch_id, channel_cols...]
Output: parquet with [channel, test, statistic, p, ...]

Supports:
  mann_whitney  — Independent two-sample rank test (2 conditions)
  wilcoxon      — Paired signed-rank test (2 conditions, matched epoch_ids)
  kruskal       — Kruskal-Wallis H-test (2+ conditions)
"""
import polars as pl, numpy as np, sys, os
from scipy.stats import kruskal, mannwhitneyu, wilcoxon

_META = {'condition', 'epoch_id', 'time', 'sfreq'}


def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}"

def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}")


def nonparam_analyze(ip: str, test: str = 'mann_whitney') -> str:
    if not os.path.exists(ip):
        print(f"[nonparam] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[nonparam] Non-parametric test ({test}): {ip}")

    df = pl.read_parquet(ip)
    conditions = sorted(df['condition'].unique().to_list())
    num_cols = [c for c in df.columns if c not in _META and df[c].dtype.is_numeric()]
    if not num_cols:
        print("[nonpaobject] = []
        for cond in conditions:
            vals = (df.filter(pl.col('condition') == cond)
                    .group_by('epoch_id').agg(pl.col(ch).mean())[ch].to_numpy())
            groups.append(vals)

        if test == 'mann_whitney':
            res = mannwhitneyu(groups[0], groups[1], alternative='two-sided')
            stat_val = _fl(res[0])
            p_val = _fl(res[1])
            results.append({'channel': ch, 'test': 'Mann-Whitney U',
                            'condition_a': conditions[0], 'condition_b': conditions[1],
                            'statistic': round(stat_val, 4), 'p': round(p_val, 5),
                            'significant': p_val < 0.05})
        elif test == 'wilcoxon':
            n = min(len(groups[0]), len(groups[1]))
            res = wilcoxon(groups[0][:n], groups[1][:n])
            stat_val = _fl(res[0])
            p_val = _fl(res[1])
            results.append({'channel': ch, 'test': 'Wilcoxon Signed-Rank',
                            'condition_a': conditions[0], 'condition_b': conditions[1],
                            'statistic': round(stat_val, 4), 'p': round(p_val, 5),
                            'significant': p_val < 0.05})
        elif test == 'kruskal':
            res = kruskal(*groups)
            stat_val = _fl(res[0])
            p_val = _fles[1])
            results.append({'channel': ch, 'test': 'Wilcoxon Signed-Rank',
                            'condition_a': conditions[0], 'condition_b': conditions[1],
                            'statistic': round(stat_val, 4), 'p': round(p_val, 5),
                            'significant': p_val < 0.05})
        elif test == 'kruskal':
            res = kruskal(*groups)
            stat_val = _fl(res[0])
            p_val = _fl(res[1])
            results.append({'channel': ch, 'test': 'Kruskal-Wallis H',
                            'condition_a': 'all', 'condition_b': 'all',
                            'statistic': round(stat_val, 4), 'p': round(p_val, 5),
                            'significant': p_val < 0.05})

    result_df = pl.DataFrame(results)
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_nonparam.parquet"
    result_df.write_parquet(out_file, compression='gzip')

    sig_count = sum(1 for r in results if r['significant'])
    print(f"[nonparam] Output: {out_file} ({len(results)} channels, {sig_count} significant)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        nonparam_analyze(a[1], a[2] if len(a) > 2 else 'mann_whitney')
    else:
        print('[nonparam] Non-parametric rank tests per channel.\n'
              'Usage: nonparam_analyzer.py <input.parquet> [mann_whitney|wilcoxon|kruskal]')
        sys.exit(1)
