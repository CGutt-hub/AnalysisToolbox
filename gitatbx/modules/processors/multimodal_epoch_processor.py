"""Multimodal Epoch Processor - Join EEG (trial-level) and HRV (bootstrap sub-window level).

EDA is excluded: it reacts on a timescale of seconds to minutes and is not
meaningful as a per-condition correlate alongside fast EEG band power.

HRV sub-window epoch_ids follow the pattern "{trial_epoch_id}_w{N}" produced by
the sliding-window epoching_processor. The parent trial epoch_id is recovered by
stripping the "_w{N}" suffix, allowing each HRV sub-window to be paired with the
EEG band power of its parent trial epoch.

This yields N = n_sub_windows x n_trials x n_conditions data points instead of
just n_conditions, making the Pearson correlations statistically meaningful.
"""
import polars as pl, sys, os

def log_info(msg): print(f"[multimodal] INFO: {msg}")
def log_error(msg): print(f"[multimodal] ERROR: {msg}")

def process(eeg_file: str, hrv_file: str) -> str:
    for f in [eeg_file, hrv_file]:
        if not os.path.exists(f): log_error(f"File not found: {f}"); sys.exit(1)

    eeg = pl.read_parquet(eeg_file)
    hrv = pl.read_parquet(hrv_file)

    # EEG: trial-level band power — keep condition, epoch_id, and band columns
    meta = {'condition', 'epoch_id', 'region', 'participant_id', 'sub_epoch_id', 'window_id'}
    band_cols = [c for c in eeg.columns if c not in meta]
    eeg_trials = eeg.select(['condition', 'epoch_id'] + band_cols)

    # HRV: sub-window epoch_ids look like "{trial_epoch_id}_w{N}"
    # Recover parent trial epoch_id by stripping the _w{N} suffix
    hrv_sub = (hrv
               .rename({'value': 'hrv'})
               .with_columns(
                   pl.col('epoch_id').str.replace(r'_w\d+$', '', literal=False).alias('parent_epoch_id')
               )
               .select(['condition', 'epoch_id', 'hrv', 'parent_epoch_id']))

    # Join each HRV sub-window to its parent trial's EEG band power
    joined = (hrv_sub
              .join(eeg_trials.rename({'epoch_id': 'parent_epoch_id'}),
                    on=['condition', 'parent_epoch_id'],
                    how='inner')
              .drop('parent_epoch_id'))

    if joined.height == 0:
        log_error("No rows after join — epoch_id may not follow the '{trial_id}_w{N}' pattern")
        sys.exit(1)

    log_info(f"Joined {joined.height} sub-window rows | EEG bands={band_cols}")
    log_info(f"Columns: {joined.columns}")

    base = os.path.splitext(os.path.basename(eeg_file))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_multimodal.parquet")
    joined.write_parquet(out_file, compression='gzip')
    print(f"[multimodal] Output: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 3:
        process(a[1], a[2])
    else:
        print('[multimodal] Join EEG (trial-level) + HRV (bootstrap sub-windows) for correlation.\nUsage: multimodal_epoch_processor.py <eeg.parquet> <hrv.parquet>')
        sys.exit(1)
