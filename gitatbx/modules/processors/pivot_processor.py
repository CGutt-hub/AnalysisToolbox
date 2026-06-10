"""Pivot Processor — Reshape long-format to wide-format parquet (or vice versa).

Long -> Wide (single file):
  Input:  [index_cols..., pivot_col, value_col]
  Output: [index_cols..., <unique values of pivot_col as columns>]

Wide -> Long (single file):
  Input:  [index_cols..., value_cols...]
  Output: [index_cols..., variable, value]

Multi-file -> Wide (cross-participant / cross-file aggregation):
  Input:  multiple parquets, each with a 'condition' column and a value column.
          Each file contributes one row in the output; the row index (participant_id)
          is inferred from the filename stem (first two '_'-delimited tokens).
          When --auto-prefix is set the third filename token (e.g. 'hrv' from
          DEAP_01_hrv_bootstrap.parquet) is prepended to column names so that
          multiple modalities can be passed together in a single call.
  Output: [participant_id, [<prefix_>]<condition>_<value_col>, ...]
  Use this to build a cross-participant flat table from per-participant result files
  (e.g., bootstrap outputs) for group-level correlation or regression (WP3, WP4).

Usage
-----
  Single file:
    pivot_processor.py <input.parquet> [long_to_wide|wide_to_long]
        [index_cols] [pivot_col] [value_col]

  Multi-file:
    pivot_processor.py multi_file_to_wide <file1.parquet> [file2 ...]
        [--value-col <col>]            (default: y_data)
        [--conditions <c1,c2,...>]     (filter to these conditions; default: all)
        [--col-prefix <prefix>]        (prepend a fixed string to all condition columns)
        [--auto-prefix]                (use 3rd filename token as per-file column prefix)
        [--out-base <stem>]            (default: l2_participant_table)
"""
from __future__ import annotations

import os
import sys

import polars as pl


def log_info(msg):    print(f"[pivot] INFO: {msg}")
def log_warning(msg): print(f"[pivot] WARNING: {msg}")
def log_error(msg):   print(f"[pivot] ERROR: {msg}")


def pivot_process(ip: str, direction: str = 'long_to_wide',
                  index_cols: str = '', pivot_col: str = '',
                  value_col: str = 'value') -> str:
    """Pivot a single parquet file between long and wide formats."""
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)
    log_info(f"Pivot ({direction}): {ip}")

    df = pl.read_parquet(ip)
    idx = [c.strip() for c in index_cols.split(',') if c.strip()]

    if direction == 'long_to_wide':
        if not pivot_col or pivot_col not in df.columns:
            log_error(f"pivot_col '{pivot_col}' not in columns"); sys.exit(1)
        if value_col not in df.columns:
            log_error(f"value_col '{value_col}' not in columns"); sys.exit(1)
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
        log_error(f"Unknown direction '{direction}'"); sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_{suffix}.parquet"
    df_out.write_parquet(out_file, compression='snappy')
    log_info(f"Output: {out_file} — shape {df_out.shape}")
    return out_file


def _participant_id_from_path(path: str) -> str:
    """Infer participant ID from filename stem (first two '_'-separated tokens)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split('_')
    return '_'.join(parts[:2]) if len(parts) >= 2 else stem


def _modality_from_path(path: str) -> str:
    """Infer modality from filename stem (third '_'-separated token if present)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split('_')
    return parts[2] if len(parts) > 2 else ''


