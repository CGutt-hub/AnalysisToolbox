"""T-Test Analyzer — Independent, paired, and Welch's t-tests on epoched data.

Input:  parquet with [condition, epoch_id, channel_cols...]
        Exactly two unique conditions expected.
Output: parquet with [channel, condition_a, condition_b, t, p, cohens_d, test_type]
"""
import polars as pl, numpy as np, sys, os
from scipy.stats import ttest_ind, ttest_rel

_META = {'condition', 'epoch_id', 'time', 'sfreq'}


def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}"

def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}")


def ttest_analyze(ip: str, test_type: str = 'independent', equal_var: bool = True) -> str:
    """Run t-test between two conditions for each numeric channel.

    Parameters
    ----------
    ip : str
        Path to input parquet (condition, epoch_id, channel_cols...).
    test_type : str
        'independent' (default), 'welch', or 'paired'.
    equal_var : bool
        For independent only; ignored when test_type='welch' or 'paired'.
    """
    if not os.path.exists(ip):
        print(f"[ttest] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[ttest] T-test ({test_type}): {ip}")

    df = pl.read_parquet(ip)
    conditions = sorted(df['condition'].unique().to_list())
    if len(conditions) != 2:
        print(f"[ttest] ERROR: Exactly 2 conditions required, got {len(conditions)}: {conditions}")
        sys.exit(1)

    num_cols = [c for c in df.columns if c not in _META and df[c].dtype.is_numeric()]
    if not num_cols:
        print("[ttest] ERROR: No numeric channel columns found"); sys.exit(1)

    cond_a, cond_b = conditions
    df_a = df.filter(pl.col('condition') == cond_a)
    df_b = df.filter(pl.col('condition') == cond_b)
    print(f"[ttest] {len(num_cols)} channel(s), conditions: {cond_a} vs {cond_b}")
ttest_rel(a[:n], b[:n])
        elif test_type == 'welch':
            res = ttest_ind(a, b, equal_var=False)
        else:
            res = ttest_ind(a, b, equal_var=equal_var)

        t_stat = _fl(res[0])
        p_val = _flen(a), len(b))
            res = ttest_rel(a[:n], b[:n])
        elif test_type == 'welch':
            res = ttest_ind(a, b, equal_var=False)
        else:
            res = ttest_ind(a, b, equal_var=equal_var)

        t_stat = _fl(res[0])
        p_val = _fl(res[1])

        pooled_std = float(np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2))
        d = float((np.mean(a) - np.mean(b)) / pooled_std) if pooled_std > 0 else 0.0

        results.append({
            'channel': ch, 'condition_a': cond_a, 'condition_b': cond_b,
            'test_type': test_type, 't': round(t_stat, 4),
            'p': round(p_val, 5), 'cohens_d': round(d, 4),
            'significant': p_val < 0.05,
        })

    result_df = pl.DataFrame(results)
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_ttest.parquet"
    result_df.write_parquet(out_file, compression='gzip')

    sig_count = sum(1 for r in results if r['significant'])
    print(f"[ttest] Output: {out_file} ({len(results)} channels, {sig_count} significant)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        ttest_analyze(a[1],
                      a[2] if len(a) > 2 else 'independent',
                      a[3].lower() not in ('0', 'false', 'no') if len(a) > 3 else True)
    else:
        print('[ttest] T-test between two conditions per channel.\n'
              'Usage: ttest_analyzer.py <input.parquet> [independent|welch|paired] [equal_var=true]')
        sys.exit(1)
