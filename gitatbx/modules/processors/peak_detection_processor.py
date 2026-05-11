"""Peak Detection Processor — Generic peak detection for any signal or point process.

Detects peaks (maxima, R-peaks, blinks, events, etc.) in any signal column using
configurable methods (scipy, neurokit2 ECG). Automatically preserves epoch structure
when present, enabling reuse across different statistical workflows and signal types.

Methods:
  - 'scipy': General peak detection via scipy.signal.find_peaks
  - 'ecg': ECG-specific R-peak detection (uses neurokit2 if available, falls back to scipy)

Input flexibility:
  - Continuous signals: [signal_column, time, sfreq]
  - Epoched signals: [condition, epoch_id, signal_column, time, sfreq]
  
Output preserves input structure: epochs pass through unchanged.
"""
import polars as pl, numpy as np, sys, os
from scipy.signal import find_peaks
from numpy.typing import NDArray
import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Logging helpers
def log_info(msg): print(f"[peak_detection] INFO: {msg}")
def log_warning(msg): print(f"[peak_detection] WARNING: {msg}")
def log_error(msg): print(f"[peak_detection] ERROR: {msg}")

def _find_peaks_in_array(sig: 'NDArray[np.float64]', time_arr: 'NDArray[np.float64]', fs: float,
                          method: str, height: 'float | None', distance: 'float | None') -> 'NDArray[np.int64]':
    """Run peak detection on a 1-D array."""
    if method == 'ecg':
        try:
            import neurokit2 as nk
            peaks_dict = nk.ecg_findpeaks(sig, sampling_rate=int(fs))
            return np.array(peaks_dict['ECG_R_Peaks'], dtype=np.int64)
        except ImportError:
            print("[peak_detection] neurokit2 not available, falling back to scipy")
    kwargs: dict = {}
    if height is not None:
        kwargs['height'] = height
    if distance is not None:
        kwargs['distance'] = int(distance * fs)
    peaks, _ = find_peaks(sig, **kwargs)
    return peaks.astype(np.int64)


def detect_peaks(ip: str, column: str, fs: float, method: str = 'scipy', height: float | None = None, distance: float | None = None) -> str:
    """Detect peaks in signal. Works on any signal type: ECG, BVP, EEG, PPG, etc.
    
    Automatically preserves epoch structure if present, enabling reuse across
    different statistical workflows (bootstrap, correlation, group-level, etc.).
    
    Methods: 'scipy' (general), 'ecg' (uses neurokit2 if available, else scipy).
    """
    print(f"[peak_detection] Peak detection: {ip}, column={column}, method={method}")
    df = pl.read_parquet(ip)
    if column not in df.columns:
        # Auto-detect by pattern
        target = next((c for c in df.columns if column.lower() in c.lower()), None)
        if not target: log_error(f"Column not found: {column}"); sys.exit(1)
        column = target

    out_file = ip.replace('.parquet', '_peaks.parquet')
    is_epoched = 'condition' in df.columns and 'epoch_id' in df.columns

    if is_epoched:
        # Process per epoch so that interval_analyzer receives condition+epoch_id context
        epoch_rows: list[dict] = []
        total_peaks = 0
        conditions = sorted(df['condition'].unique().to_list())
        for cond in conditions:
            cond_df = df.filter(pl.col('condition') == cond)
            epoch_ids = sorted(cond_df['epoch_id'].unique().to_list())
            for eid in epoch_ids:
                ep = cond_df.filter(pl.col('epoch_id') == eid)
                sig = ep[column].to_numpy()
                t = ep['time'].to_numpy() if 'time' in ep.columns else np.arange(len(sig)) / fs
                local_peaks = _find_peaks_in_array(sig, t, fs, method, height, distance)
                for lp in local_peaks:
                    epoch_rows.append({
                        'condition': cond,
                        'epoch_id': eid,
                        'peak_sample': int(lp),
                        'time': float(t[lp]),
                        'sfreq': float(fs),
                    })
                    total_peaks += 1
        result = pl.DataFrame(epoch_rows) if epoch_rows else pl.DataFrame({
            'condition': pl.Series([], dtype=pl.Utf8),
            'epoch_id': pl.Series([], dtype=pl.Utf8),
            'peak_sample': pl.Series([], dtype=pl.Int64),
            'time': pl.Series([], dtype=pl.Float64),
            'sfreq': pl.Series([], dtype=pl.Float64),
        })
        print(f"[peak_detection] Per-epoch mode: {total_peaks} peaks across "
              f"{len(conditions)} conditions")
        if total_peaks < 10:
            log_warning(f"Only {total_peaks} peaks total, check signal quality or detection parameters")
    else:
        # Flat signal mode (no epoch structure)
        sig = df[column].to_numpy()
        time_arr = df['time'].to_numpy() if 'time' in df.columns else np.arange(len(sig)) / fs
        print(f"[peak_detection] Flat mode: {len(sig)} samples")
        peaks = _find_peaks_in_array(sig, time_arr, fs, method, height, distance)
        result = pl.DataFrame({'peak_sample': peaks.tolist(), 'time': time_arr[peaks].tolist(), 'sfreq': [fs] * len(peaks)})
        if len(peaks) < 10:
            log_warning(f"Only {len(peaks)} peaks detected, check signal quality or detection parameters")
        elif len(peaks) > len(sig) * 0.5:
            log_warning(f"{len(peaks)} peaks detected ({len(peaks)/len(sig)*100:.1f}% of samples), may be over-detecting")

    result.write_parquet(out_file, compression='snappy')
    print(f"[peak_detection] Output: {out_file} ({len(result)} peaks)")
    return out_file

if __name__ == '__main__': (lambda a: detect_peaks(a[1], a[2], float(a[3]), a[4] if len(a) > 4 else 'scipy', float(a[5]) if len(a) > 5 and a[5] and a[5] != 'None' else None, float(a[6]) if len(a) > 6 and a[6] and a[6] != 'None' else None) if len(a) >= 4 else (print('[peak_detection] Detect peaks in signal using scipy or neurokit2 (ECG R-peaks).\nUsage: peak_detection_processor.py <input.parquet> <column> <fs> [method=scipy|ecg] [height] [distance_sec]'), sys.exit(1)))(sys.argv)
