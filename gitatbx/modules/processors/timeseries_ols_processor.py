"""Time-Series OLS Processor — OLS regression on wide-format time-series data.

Input:  wide parquet with x_col (e.g. year) + multiple y_cols (indicator columns)
Output: parquet with [column, slope, slope_se, p_value, r_squared, significant,
                       value_first, value_last, value_mean, total_change_pct,
                       x_start, x_end, n_observations]

This is the generic AnalysisToolbox equivalent of the inline linregress loops
in domain-specific pipelines (e.g. labourAIVolt analyze_ols).
"""
import polars as pl, numpy as np, sys, os
from scipy.stats import linregress


def _fl(v: object) -> float:
    """Extract float from a scipy result element (works around incomplete stubs)."""
    return float(f"{v}")


def timeseries_ols_process(ip: str, x_col: str = 'year', y_cols: str | None = None) -> str:
    """Run OLS regression of each y_col against x_col.

    Parameters
    ----------
    ip : str
        Path to wide-format parquet.
    x_col : str
        Column name for the independent variable (default: 'year').
    y_cols : str or None
        Comma-separated column names for dependent variables.
        If None, all numeric columns except x_col are used.
    """
    if not os.path.exists(ip):
        print(f"[ts_ols] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[ts_ols] Time-series OLS: {ip}, x={x_col}")

    df = pl.read_parquet(ip)
    if x_col not in df.columns:
        print(f"[ts_ols] ERROR: x_col '{x_col}' not found"); sys.exit(1)

    if y_cols:
        cols = [c.strip() for c in y_cols.split(',')]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            print(f"[ts_ols] ERROR: Columns not found: {missing}"); sys.exit(1)
    else:
        cols = [c for c in df.columns if c != x_col and df[c].dtype.is_numeric()]

    if not cols:
        print("[ts_ols] ERROR: No y columns found"); sys.exit(1)
    print(f"[ts_ols] {len(cols)} y-column(s)")

    records: list[dict[str, object]] = []
    for col in cols:
        data = df.filter(pl.col(col).is_not_null())
        if len(data) < 3:
            continue
        x = data[x_col].to_numpy().astype(float)
        y = data[col].to_numpy().astype(float)
        res = linregress(x, y)
        slope = _fl(res[0])
        r_val = _fl(res[2])
        p = _fl(res[3])
        se = _fl(res[4])
        mean_val = float(np.mean(y))
        records.append({
            'column': col,
            'slope': round(slope, 6),
            'slope_se': round(se, 6),
            'p_value': round(p, 5),
            'r_squared': round(r_val ** 2, 4),
            'significant': p < 0.05,
            'value_first': round(float(y[0]), 4),
            'value_last': round(float(y[-1]), 4),
            'value_mean': round(mean_val, 4),
            'total_change_pct': round((y[-1] - y[0]) / (abs(y[0]) + 1e-9) * 100, 2),
            'x_start': round(float(x[0]), 2),
            'x_end': round(float(x[-1]), 2),
            'n_observations': len(x),
        })

    if not records:
        print("[ts_ols] WARNING: No columns with enough data (n>=3)")
        result_df = pl.DataFrame()
    else:
        result_df = pl.DataFrame(records)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_ts_ols.parquet"
    result_df.write_parquet(out_file, compression='gzip')

    sig_count = sum(1 for r in records if r['significant'])
    print(f"[ts_ols] Output: {out_file} ({len(records)} columns, {sig_count} significant)")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        timeseries_ols_process(a[1],
                               a[2] if len(a) > 2 else 'year',
                               a[3] if len(a) > 3 else None)
    else:
        print('[ts_ols] OLS regression on wide-format time-series data.\n'
              'Usage: timeseries_ols_processor.py <input.parquet> [x_col=year] [y_col1,y_col2,...]')
        sys.exit(1)
