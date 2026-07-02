import polars as pl, sys, os, re

# Logging helpers
def log_info(msg): print(f"[concatenating] INFO: {msg}")
def log_warning(msg): print(f"[concatenating] WARNING: {msg}")
def log_error(msg): print(f"[concatenating] ERROR: {msg}")

def extract_pid(filepath: str) -> str:
    """Extract participant ID from filepath (e.g. EV_002, EV2_01, DEAP_01)."""
    basename = os.path.basename(filepath)
    match = re.match(r'^([A-Za-z][A-Za-z0-9]*_\d+)', basename)
    return match.group(1) if match else ''

def _is_signal_file(df: pl.DataFrame) -> bool:
    """True when the file is a Nextflow signal pointer, not actual data."""
    return 'signal' in df.columns and 'folder_path' in df.columns and 'x_data' not in df.columns and 'y_data' not in df.columns


def _resolve_signal(df: pl.DataFrame, original_path: str) -> pl.DataFrame:
    """Load actual data from the folder_path recorded in a signal file.
    
    Picks the single parquet file in that folder whose name most closely
    matches the signal file's own basename (same stem prefix).
    Falls back to the first parquet found.
    """
    folder = str(df['folder_path'][0])
    if not os.path.isdir(folder):
        log_warning(f"Signal folder not found: {folder}, falling back to signal file itself")
        return df
    candidates = sorted([
        os.path.join(folder, fn)
        for fn in os.listdir(folder)
        if fn.endswith('.parquet')
    ])
    if not candidates:
        log_warning(f"No parquet files in signal folder: {folder}")
        return df
    # Prefer the file whose name shares the longest prefix with the original filename
    base = os.path.splitext(os.path.basename(original_path))[0]
    best = max(candidates, key=lambda p: len(os.path.commonprefix([base, os.path.basename(p)])))
    log_info(f"Signal resolved: {os.path.basename(original_path)} -> {best}")
    return pl.read_parquet(best)