def _extract_scalar(value) -> float | None:
    """Extract a float from a value that may be a list or a scalar."""
    if value is None:
        return None
    if isinstance(value, list):
        return float(value[0]) if value else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def multi_file_to_wide(files: list[str],
                       value_col: str = 'y_data',
                       conditions: list[str] | None = None,
                       col_prefix: str = '',
                       auto_prefix: bool = False,
                       out_base: str = 'l2_participant_table') -> str:
    """Build a wide per-participant table from multiple per-participant result files.

    Each input file represents one participant + modality combination.  The
    participant ID is inferred from the first two '_'-delimited filename tokens.

    Column naming:
      - Default:       <condition>_<value_col>
      - --col-prefix:  <prefix><condition>_<value_col>
      - --auto-prefix: <modality>_<condition>_<value_col>  (modality = 3rd token)

    When --auto-prefix is used, multiple modality files per participant are merged
    into a single row, enabling cross-modal correlation in a single pivot call.

    Parameters
    ----------
    files:       Per-participant parquet paths.
    value_col:   Column to extract the summary value from (default: 'y_data').
    conditions:  Optional condition filter.  None = all conditions.
    col_prefix:  Fixed string prepended to all condition column names.
    auto_prefix: When True, use the 3rd filename token as a per-file column prefix.
    out_base:    Output filename stem.

    Returns
    -------
    Path to the output parquet.
    """
    if not files:
        log_error("No input files provided"); sys.exit(1)
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}"); sys.exit(1)

    # participant_id -> row dict  (merged across files when auto_prefix is used)
    participant_rows: dict[str, dict] = {}
    all_cond_cols: set[str] = set()

    _meta = {'condition', 'plot_type', 'x_label', 'y_label', 'x_data', 'y_ticks', 'region'}

    for fpath in sorted(files):
        pid = _participant_id_from_path(fpath)
        if auto_prefix:
            prefix = _modality_from_path(fpath) + '_'
        else:
            prefix = col_prefix

        df = pl.read_parquet(fpath)
        if df.height == 0:
            log_warning(f"Empty file skipped: {fpath}"); continue

        # Resolve value column
        if value_col not in df.columns:
            candidates = [c for c in df.columns
                          if c not in _meta and df[c].dtype.is_numeric()]
            if not candidates:
                log_warning(f"No usable value column in {fpath}, skipping"); continue
            actual_col = candidates[0]
            log_warning(f"'{value_col}' not in {os.path.basename(fpath)}; using '{actual_col}'")
        else:
            actual_col = value_col

        if 'condition' not in df.columns:
            log_warning(f"No 'condition' column in {fpath}, skipping"); continue

        row = participant_rows.setdefault(pid, {'participant_id': pid})
        for rec in df.iter_rows(named=True):
            cond = str(rec.get('condition', ''))
            if conditions and cond not in conditions:
                continue
            scalar = _extract_scalar(rec.get(actual_col))
            if scalar is None:
                continue
            col_name = f"{prefix}{cond}_{actual_col}"
            row[col_name] = scalar
            all_cond_cols.add(col_name)

    # Filter participants that contributed at least one value
    rows = [r for r in participant_rows.values() if len(r) > 1]
    if not rows:
        log_error("No data extracted from any participant file"); sys.exit(1)

    all_cond_cols_sorted = sorted(all_cond_cols)
    for row in rows:
        for col in all_cond_cols_sorted:
            row.setdefault(col, None)

    out_df = (pl.DataFrame(rows)
              .sort('participant_id')
              .with_columns([pl.col(c).cast(pl.Float64) for c in all_cond_cols_sorted]))

    log_info(f"Extracted {len(rows)} participants, {len(all_cond_cols_sorted)} condition columns")
    if auto_prefix:
        modalities = sorted({_modality_from_path(f) for f in files if _modality_from_path(f)})
        log_info(f"Modalities merged: {modalities}")

    out_path = os.path.join(os.getcwd(), f"{out_base}.parquet")
    out_df.write_parquet(out_path, compression='snappy')
    log_info(f"Output: {out_path}")
    print(out_path)
    return out_path


if __name__ == '__main__':
    a = sys.argv[1:]

    if not a:
        print('[pivot] Reshape parquet between long and wide formats.')
        print('[pivot] Usage: pivot_processor.py <input.parquet> [long_to_wide|wide_to_long]')
        print('[pivot]          [index_cols] [pivot_col] [value_col]')
        print('[pivot]        pivot_processor.py multi_file_to_wide <file1.parquet> ...')
        print('[pivot]          [--value-col <col>] [--conditions <c1,c2>]')
        print('[pivot]          [--col-prefix <str>] [--auto-prefix] [--out-base <stem>]')
        sys.exit(1)

    if a[0] == 'multi_file_to_wide':
        files: list[str] = []
        value_col = 'y_data'
        conditions = None
        col_prefix = ''
        auto_prefix = False
        out_base = 'l2_participant_table'
        i = 1
        while i < len(a):
            if a[i] == '--value-col' and i + 1 < len(a):
                value_col = a[i + 1]; i += 2
            elif a[i] == '--conditions' and i + 1 < len(a):
                conditions = [c.strip() for c in a[i + 1].split(',')]; i += 2
            elif a[i] == '--col-prefix' and i + 1 < len(a):
                col_prefix = a[i + 1]; i += 2
            elif a[i] == '--auto-prefix':
                auto_prefix = True; i += 1
            elif a[i] == '--out-base' and i + 1 < len(a):
                out_base = a[i + 1]; i += 2
            else:
                files.append(a[i]); i += 1
        multi_file_to_wide(files, value_col, conditions, col_prefix, auto_prefix, out_base)
    else:
        pivot_process(
            a[0],
            a[1] if len(a) > 1 else 'long_to_wide',
            a[2] if len(a) > 2 else '',
            a[3] if len(a) > 3 else '',
            a[4] if len(a) > 4 else 'value',
        )
