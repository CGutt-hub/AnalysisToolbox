"""Multimodal Epoch Processor - Join EEG, EDA, and HRV by aggregating to condition-level means."""
import polars as pl, sys, os

def log_info(msg): print(f"[multimodal] INFO: {msg}")
def log_error(msg): print(f"[multimodal] ERROR: {msg}")

def process(eeg_file: str, eda_file: str, hrv_file: str) -> str:
    for f in [eeg_file, eda_file, hrv_file]:
        if not os.path.exists(f): log_error(f"File not found: {f}"); sys.exit(1)

    eeg = pl.read_parquet(eeg_file)
    eda = pl.read_parquet(eda_file)
    hrv = pl.read_parquet(hrv_file)

    # Aggregate all modalities to condition-level means (avoids epoch_id alignment issues)
    # EDA/HRV: mean of 'value' per condition
    eda_agg = eda.group_by('condition').agg(pl.col('value').mean().alias('eda'))
    hrv_agg = hrv.group_by('condition').agg(pl.col('value').mean().alias('hrv'))

    # EEG: mean of each band per condition (drop non-band columns)
    meta = {'condition', 'epoch_id', 'region', 'participant_id', 'sub_epoch_id', 'window_id'}
    band_cols = [c for c in eeg.columns if c not in meta]
    eeg_agg = eeg.group_by('condition').agg([pl.col(c).mean() for c in band_cols])

    # Join on condition
    joined = (eeg_agg
              .join(eda_agg, on='condition', how='inner')
              .join(hrv_agg, on='condition', how='inner'))

    if joined.height == 0:
        log_error("No matching conditions after join — check condition labels between modalities")
        sys.exit(1)

    log_info(f"Joined {joined.height} conditions | EEG bands={band_cols}")
    log_info(f"Columns: {joined.columns}")

    base = os.path.splitext(os.path.basename(eeg_file))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_multimodal.parquet")
    joined.write_parquet(out_file, compression='snappy')
    print(f"[multimodal] Output: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    a = sys.argv
    if len(a) >= 4:
        process(a[1], a[2], a[3])
    else:
        print('[multimodal] Join EEG+EDA+HRV by condition-level means.\nUsage: multimodal_epoch_processor.py <eeg.parquet> <eda.parquet> <hrv.parquet>')
        sys.exit(1)
