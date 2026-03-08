"""Peak Detection Processor - Detect peaks in any signal column using configurable methods."""
import polars as pl, numpy as np, sys, os
from scipy.signal import find_peaks
from numpy.typing import NDArray
import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Logging helpers
def log_info(msg): print(f"[peak_detection] INFO: {msg}")
def log_warning(msg): print(f"[peak_detection] WARNING: {msg}")
def log_error(msg): print(f"[peak_detection] ERROR: {msg}")

def detect_peaks(ip: str, column: str, fs: float, method: str = 'scipy', height: float | None = None, distance: float | None = None) -> str:
    """Detect peaks in signal. Methods: 'scipy' (general), 'ecg' (uses neurokit2 if available)."""
    print(f"[peak_detection] Peak detection: {ip}, column={column}, method={method}")
    df = pl.read_parquet(ip)
    if column not in df.columns:
        # Auto-detect by pattern
        target = next((c for c in df.columns if column.lower() in c.lower()), None)
        if not target: log_error(f"Column not found: {column}"); sys.exit(1)
        column = target
    sig: NDArray[np.float64] = df[column].to_numpy()
    time_offset = float(df['time'][0]) if 'time' in df.columns else 0.0
    print(f"[peak_detection] Detecting peaks in {column}: {len(sig)} samples")
    
    peaks: NDArray[np.int64]
    if method == 'ecg':
        try:
            import neurokit2 as nk
            peaks_dict = nk.ecg_findpeaks(sig, sampling_rate=int(fs))
            peaks = np.array(peaks_dict['ECG_R_Peaks'], dtype=np.int64)
        except ImportError:
            print("[peak_detection] neurokit2 not available, falling back to scipy")
            kwargs = {}
            if height is not None: kwargs['height'] = height
            if distance is not None: kwargs['distance'] = int(distance * fs)
            peaks, _ = find_peaks(sig, **kwargs)
            peaks = peaks.astype(np.int64)
    else:  # scipy
        kwargs = {}
        if height is not None: kwargs['height'] = height
        if distance is not None: kwargs['distance'] = int(distance * fs)
        peaks, _ = find_peaks(sig, **kwargs)
        peaks = peaks.astype(np.int64)
    
    result = pl.DataFrame({'peak_sample': peaks, 'time': time_offset + peaks / fs, 'sfreq': [fs] * len(peaks)})
    
    # Quality check: very few peaks detected
    if len(peaks) < 10:
        log_warning(f"Only {len(peaks)} peaks detected, check signal quality or detection parameters")
    elif len(peaks) > len(sig) * 0.5:
        log_warning(f"{len(peaks)} peaks detected ({len(peaks)/len(sig)*100:.1f}% of samples), may be over-detecting")
    
    out_file = ip.replace('.parquet', '_peaks.parquet')
    result.write_parquet(out_file, compression='snappy')
    print(f"[peak_detection] Output: {out_file} ({len(peaks)} peaks)")
    
    # Generate inline visualization: interval time series
    if len(peaks) > 1:
        intervals: np.ndarray = np.diff(peaks) / fs * 1000  # Convert to ms
        interval_times: list[float] = (time_offset + peaks[1:] / fs).tolist()
        intervals_list: list[float] = intervals.tolist()
        if len(intervals_list) > 10000:
            step: int = len(intervals_list) // 10000
            interval_times = interval_times[::step]
            intervals_list = intervals_list[::step]
        vis_df = pl.DataFrame({
            'x_data': [[interval_times]],
            'y_data': [[intervals_list]],
            'plot_type': ['line'],
            'labels': [['Interval (ms)']],
            'x_label': ['Time (s)'],
            'y_label': ['Interval (ms)']
        })
        vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')
    
    return out_file

if __name__ == '__main__': (lambda a: detect_peaks(a[1], a[2], float(a[3]), a[4] if len(a) > 4 else 'scipy', float(a[5]) if len(a) > 5 and a[5] else None, float(a[6]) if len(a) > 6 and a[6] else None) if len(a) >= 4 else (print('[peak_detection] Detect peaks in signal using scipy or neurokit2 (ECG R-peaks).\nUsage: peak_detection_processor.py <input.parquet> <column> <fs> [method=scipy|ecg] [height] [distance_sec]'), sys.exit(1)))(sys.argv)
