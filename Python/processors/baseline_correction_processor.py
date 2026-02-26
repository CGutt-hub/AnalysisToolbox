"""Baseline Correction Processor - Subtract baseline mean from signals."""
import polars as pl, sys, os

# Logging helpers
def log_info(msg): print(f"[baseline_correction] INFO: {msg}")
def log_warning(msg): print(f"[baseline_correction] WARNING: {msg}")
def log_error(msg): print(f"[baseline_correction] ERROR: {msg}")

def baseline_correct(ip: str, baseline_sec: float = 5.0, sfreq: float | None = None) -> str:
    """Subtract mean of first N seconds from each channel. Generic for any time series."""
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    print(f"[baseline_correction] Baseline correction: {ip}, baseline={baseline_sec}s")
    df = pl.read_parquet(ip)
    data_cols = [c for c in df.columns if c not in ['time', 'sfreq', 'condition', 'epoch_id']]
    if not data_cols: log_error("No data columns found"); sys.exit(1)
    fs = sfreq or (float(df['sfreq'].head(1).item()) if 'sfreq' in df.columns else 1.0)  # type: ignore[arg-type]
    n_baseline = int(baseline_sec * fs)
    
    # Quality check: baseline period too short or too long
    total_samples = len(df)
    if n_baseline < 10:
        log_warning(f"Baseline period very short ({n_baseline} samples), baseline correction may be unreliable")
    elif n_baseline > total_samples * 0.5:
        log_warning(f"Baseline period ({n_baseline} samples) is >50% of total ({total_samples}), consider reducing baseline_sec")
    print(f"[baseline_correction] Using first {n_baseline} samples as baseline ({len(data_cols)} channels)")
    result = df.with_columns([(pl.col(c) - pl.col(c).head(n_baseline).mean()).alias(c) for c in data_cols])
    out_file = ip.replace('.parquet', '_bl.parquet')
    result.write_parquet(out_file)
    
    # Generate inline visualization
    if 'time' in result.columns and data_cols:
        time_data: list[float] = result['time'].to_list()
        y_data: list[list[float]] = [result[col].to_list() for col in data_cols]
        if len(time_data) > 10000:
            step: int = len(time_data) // 10000
            time_data = time_data[::step]
            y_data = [yd[::step] for yd in y_data]
        vis_df = pl.DataFrame({
            'x_data': [[[time_data] for _ in range(len(data_cols))]],
            'y_data': [y_data],
            'plot_type': ['line'],
            'labels': [data_cols],
            'x_label': ['Time (s)'],
            'y_label': ['Amplitude']
        })
        vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'))
    
    print(f"[baseline_correction] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: baseline_correct(a[1], float(a[2]) if len(a) > 2 else 5.0, float(a[3]) if len(a) > 3 else None) if len(a) >= 2 else (print('[baseline_correction] Subtract mean of first N seconds from each channel.\nUsage: baseline_correction_processor.py <input.parquet> [baseline_sec=5.0] [sfreq]'), sys.exit(1)))(sys.argv)
