"""Condition Profile Processor - Pivot OLS condition betas from multiple sources into a wide table.

Takes multiple OLS parquet files (each in long format: condition × channel rows with beta column),
assigns a source prefix to each, and pivots to a wide table:

    condition | source1_channel1 | source1_channel2 | source2_channel1 | ...

This wide table (one row per condition) is ready for correl_analyzer to compute pairwise
Pearson r across sources — giving a within-participant cross-signal consistency measure.

Usage:
    condition_profile_processor.py <file1> <file2> ... <src1> <src2> ... [suffix=condition_profile]

Convention: positional args are split in half. First half = file paths, second half = source
names (same order). If odd arg count, last arg is the output suffix.

Example (3 sources):
    condition_profile_processor.py eeg_ols.parquet eda_ols.parquet hrv_ols.parquet eeg eda hrv condition_profile
"""
import polars as pl, sys, os, json

# Label map loaded once from VIS_LABEL_MAP env var (set by Nextflow from params.vis_label_map)
_VIS_MAP: dict[str, str] = json.loads(os.environ.get('VIS_LABEL_MAP', '{}'))
_VIS_MAP_LOWER = {k.lower(): v for k, v in _VIS_MAP.items()}

def _prettify(name: str) -> str:
    """Convert snake_case column names to human-readable labels using VIS_LABEL_MAP."""
    parts = name.split('_')
    parts = [_VIS_MAP_LOWER.get(p.lower(), p.capitalize()) for p in parts]
    return ' '.join(parts)

def log_info(msg):    print(f"[condition_profile] INFO: {msg}")
def log_warning(msg): print(f"[condition_profile] WARNING: {msg}")
def log_error(msg):   print(f"[condition_profile] ERROR: {msg}")


def condition_profile_process(files: list[str], sources: list[str]) -> str:
    output_suffix = 'condprof'
    if len(files) != len(sources):
        log_error(f"{len(files)} files but {len(sources)} source names — must match")
        sys.exit(1)
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
            sys.exit(1)

    log_info(f"Merging {len(files)} sources: {sources}")

    wide_dfs = []
    for fp, mod in zip(files, sources):
        df = pl.read_parquet(fp)
        required = {'condition', 'channel', 'beta'}
        missing = required - set(df.columns)
        if missing:
            log_error(f"{fp} is missing columns: {missing}"); sys.exit(1)

        # Pivot long→wide: one row per condition, channels become columns
        pivoted = df.pivot(values='beta', index='condition', on='channel')

        # Prefix every channel column with the modality name to avoid collisions
        rename_map = {c: f"{mod}_{c}" for c in pivoted.columns if c != 'condition'}
        pivoted = pivoted.rename(rename_map)
        wide_dfs.append(pivoted)
        log_info(f"  {mod}: {list(rename_map.values())}")

    # Inner-join all modalities on condition (3 rows if 3 conditions)
    merged = wide_dfs[0]
    for other in wide_dfs[1:]:
        merged = merged.join(other, on='condition', how='inner')

    n_signals = [c for c in merged.columns if c != 'condition']
    log_info(f"Wide table: {merged.shape[0]} conditions × {len(n_signals)} signals")
    log_info(f"Signals: {n_signals}")

    # Derive participant ID from the first input file (e.g. EV_002_..._ols.parquet → EV_002)
    import re as _re
    _pid_match = _re.match(r'^([A-Za-z]+_\d+)', os.path.basename(files[0]))
    _pid_prefix = (_pid_match.group(1) + '_') if _pid_match else ''
    out_file = f"{_pid_prefix}{output_suffix}.parquet"
    merged.write_parquet(out_file, compression='snappy')

    # Visualisation: grid of condition bars per signal (passed straight to interactive plotter)
    conditions = merged['condition'].to_list()
    vis_labels = [_prettify(s) for s in n_signals]
    vis_df = pl.DataFrame({
        'x_data':    [vis_labels],
        'y_data':    [[merged.filter(pl.col('condition') == cond)[n_signals].row(0) for cond in conditions]],
        'y_var':     [[[0.0] * len(n_signals) for _ in conditions]],
        'plot_type': ['grid'],
        'labels':    [conditions],
        'x_label':   ['Signal'],
        'y_label':   ['Condition Beta (a.u.)']
    })
    vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')

    print(f"[condition_profile] Output: {out_file}")
    print(f"[condition_profile] Created procedure visualization: {out_file.replace('.parquet', '_vis.parquet')}")
    return out_file


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 4 or len(args) % 2 != 0:
        print(
            '[condition_profile] Pivot OLS betas from multiple sources into a wide condition×signal table.\n'
            'Usage: condition_profile_processor.py <file1> [file2 ...] <src1> [src2 ...]\n'
            'Files and source names must be the same count (even total args).\n'
            'Example: condition_profile_processor.py eeg_ols.parquet eda_ols.parquet eeg eda'
        )
        sys.exit(1)

    n_each = len(args) // 2
    files_arg = args[:n_each]
    srcs_arg = args[n_each:]
    condition_profile_process(files_arg, srcs_arg)
