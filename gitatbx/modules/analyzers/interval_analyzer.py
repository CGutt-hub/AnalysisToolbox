"""Interval Analyzer Module - Generic inter-event interval computation (Strict fail-fast implementation)."""
import polars as pl, numpy as np, sys, os

def log_info(msg: str):  print(f"[interval] INFO: {msg}")
def log_error(msg: str): print(f"[interval] ERROR: {msg}")

def analyze_intervals(ip: str, target_cols: list[str], metrics_mode: str) -> str:
    log_info(f"Interval analysis execution: {ip}")

    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    if metrics_mode.upper() not in ('ALL', 'SDNN', 'RMSSD'):
        log_error(f"Invalid metrics_mode '{metrics_mode}'. Must be 'ALL', 'SDNN', or 'RMSSD'.")
        sys.exit(1)

    try:
        df = pl.read_parquet(ip)
    except Exception as e:
        log_error(f"Failed to read Parquet dataset: {e}")
        sys.exit(1)

    if df.height == 0:
        log_error("Input dataset is empty.")
        sys.exit(1)

    if not target_cols:
        log_error("Target columns must be explicitly declared.")
        sys.exit(1)

    missing_cols = [c for c in target_cols if c not in df.columns]
    if missing_cols:
        log_error(f"Declared columns not found in Parquet dataset: {missing_cols}")
        sys.exit(1)

    event_col = target_cols[0]
    group_keys = [c for c in target_cols[1:] if c != 'time']
    time_cols = [c for c in target_cols[1:] if c == 'time']

    if not time_cols and 'sfreq' not in df.columns:
        log_error("Dataset must contain either an explicit 'time' column in target_cols or an 'sfreq' column.")
        sys.exit(1)
    
    time_col_name = time_cols[0] if time_cols else None
    
    sfreq = None
    if time_col_name is None:
        if 'sfreq' not in df.columns or df['sfreq'].null_count() > 0:
            log_error("Explicit 'sfreq' column missing or contains null values.")
            sys.exit(1)
        sfreq = float(df['sfreq'][0])
        if sfreq <= 0:
            log_error(f"Invalid sampling frequency sfreq={sfreq}")
            sys.exit(1)

    records = []
    if group_keys:
        grouped = df.group_by(group_keys)
        for keys, epoch_df in grouped:
            key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
            events = epoch_df[event_col].to_numpy()
            
            if len(events) < 2:
                log_error(f"Sub-group {key_dict} contains fewer than 2 events for interval estimation.")
                sys.exit(1)
            
            if time_col_name is not None:
                time_events = epoch_df.sort(event_col)[time_col_name].to_numpy()
                intervals = np.diff(time_events) * 1000.0
            else:
                intervals = np.diff(events) / sfreq * 1000.0

            if len(intervals) < 2:
                log_error(f"Sub-group {key_dict} has insufficient interval points.")
                sys.exit(1)

            sdnn_val = float(np.std(intervals, ddof=1))
            rmssd_val = float(np.sqrt(np.mean(np.diff(intervals) ** 2)))

            if metrics_mode.upper() in ('ALL', 'SDNN'):
                r_sdnn = key_dict.copy()
                r_sdnn['metric'] = 'SDNN'
                r_sdnn['value'] = sdnn_val
                records.append(r_sdnn)

            if metrics_mode.upper() in ('ALL', 'RMSSD'):
                r_rmssd = key_dict.copy()
                r_rmssd['metric'] = 'RMSSD'
                r_rmssd['value'] = rmssd_val
                records.append(r_rmssd)
    else:
        events = df[event_col].to_numpy()
        if len(events) < 2:
            log_error("Dataset contains fewer than 2 events for interval estimation.")
            sys.exit(1)

        if time_col_name is not None:
            time_events = df.sort(event_col)[time_col_name].to_numpy()
            intervals = np.diff(time_events) * 1000.0
        else:
            intervals = np.diff(events) / sfreq * 1000.0

        if len(intervals) < 2:
            log_error("Insufficient interval points generated.")
            sys.exit(1)

        if metrics_mode.upper() in ('ALL', 'SDNN'):
            records.append({'metric': 'SDNN', 'value': float(np.std(intervals, ddof=1))})
        if metrics_mode.upper() in ('ALL', 'RMSSD'):
            records.append({'metric': 'RMSSD', 'value': float(np.sqrt(np.mean(np.diff(intervals) ** 2)))})

    if not records:
        log_error("No interval records calculated.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_intervals.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) != 4:
        log_error("CRITICAL: Exact parameters required: <input.parquet> <target_cols_str> <metrics_mode>")
        sys.exit(1)

    ip = sys.argv[1]
    t_cols = [c.strip(" '\"\\") for c in sys.argv[2].split(',') if c.strip(" '\"\\")]
    mm_mode = sys.argv[3].strip(" '\"\\")

    analyze_intervals(ip, target_cols=t_cols, metrics_mode=mm_mode)