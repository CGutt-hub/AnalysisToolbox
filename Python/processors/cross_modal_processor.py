"""Cross-Modal Processor - Pivot OLS condition betas from multiple modalities into a wide table.

Takes multiple OLS parquet files (each in long format: condition × channel rows with beta column),
assigns a modality prefix to each, and pivots to a wide table:

    condition | modality1_channel1 | modality1_channel2 | modality2_channel1 | ...

This wide table (one row per condition) is ready for correl_analyzer to compute pairwise
Pearson r across modalities — giving a within-participant cross-modal consistency measure.

Usage:
    cross_modal_processor.py <file1> <file2> ... <mod1> <mod2> ... [suffix=cross_modal]

Convention: positional args are split in half. First half = file paths, second half = modality
names (same order). If odd arg count, last arg is the output suffix.

Example (3 modalities):
    cross_modal_processor.py eeg_ols.parquet eda_ols.parquet hrv_ols.parquet eeg eda hrv cross_modal
"""
import polars as pl, sys, os


def log_info(msg):    print(f"[cross_modal] INFO: {msg}")
def log_warning(msg): print(f"[cross_modal] WARNING: {msg}")
def log_error(msg):   print(f"[cross_modal] ERROR: {msg}")


def cross_modal_process(files: list[str], modalities: list[str], output_suffix: str = 'cross_modal') -> str:
    if len(files) != len(modalities):
        log_error(f"{len(files)} files but {len(modalities)} modality names — must match")
        sys.exit(1)
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
            sys.exit(1)

    log_info(f"Merging {len(files)} modalities: {modalities}")

    wide_dfs = []
    for fp, mod in zip(files, modalities):
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

    out_file = f"{output_suffix}.parquet"
    merged.write_parquet(out_file, compression='snappy')

    # Visualisation: grid of condition bars per signal (passed straight to interactive plotter)
    conditions = merged['condition'].to_list()
    vis_df = pl.DataFrame({
        'x_data':    [n_signals],
        'y_data':    [[merged.filter(pl.col('condition') == cond)[n_signals].row(0) for cond in conditions]],
        'y_var':     [[[0.0] * len(n_signals) for _ in conditions]],
        'plot_type': ['grid'],
        'labels':    [conditions],
        'x_label':   ['Signal'],
        'y_label':   ['Condition Beta (a.u.)']
    })
    vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')

    print(f"[cross_modal] Output: {out_file}")
    print(f"[cross_modal] Created procedure visualization: {out_file.replace('.parquet', '_vis.parquet')}")
    return out_file


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 4:
        print(
            '[cross_modal] Pivot OLS betas from multiple modalities into a wide condition×signal table.\n'
            'Usage: cross_modal_processor.py <file1> [file2 ...] <mod1> [mod2 ...] [suffix]\n'
            'Files and modality names must be the same count.\n'
            'If total arg count is odd, the last arg is treated as the output suffix.\n'
            'Example: cross_modal_processor.py eeg_ols.parquet eda_ols.parquet eeg eda cross_modal'
        )
        sys.exit(1)

    # Odd arg count → last arg is suffix
    has_suffix = (len(args) % 2 == 1)
    suffix = args[-1] if has_suffix else 'cross_modal'
    core = args[:-1] if has_suffix else args
    n_each = len(core) // 2
    files_arg = core[:n_each]
    mods_arg = core[n_each:]
    cross_modal_process(files_arg, mods_arg, suffix)
