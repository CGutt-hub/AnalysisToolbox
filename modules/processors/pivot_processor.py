"""Pivot Processor — Reshape long-format to wide-format parquet (or vice versa).

Long → Wide:
  Input:  [index_cols..., pivot_col, value_col]
  Output: [index_cols..., <unique values of pivot_col as columns>]

Wide → Long:
  Input:  [index_cols..., value_cols...]
  Output: [index_cols..., variable, value]
"""
from __future__ import annotations

import os
import sys

import polars as pl


def pivot_process(ip: str, direction: str = 'long_to_wide',
                  index_cols: str = '', pivot_col: str = '',
                  value_col: str = 'value') -> str:
    """Pivot a parquet file.

    Parameters
    ----------
    ip : str
        Input parquet path.
    direction : str
        'long_to_wide' or 'wide_to_long'.
    index_cols : str
        Comma-separated columns to keep as row identifiers.
    pivot_col : str
        Column whose unique values become new column headers (long_to_wide only).
    value_col : str
        Column containing the values to fill (long_to_wide) or
        comma-separated column names to unpivot (wide_to_long).
    """
    if not os.path.exists(ip):
        print(f"[pivot] ERROR: File not found: {ip}"); sys.exit(1)
    print(f"[pivot] Pivot ({direction}): {ip}")

    df = pl.read_parquet(ip)
    idx = [c.strip() for c in index_cols.split(',') if c.strip()]

    if direction == 'long_to_wide':
        if not pivot_col or pivot_col not in df.columns:
            print(f"[pivot] ERROR: pivot_col '{pivot_col}' not in columns"); sys.exit(1)
        if value_col not in df.columns:
            print(f"[pivot] ERROR: value_col '{value_col}' not in columns"); sys.exit(1)
        df_out = (df.filter(pl.col(value_col).is_not_null())
                  .unique(subset=idx + [pivot_col], keep='first')
                  .pivot(values=value_col, index=idx, on=pivot_col,
                         aggregate_function='mean')
                  .sort(idx))
        suffix = 'wide'
    elif direction == 'wide_to_long':
        val_cols = [c.strip() for c in value_col.split(',') if c.strip()]
        if not val_cols:
            val_cols = [c for c in df.columns if c not in idx and df[c].dtype.is_numeric()]
        df_out = df.unpivot(on=val_cols, index=idx,
                            variable_name='variable', value_name='value')
        suffix = 'long'
    else:
        print(f"[pivot] ERROR: Unknown direction '{direction}'"); sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_{suffix}.parquet"
    df_out.write_parquet(out_file, compression='snappy')
    print(f"[pivot] Output: {out_file} — shape {df_out.shape}")
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 2:
        pivot_process(
            a[1],
            a[2] if len(a) > 2 else 'long_to_wide',
            a[3] if len(a) > 3 else '',
            a[4] if len(a) > 4 else '',
            a[5] if len(a) > 5 else 'value',
        )
    else:
        print('[pivot] Reshape parquet between long and wide formats.\n'
              'Usage: pivot_processor.py <input.parquet> [long_to_wide|wide_to_long] '
              '[index_cols] [pivot_col] [value_col]')
        sys.exit(1)
