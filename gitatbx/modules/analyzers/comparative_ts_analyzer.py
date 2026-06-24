"""Comparative Time-Series Analyzer — incremental overlaid group comparison.

Input:  A trigger parquet (the latest participant's data) — ensures this process
        runs each time a new participant completes.
        + l1_dir: path to the L1 results folder containing all participant plots/.
        The analyzer scans l1_dir for all *_api.parquet files and concatenates
        them, so each run produces an updated overlay of all participants so far.

Output: comparative.parquet          — OLS results per group × indicator
"""
from __future__ import annotations
import glob, os, sys, numpy as np, polars as pl
from scipy.stats import linregress

TAG = "comparative_ts"


def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}")


def comparative_ts_analyze(
    trigger: str,
    l1_dir: str,
    group_col: str = 'participant_id',
    x_col: str = 'year',
    indicator_col: str = 'indicator',
    value_col: str = 'value',
) -> str:
    """Incremental overlaid group comparison.

    Scans l1_dir/**/plots/*_api.parquet (all completed participants),
    concatenates them, and produces overlaid line charts + grouped OLS.
    Re-runs every time a new participant triggers this process.
    """
    print(f"[{TAG}] Triggered by: {trigger}")
    print(f"[{TAG}] Scanning L1 dir: {l1_dir}")

    # Find all api parquets across L1 participant folders
    pattern = os.path.join(l1_dir, "**", "plots", "*_api.parquet")
    api_files = sorted(glob.glob(pattern, recursive=True))
    if not api_files:
        # Fallback: check directly in participant subfolders
        pattern = os.path.join(l1_dir, "**", "*_api.parquet")
        api_files = sorted(glob.glob(pattern, recursive=True))

    if not api_files:
        print(f"[{TAG}] No *_api.parquet found in {l1_dir} — only trigger available")
        # At minimum, use the trigger file itself
        if os.path.exists(trigger):
            api_files = [trigger]
        else:
            print(f"[{TAG}] ERROR: No data found"); sys.exit(1)

    print(f"[{TAG}] Found {len(api_files)} participant datasets:")
    for f in api_files:
        print(f"[{TAG}]   {os.path.basename(f)}")

    # Concatenate all participant api parquets
    dfs = []
    for f in api_files:
        try:
            dfs.append(pl.read_parquet(f))
        except Exception as e:
            print(f"[{TAG}] WARNING: Could not read {f}: {e}")
    if not dfs:
        print(f"[{TAG}] ERROR: No readable parquets"); sys.exit(1)

    df = pl.concat(dfs, how='diagonal')
    for col in (group_col, x_col, indicator_col, value_col):
        if col not in df.columns:
            print(f"[{TAG}] ERROR: Column '{col}' not found in {list(df.columns)}")
            sys.exit(1)

    groups = sorted(df[group_col].unique().to_list(), key=str)
    indicators = sorted(df[indicator_col].unique().to_list(), key=str)
    print(f"[{TAG}] {len(groups)} groups, {len(indicators)} indicators")

    # Resolve display labels: prefer 'country' column if available
    label_map: dict[str, str] = {}
    for g in groups:
        sub = df.filter(pl.col(group_col) == g)
        if 'country' in sub.columns:
            label_map[str(g)] = str(sub['country'][0])
        else:
            label_map[str(g)] = str(g)
    group_labels = [label_map[str(g)] for g in groups]
    print(f"[{TAG}] Labels: {group_labels}")

    # Use a stable base name (not trigger filename) since we're aggregating all L1
    base = 'comparative'

    # ── Per-indicator overlaid line charts ──────────────────────────────────
    for ind in indicators:
        ind_df = df.filter(pl.col(indicator_col) == ind)
        x_per_group: list[list[float]] = []
        y_per_group: list[list[float]] = []
        for g in groups:
            g_df = (ind_df
                    .filter(pl.col(group_col) == g)
                    .filter(pl.col(value_col).is_not_null())
                    .sort(x_col))
            x_per_group.append(g_df[x_col].cast(pl.Float64).to_list())
            y_per_group.append(g_df[value_col].cast(pl.Float64).to_list())

        ind_out = pl.DataFrame({
            'plot_type': ['line_overlay'],
            'title': [ind.replace('_', ' ').title()],
            'x_data': [x_per_group],
            'y_data': [y_per_group],
            'y_var': [[[] for _ in groups]],
            'labels': [group_labels],
            'x_label': [x_col.replace('_', ' ').title()],
            'y_label': [ind.replace('_', ' ').title()],
        })
        ind_path = f"{base}_{ind}.parquet"
        ind_out.write_parquet(ind_path, compression='gzip')
        print(f"[{TAG}] Overlay: {ind_path}")

    # ── OLS per group × indicator ──────────────────────────────────────────
    records: list[dict[str, object]] = []
    for g in groups:
        for ind in indicators:
            sub = (df.filter(
                (pl.col(group_col) == g) & (pl.col(indicator_col) == ind)
            ).filter(pl.col(value_col).is_not_null())
             .sort(x_col))
            if len(sub) < 3:
                continue
            x = sub[x_col].to_numpy().astype(float)
            y = sub[value_col].to_numpy().astype(float)
            res = linregress(x, y)
            slope = _fl(res[0])
            se = _fl(res[4])
            p = _fl(res[3])
            r_val = _fl(res[2])
            records.append({
                'group': label_map[str(g)],
                'group_id': str(g),
                'indicator': ind,
                'slope': round(slope, 6),
                'slope_se': round(se, 6),
                'p_value': round(p, 5),
                'r_squared': round(r_val ** 2, 4),
                'significant': p < 0.05,
                'value_first': round(float(y[0]), 4),
                'value_last': round(float(y[-1]), 4),
                'total_change_pct': round(
                    (float(y[-1]) - float(y[0])) / (abs(float(y[0])) + 1e-9) * 100, 2
                ),
                'n_observations': len(x),
            })

    result_df = pl.DataFrame(records) if records else pl.DataFrame()
    out_file = f"{base}_comparative.parquet"
    result_df.write_parquet(out_file, compression='gzip')

    sig_count = sum(1 for r in records if r.get('significant'))
    print(f"[{TAG}] Output: {out_file} "
          f"({len(records)} results, {sig_count} significant, "
          f"{len(indicators)} indicators × {len(groups)} groups)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 3:
        comparative_ts_analyze(
            a[1],
            a[2],
            a[3] if len(a) > 3 else 'participant_id',
            a[4] if len(a) > 4 else 'year',
            a[5] if len(a) > 5 else 'indicator',
            a[6] if len(a) > 6 else 'value',
        )
    else:
        print(f'[{TAG}] Incremental overlaid group comparison of time-series data.\n'
              f'Usage: comparative_ts_analyzer.py <trigger.parquet> <l1_results_dir> '
              f'[group_col] [x_col] [indicator_col] [value_col]')
        sys.exit(1)
