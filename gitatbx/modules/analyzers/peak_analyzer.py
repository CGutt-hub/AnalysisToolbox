"""Peak Analyzer Module - Generic latency and amplitude extraction (Strict fail-fast implementation)."""
import polars as pl, numpy as np, sys, os

def log_info(msg: str):  print(f"[peak] INFO: {msg}")
def log_error(msg: str): print(f"[peak] ERROR: {msg}")

def analyze_peaks(
    ip: str, 
    target_cols: list[str],
    method: str, 
    time_window_str: str,
    participant_col: str = 'participant_id',
    condition_col: str = 'condition',
    epoch_col: str = 'epoch_id',
    time_col: str = 'time'
) -> str:
    log_info(f"Peak analysis execution: {ip}, method={method}")
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file non-existent or empty: {ip}")
        sys.exit(1)

    if method not in ('max', 'min', 'max_abs'):
        log_error(f"Unsupported peak extraction method '{method}'. Must be 'max', 'min', or 'max_abs'.")
        sys.exit(1)

    df = pl.read_parquet(ip)
    if df.height == 0:
        log_error("Input dataset contains zero rows.")
        sys.exit(1)

    if not target_cols:
        log_error("Target signal columns (target_cols) must be explicitly declared.")
        sys.exit(1)

    missing_cols = [c for c in target_cols if c not in df.columns]
    if missing_cols:
        log_error(f"Declared target signal columns missing from dataset: {missing_cols}")
        sys.exit(1)

    if time_col not in df.columns:
        log_error(f"Required time column '{time_col}' missing from dataset.")
        sys.exit(1)

    for ch in target_cols:
        if df[ch].null_count() > 0 or np.isnan(df[ch].to_numpy()).any():
            log_error(f"Null or NaN values detected in channel column '{ch}'. Imputation disabled.")
            sys.exit(1)

    g_keys = [c for c in [participant_col, condition_col, epoch_col] if c in df.columns]

    t_start, t_stop = None, None
    if time_window_str.upper() != 'NONE':
        if ',' not in time_window_str:
            log_error(f"Invalid time_window format '{time_window_str}'. Must be 'low,high' or 'NONE'.")
            sys.exit(1)
        parts = time_window_str.split(',')
        try:
            t_start, t_stop = float(parts[0]), float(parts[1])
        except ValueError as e:
            log_error(f"Failed to parse numeric time window bound values: {e}")
            sys.exit(1)

    records = []
    if g_keys:
        grouped = df.group_by(g_keys)
        for keys, sub_df in grouped:
            key_dict = dict(zip(g_keys, keys if isinstance(keys, tuple) else [keys]))
            times = sub_df[time_col].to_numpy()

            mask = (times >= t_start) & (times <= t_stop) if t_start is not None and t_stop is not None else np.ones(len(times), dtype=bool)

            if not np.any(mask):
                log_error(f"Time window {time_window_str} contains no time samples for sub-group {key_dict}.")
                sys.exit(1)

            for ch in target_cols:
                data = sub_df[ch].to_numpy()
                masked_data = data[mask]
                masked_times = times[mask]

                if method == 'max':
                    idx = np.argmax(masked_data)
                elif method == 'min':
                    idx = np.argmin(masked_data)
                elif method == 'max_abs':
                    idx = np.argmax(np.abs(masked_data))

                row = key_dict.copy()
                row['channel'] = ch
                row['latency'] = float(masked_times[idx])
                row['amplitude'] = float(masked_data[idx])
                row['method'] = method
                records.append(row)
    else:
        times = df[time_col].to_numpy()
        mask = (times >= t_start) & (times <= t_stop) if t_start is not None and t_stop is not None else np.ones(len(times), dtype=bool)
        if not np.any(mask):
            log_error(f"Time window {time_window_str} contains no time samples.")
            sys.exit(1)

        for ch in target_cols:
            data = df[ch].to_numpy()[mask]
            masked_times = times[mask]
            idx = np.argmax(np.abs(data)) if method == 'max_abs' else (np.argmax(data) if method == 'max' else np.argmin(data))
            records.append({'channel': ch, 'latency': float(masked_times[idx]), 'amplitude': float(data[idx]), 'method': method})

    if not records:
        log_error("No peaks extracted from dataset.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_peak.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')

    log_info(f"Peak extraction output saved: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) != 5:
        log_error("CRITICAL: Exact parameters required: <epochs.parquet> <target_cols_str> <method> <time_window_str>")
        sys.exit(1)

    ip = sys.argv[1]
    t_cols = [c.strip(" '\"\\") for c in sys.argv[2].split(',') if c.strip(" '\"\\")]
    method = sys.argv[3].strip(" '\"\\")
    tw_str = sys.argv[4].strip(" '\"\\")

    analyze_peaks(ip, target_cols=t_cols, method=method, time_window_str=tw_str)