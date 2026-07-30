"""Peak Analyzer Module - Generic latency and amplitude extraction (Strict fail-fast implementation)."""
import polars as pl, numpy as np, sys, os, ast

def log_info(msg: str):  print(f"[peak] INFO: {msg}")
def log_error(msg: str): print(f"[peak] ERROR: {msg}")

def analyze_peaks(
    ip: str, 
    target_cols: list[str],
    method: str = 'max_abs', 
    time_window: str | tuple | None = None,
    participant_col: str = 'participant_id',
    condition_col: str = 'condition',
    epoch_col: str = 'epoch_id',
    time_col: str = 'time'
) -> str:
    log_info(f"Peak analysis execution: {ip}, method={method}")
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file non-existent or empty: {ip}")
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

    g_keys = [c for c in [participant_col, condition_col, epoch_col] if c in df.columns]

    t_start, t_stop = None, None
    if isinstance(time_window, str) and ',' in time_window:
        parts = time_window.split(',')
        t_start, t_stop = float(parts[0]), float(parts[1])
    elif isinstance(time_window, (tuple, list)) and len(time_window) == 2:
        t_start, t_stop = float(time_window[0]), float(time_window[1])

    records = []
    if g_keys:
        grouped = df.group_by(g_keys)
        for keys, sub_df in grouped:
            key_dict = dict(zip(g_keys, keys if isinstance(keys, tuple) else [keys]))
            times = sub_df[time_col].to_numpy()

            if t_start is not None and t_stop is not None:
                mask = (times >= t_start) & (times <= t_stop)
            else:
                mask = np.ones(len(times), dtype=bool)

            if not np.any(mask):
                log_error(f"Time window {time_window} contains no time samples for sub-group {key_dict}.")
                sys.exit(1)

            for ch in target_cols:
                data = sub_df[ch].to_numpy()
                masked_data = data[mask]
                masked_times = times[mask]

                if len(masked_data) == 0:
                    log_error(f"Sub-group {key_dict} column '{ch}' has empty sample slice.")
                    sys.exit(1)

                if method == 'max':
                    idx = np.argmax(masked_data)
                elif method == 'min':
                    idx = np.argmin(masked_data)
                elif method == 'max_abs':
                    idx = np.argmax(np.abs(masked_data))
                else:
                    log_error(f"Unknown peak extraction method: '{method}'")
                    sys.exit(1)

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
            log_error(f"Time window {time_window} contains no time samples.")
            sys.exit(1)

        for ch in target_cols:
            data = df[ch].to_numpy()[mask]
            masked_times = times[mask]
            if len(data) == 0:
                log_error(f"Column '{ch}' has empty sample slice.")
                sys.exit(1)
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
    args = sys.argv
    if len(args) >= 3:
        raw_targets = args[2]
        if raw_targets.startswith('[') and raw_targets.endswith(']'):
            t_cols = ast.literal_eval(raw_targets)
        else:
            t_cols = [c.strip() for c in raw_targets.split(',') if c.strip()]

        analyze_peaks(
            ip=args[1], 
            target_cols=t_cols,
            method=args[3] if len(args) > 3 and args[3] not in ('None', '') else 'max_abs',
            time_window=args[4] if len(args) > 4 and args[4] not in ('None', '') else None
        )
    else:
        log_error("Usage: python peak_analyzer.py <epochs.parquet> <target_cols_list_or_comma_str> [method] [time_window]")
        sys.exit(1)