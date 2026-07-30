"""Spectrum Analyzer Module - Generic Welch PSD spectral estimation (Strict fail-fast implementation with explicit target/metadata columns)."""
import polars as pl, numpy as np, sys, ast, os
from scipy.signal import welch as scipy_welch

def log_info(msg: str):  print(f"[spectrum] INFO: {msg}")
def log_error(msg: str): print(f"[spectrum] ERROR: {msg}")

def compute_spectrum(ip: str, 
                     target_cols: list[str], 
                     max_freq: float = 45.0) -> str:
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"File not found or empty: {ip}")
        sys.exit(1)

    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if df.height == 0:
        log_error("Input dataframe is empty. Cannot process spectrum.")
        sys.exit(1)

    if not target_cols:
        log_error("Target columns list must be explicitly declared.")
        sys.exit(1)

    missing_targets = [c for c in target_cols if c not in df.columns]
    if missing_targets:
        log_error(f"Declared target/metadata columns not found in dataset: {missing_targets}")
        sys.exit(1)

    # Strict contract: target_cols contains [channels/regions..., condition, epoch_id, time]
    # Let's extract the time and grouping metadata columns explicitly from target_cols definition
    time_col = [c for c in target_cols if c == 'time']
    if not time_col:
        log_error("Explicit 'time' column must be included in target_cols.")
        sys.exit(1)
    time_col_name = time_col[0]

    group_keys = [c for c in target_cols if c in ('condition', 'epoch_id', 'participant_id')]
    channels = [c for c in target_cols if c not in group_keys and c != time_col_name]

    if not channels:
        log_error("No signal channels or regions specified within target_cols.")
        sys.exit(1)

    if epoch_col_name := next((c for c in group_keys if c == 'epoch_id'), None):
        first_epoch_val = df[epoch_col_name][0]
        first_epoch = df.filter(pl.col(epoch_col_name) == first_epoch_val)
        times = first_epoch[time_col_name].to_numpy()
    else:
        times = df[time_col_name].to_numpy()

    if len(times) < 2:
        log_error("Insufficient time-series points to resolve sampling frequency.")
        sys.exit(1)

    dt_series = np.diff(times)
    if np.any(dt_series <= 0):
        log_error("Non-positive or non-monotonic time steps detected in time series.")
        sys.exit(1)

    dt = float(dt_series[0])
    sfreq = 1.0 / dt
    n_times = len(times)
    nperseg = min(int(sfreq), n_times // 2)

    if nperseg < 4:
        log_error(f"Segment length (nperseg={nperseg}) too small for spectral estimation.")
        sys.exit(1)

    _f_ref = scipy_welch(np.zeros(n_times), fs=sfreq, nperseg=nperseg)[0]
    fmask = (_f_ref >= 1.0) & (_f_ref <= max_freq)
    freq_vals = _f_ref[fmask]

    records = []
    
    if group_keys:
        grouped = df.group_by(group_keys)
        for keys, epoch_df in grouped:
            key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
            for ch in channels:
                signal_arr = epoch_df[ch].to_numpy()
                _, psd = scipy_welch(signal_arr, fs=sfreq, nperseg=nperseg)
                for f, p in zip(freq_vals, psd[fmask]):
                    row = key_dict.copy()
                    row['channel'] = ch
                    row['frequency'] = float(f)
                    row['power'] = float(p)
                    records.append(row)
    else:
        for ch in channels:
            signal_arr = df[ch].to_numpy()
            _, psd = scipy_welch(signal_arr, fs=sfreq, nperseg=nperseg)
            for f, p in zip(freq_vals, psd[fmask]):
                records.append({'channel': ch, 'frequency': float(f), 'power': float(p)})

    if not records:
        log_error("No PSD estimates were computed.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_spectrum.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    a = sys.argv
    if len(a) < 3:
        log_error("Usage: python spectrum_analyzer.py <input.parquet> <target_cols_list> [max_freq]")
        sys.exit(1)

    epochs = a[1]
    
    try:
        target_cols = ast.literal_eval(a[2])
    except Exception as e:
        log_error(f"Failed to parse target columns argument: {e}")
        sys.exit(1)

    max_freq = float(a[3]) if len(a) > 3 and a[3] not in ('None', 'null', '') else 45.0

    compute_spectrum(epochs, target_cols=target_cols, max_freq=max_freq)