def concat_generic(files: list[str], conds: list[str]) -> pl.DataFrame:
    """
    Generic concatenation — two modes depending on input shape:

    MODE A – plot-ready inputs (have x_data + y_data):
        Per-condition single-row dataframes are merged into one multi-condition row.
        List fields (y_data, y_var, …) become lists-of-lists; metadata is taken from
        the first file; a 'labels' column is added.  Output is a single-row parquet
        ready for the plotter.

    MODE B – raw / long-format inputs (everything else):
        Files are row-bound via pl.concat.  Long-format (region/channel + value) is
        auto-converted to bar-plot-ready before stacking.
        Signal pointer files (folder_path column) are resolved to their actual data
        before processing.
    """
    print(f"[concatenating] Concatenating {len(files)} files")

    # ── helpers ────────────────────────────────────────────────────────────────

    def load_df(f: str, fallback_cond: str) -> pl.DataFrame:
        """Load one input; resolve signal pointers; auto-convert long-format."""
        df = pl.read_parquet(f)

        # Resolve signal pointer files
        if _is_signal_file(df):
            df = _resolve_signal(df, f)

        # Auto-convert long-format (region/channel + value/sem) → bar plot-ready
        region_col = next((c for c in ('region', 'channel') if c in df.columns), None)
        if region_col and 'value' in df.columns and 'x_data' not in df.columns:
            agg = df.group_by(region_col).agg([
                pl.col('value').mean().alias('value'),
                *([] if 'sem' not in df.columns else [pl.col('sem').mean().alias('sem')])
            ]).sort(region_col)
            cond_name = str(df['condition'][0]) if 'condition' in df.columns else fallback_cond
            log_info(f"Long-format auto-converted: {cond_name}, {agg.height} regions")
            return pl.DataFrame([{
                'condition': cond_name,
                'x_data':   agg[region_col].to_list(),
                'y_data':   agg['value'].to_list(),
                'y_var':    agg['sem'].to_list() if 'sem' in agg.columns else [0.0] * agg.height,
                'plot_type': 'bar',
                'x_label':  region_col.capitalize(),
                'y_label':  str(df['y_label'][0]) if 'y_label' in df.columns else 'Amplitude',
            }])
        return df

    loaded = [load_df(f, conds[i] if i < len(conds) else f"cond{i+1}") for i, f in enumerate(files)]

    # ── guard: all inputs are unresolvable signal pointers (upstream had no data) ──
    all_unresolved = all(_is_signal_file(df) for df in loaded)
    if all_unresolved:
        log_warning("All inputs are unresolved signal pointers — no upstream data, writing empty placeholder")
        return pl.DataFrame({
            'condition': ['no_data'],
            'x_data': [[]],
            'y_data': [[]],
            'y_var': [[]],
            'plot_type': ['bar'],
            'x_label': ['Condition'],
            'y_label': ['Value'],
        })

    # ── decide mode ─────────────────────────────────────────────────────────────
    is_plot_ready = all('x_data' in df.columns and 'y_data' in df.columns for df in loaded)

    # ── MODE B: raw row-bind ────────────────────────────────────────────────────
    if not is_plot_ready:
        log_info("Raw/mixed inputs detected — performing row-bind concatenation")
        # If the caller supplied explicit labels (label:path syntax), inject them as a
        # 'source' column so downstream scripts (e.g. anova_analyzer group_by) can
        # distinguish rows by origin.
        label_injected = [c for c in conds if c and not c.startswith('cond')]
        if label_injected and len(label_injected) == len(loaded):
            labelled = [
                df.with_columns(pl.lit(label).alias('source'))
                for df, label in zip(loaded, conds)
            ]
            result_df = pl.concat(labelled, how='diagonal')
            log_info(f"Injected 'source' column with labels: {conds}")
        else:
            result_df = pl.concat(loaded, how='diagonal')
        print(f"[concatenating] Row-bound {len(loaded)} files -> {result_df.height} rows, cols: {result_df.columns}")
        return result_df

    # ── MODE A: plot-ready single-row merge ─────────────────────────────────────
    all_rows = [df.to_dicts()[0] for df in loaded]
    first_row = all_rows[0]

    labels = [row.get('condition', conds[i] if i < len(conds) else f'cond{i+1}') for i, row in enumerate(all_rows)]
    print(f"[concatenating] Labels extracted: {labels}")

    # List fields to aggregate across conditions (exclude shared metadata lists)
    metadata_list_fields = {'y_labels'}
    plot_type = first_row.get('plot_type', '')
    if plot_type in ('grid', 'bar'):
        metadata_list_fields.add('x_data')

    list_fields = [
        k for k, v in first_row.items()
        if isinstance(v, (list, tuple)) and k not in metadata_list_fields
    ]
    metadata_fields = {k: v for k, v in first_row.items()
                       if k not in list_fields and k != 'condition'}
    for k in metadata_list_fields:
        if k in first_row:
            metadata_fields[k] = first_row[k]

    print(f"[concatenating] List fields (to aggregate): {list_fields}")
    print(f"[concatenating] Metadata fields: {list(metadata_fields.keys())}")

    aggregated = {field: [row.get(field, []) for row in all_rows] for field in list_fields}
    aggregated['labels'] = labels

    # Unwrap over-nested single-element lists so plotter gets consistent depth
    def normalize_nested(data):
        if not data or not isinstance(data, list):
            return data
        result = []
        for item in data:
            while isinstance(item, list) and len(item) == 1 and isinstance(item[0], list):
                item = item[0]
            result.append(item)
        return result

    for field in ('y_data', 'y_var', 'ci_lower', 'ci_upper'):
        if field in aggregated:
            aggregated[field] = normalize_nested(aggregated[field])

    # Flatten bar charts: merge per-condition single values into one series
    # Input:  y_data=[[5.2],[4.8],[5.0]], labels=['NEG','NEU','POS'], x_data=['NEG']
    # Output: y_data=[5.2,4.8,5.0], x_data=['NEG','NEU','POS'] (no labels needed)
    if plot_type == 'bar' and 'y_data' in aggregated:
        y_list = aggregated['y_data']
        if all(isinstance(v, list) and len(v) == 1 for v in y_list):
            metadata_fields['x_data'] = labels
            aggregated['y_data'] = [v[0] for v in y_list]
            for field in ('y_var', 'ci_lower', 'ci_upper'):
                if field in aggregated:
                    aggregated[field] = [
                        v[0] if isinstance(v, list) and len(v) == 1 else v
                        for v in aggregated[field]
                    ]
            del aggregated['labels']
            log_info(f"Flattened bar chart: {len(y_list)} conditions -> x_data={metadata_fields['x_data']}")

    return pl.DataFrame([{**metadata_fields, **aggregated}])


def _parse_items(items: list[str]) -> tuple[list[str], list[str]]:
    """Parse CLI items into (files, labels).

    Supports three syntaxes:
      1. labels=EDA,HRV,...  file1 file2 ...   – explicit labels= token (IOInterface-friendly)
      2. EDA:file1 HRV:file2 ...               – label:path inline syntax
      3. file1 file2 ...                       – no labels; derived from basename suffix
    """
    label_arg = next((p for p in items if p.startswith('labels=')), None)
    if label_arg:
        files  = [p for p in items if not p.startswith('labels=')]
        labels = label_arg.split('=', 1)[1].split(',')
    elif items and ':' in items[0]:
        files  = [p.split(':', 1)[1] for p in items]
        labels = [p.split(':', 1)[0] for p in items]
    else:
        files  = items
        labels = [os.path.splitext(os.path.basename(p))[0].rsplit('_', 1)[-1] for p in items]
    return files, labels


if __name__ == '__main__':
    _a = sys.argv
    if len(_a) < 3:
        print(
            f"Aggregate multiple condition parquets into single plot-ready output.\n"
            f"[concatenating] Usage: python {_a[0]} <path1> <path2> ... <out_basename> "
            f"OR <label1:path1> <label2:path2> ... <out_basename> "
            f"OR labels=L1,L2,... <path1> <path2> ... <out_basename>"
        )
        sys.exit(1)
    _items, _out_base = _a[1:-1], _a[-1]
    _files, _labels = _parse_items(_items)
    _pid = extract_pid(_files[0]) if _files else ''
    _out_path = os.path.join(
        os.getcwd(),
        f"{_pid + '_' if _files and _pid else ''}{_out_base}.parquet",
    )
    _result = concat_generic(_files, _labels)
    _result.write_parquet(_out_path, compression='gzip')
    print(f"[concatenating] Concatenated {len(_files)} files -> {_out_path}")
    print(_out_path)
