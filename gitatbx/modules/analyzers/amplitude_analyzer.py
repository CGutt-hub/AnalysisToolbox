"""Amplitude Analyzer Module - Generic, participant-agnostic, strict fail-fast implementation with explicit target/metadata columns."""
import os, sys, ast, polars as pl, numpy as np

def log_info(msg: str):    print(f"[amplitude] INFO: {msg}")
def log_error(msg: str):   print(f"[amplitude] ERROR: {msg}")

def analyze_amplitude(ip: str, 
                      target_cols: list[str], 
                      method: str = 'peak_baseline', 
                      baseline_window: tuple = (-0.2, 0.0)) -> str:
    log_info(f"Amplitude analysis execution: {ip}, method={method}")
    
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
        log_error("Target columns list must be explicitly declared.")
        sys.exit(1)

    missing_targets = [c for c in target_cols if c not in df.columns]
    if missing_targets:
        log_error(f"Declared target/metadata columns not found in Parquet dataset: {missing_targets}")
        sys.exit(1)

    # Strict contract: target_cols contains [signal_cols..., condition, epoch_id, time]
    time_col = [c for c in target_cols if c == 'time']
    if not time_col:
        log_error("Required 'time' column must be included in target_cols.")
        sys.exit(1)
    time_col_name = time_col[0]

    group_keys = [c for c in target_cols if c in ('condition', 'epoch_id', 'participant_id')]
    signal_cols = [c for c in target_cols if c not in group_keys and c != time_col_name]

    if not signal_cols:
        log_error("No signal channels specified within target_cols.")
        sys.exit(1)

    records = []
    if group_keys:
        grouped = df.group_by(group_keys)
        for keys, sub_epoch in grouped:
            key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
            
            for signal_col in signal_cols:
                epoch_data = sub_epoch[signal_col].to_numpy()
                if len(epoch_data) == 0 or np.isnan(epoch_data).all():
                    log_error(f"Empty or all-NaN signal data in sub-group {key_dict}, column '{signal_col}'.")
                    sys.exit(1)

                time_axis = sub_epoch[time_col_name].to_numpy()
                
                if method == 'peak_baseline':
                    t_min, t_max = baseline_window
                    baseline_mask = (time_axis >= t_min) & (time_axis <= t_max)
                    if not np.any(baseline_mask):
                        log_error(f"Baseline window {baseline_window} contains no time samples for column '{signal_col}'.")
                        sys.exit(1)
                    baseline = np.mean(epoch_data[baseline_mask])
                    val = np.max(epoch_data) - baseline
                elif method == 'mean':
                    val = np.mean(epoch_data)
                elif method == 'peak':
                    val = np.max(epoch_data)
                else:
                    log_error(f"Unknown amplitude calculation method: '{method}'.")
                    sys.exit(1)

                row = key_dict.copy()
                row['channel'] = signal_col
                row['amplitude'] = float(val)
                row['method'] = method
                records.append(row)
    else:
        for signal_col in signal_cols:
            epoch_data = df[signal_col].to_numpy()
            if len(epoch_data) == 0 or np.isnan(epoch_data).all():
                log_error(f"Empty or all-NaN signal data in column '{signal_col}'.")
                sys.exit(1)

            time_axis = df[time_col_name].to_numpy()
            if method == 'peak_baseline':
                t_min, t_max = baseline_window
                baseline_mask = (time_axis >= t_min) & (time_axis <= t_max)
                if not np.any(baseline_mask):
                    log_error(f"Baseline window {baseline_window} contains no time samples for column '{signal_col}'.")
                    sys.exit(1)
                baseline = np.mean(epoch_data[baseline_mask])
                val = np.max(epoch_data) - baseline
            elif method == 'mean':
                val = np.mean(epoch_data)
            elif method == 'peak':
                val = np.max(epoch_data)
            else:
                log_error(f"Unknown amplitude calculation method: '{method}'.")
                sys.exit(1)

            records.append({'channel': signal_col, 'amplitude': float(val), 'method': method})

    if not records:
        log_error("No valid amplitude records calculated.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_amp.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')
    
    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log_error("Usage: python amplitude_analyzer.py <input.parquet> <target_cols_list> [method] [baseline_low] [baseline_high]")
        sys.exit(1)

    ip = sys.argv[1]
    
    raw_targets = sys.argv[2]
    if raw_targets.startswith('[') and raw_targets.endswith(']'):
        t_cols = ast.literal_eval(raw_targets)
    else:
        t_cols = [c.strip() for c in raw_targets.split(',') if c.strip()]

    method = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] not in ('None', 'null', '') else 'peak_baseline'
    
    b_low = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] not in ('None', 'null', '') else -0.2
    b_high = float(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] not in ('None', 'null', '') else 0.0
    bw = (b_low, b_high)

    analyze_amplitude(ip, target_cols=t_cols, method=method, baseline_window=bw)
