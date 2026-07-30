"""Peak Detection Processor — Generic peak detection for any signal or point process.
Strict fail-fast implementation matching amplitude_analyzer structure with a unified target columns list.
"""
import os, sys, ast, polars as pl, numpy as np
from scipy.signal import find_peaks

def log_info(msg: str):  print(f"[peak_detection] INFO: {msg}")
def log_error(msg: str): print(f"[peak_detection] ERROR: {msg}")

def detect_peaks(ip: str, 
                 target_cols: list[str], 
                 method: str = 'scipy', 
                 sfreq: float = 1000.0,
                 height: float | None = None,
                 distance: float | None = None) -> str:
    log_info(f"Peak detection execution: {ip}, method={method}")
    
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    try:
        df = pl.read_parquet(ip)
    except Exception as e:
        log_error(f"Failed to read Parquet dataset: {e}")
        sys.exit(1)

    if df.height == 0:
        log_error("Input dataset contains zero rows.")
        sys.exit(1)

    if not target_cols:
        log_error("Target columns must be explicitly declared.")
        sys.exit(1)

    missing_cols = [c for c in target_cols if c not in df.columns]
    if missing_cols:
        log_error(f"Declared columns not found in Parquet dataset: {missing_cols}")
        sys.exit(1)

    # Strict contract: target_cols must contain [ecg_signal, condition, epoch_id, time]
    # We extract the signal column as the first element, and explicit metadata keys from the rest.
    signal_col = target_cols[0]
    group_keys = [c for c in target_cols[1:] if c != 'time']
    time_col = [c for c in target_cols[1:] if c == 'time']

    if not time_col:
        log_error("Required 'time' column must be included in target_cols.")
        sys.exit(1)
    time_col = time_col[0]

    records = []
    
    def process_array(signal_data, time_axis, key_dict, sig_name):
        if len(signal_data) == 0 or np.isnan(signal_data).all():
            log_error(f"Empty or all-NaN signal data in column '{sig_name}'.")
            sys.exit(1)

        if method == 'ecg':
            try:
                import neurokit2 as nk
                peaks_dict = nk.ecg_findpeaks(signal_data, sampling_rate=int(sfreq))
                local_peaks = np.array(peaks_dict['ECG_R_Peaks'], dtype=np.int64)
            except ImportError:
                log_error("neurokit2 is required for 'ecg' method but is not available.")
                sys.exit(1)
        elif method == 'scipy':
            kwargs = {}
            if height is not None:
                kwargs['height'] = height
            if distance is not None:
                kwargs['distance'] = int(distance * sfreq)
            local_peaks, _ = find_peaks(signal_data, **kwargs)
            local_peaks = local_peaks.astype(np.int64)
        else:
            log_error(f"Unknown peak detection method: '{method}'.")
            sys.exit(1)

        for lp in local_peaks:
            row = key_dict.copy() if key_dict else {}
            row['channel'] = sig_name
            row['peak_sample'] = int(lp)
            row['time'] = float(time_axis[lp])
            row['sfreq'] = float(sfreq)
            records.append(row)

    if group_keys:
        grouped = df.group_by(group_keys)
        for keys, sub_epoch in grouped:
            key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
            signal_data = sub_epoch[signal_col].to_numpy()
            time_axis = sub_epoch[time_col].to_numpy()
            process_array(signal_data, time_axis, key_dict, signal_col)
    else:
        signal_data = df[signal_col].to_numpy()
        time_axis = df[time_col].to_numpy()
        process_array(signal_data, time_axis, {}, signal_col)

    if not records:
        log_error("No valid peak records detected.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_peaks.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')
    
    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

def _parse_optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    token = str(raw).strip().strip("\"").strip("'").lower()
    if token in {'', 'none', 'null', 'na', 'nan'}:
        return None
    return float(token)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log_error("Usage: python peak_detection_processor.py <input.parquet> <target_cols_list> [method] [sfreq] [height] [distance]")
        sys.exit(1)

    ip = sys.argv[1]
    
    raw_targets = sys.argv[2]
    if raw_targets.startswith('[') and raw_targets.endswith(']'):
        t_cols = ast.literal_eval(raw_targets)
    else:
        t_cols = [c.strip() for c in raw_targets.split(',') if c.strip()]

    method = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] not in ('None', 'null', '') else 'scipy'
    sfreq = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] not in ('None', 'null', '') else 1000.0
    height = _parse_optional_float(sys.argv[5]) if len(sys.argv) > 5 else None
    distance = _parse_optional_float(sys.argv[6]) if len(sys.argv) > 6 else None

    detect_peaks(ip, target_cols=t_cols, method=method, sfreq=sfreq, height=height, distance=distance)