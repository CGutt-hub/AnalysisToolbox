"""Asymmetry Analyzer Module - Generic, strict fail-fast implementation."""
import os, sys, polars as pl, numpy as np

def log_info(msg: str):  print(f"[asymmetry] INFO: {msg}")
def log_error(msg: str): print(f"[asymmetry] ERROR: {msg}")

def compute_asymmetry(ip: str, 
                      target_cols: list[str],
                      pairs: list[tuple[str, str]], 
                      mode: str,
                      value_col: str,
                      channel_col: str) -> str:
    log_info(f"Asymmetry analysis execution: {ip}, pairs={pairs}, mode={mode}")
    
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    if mode not in ('log', 'subtraction'):
        log_error(f"Invalid mode '{mode}'. Must be 'log' or 'subtraction'.")
        sys.exit(1)

    if not target_cols:
        log_error("Target columns list must be explicitly declared.")
        sys.exit(1)

    if not pairs:
        log_error("No channel/region pairs specified for asymmetry analysis.")
        sys.exit(1)

    try:
        df = pl.read_parquet(ip)
    except Exception as e:
        log_error(f"Failed to read parquet dataset: {e}")
        sys.exit(1)

    if df.height == 0:
        log_error("Input dataset is empty.")
        sys.exit(1)

    missing_targets = [c for c in target_cols if c not in df.columns]
    if missing_targets:
        log_error(f"Declared target/metadata columns missing from dataset: {missing_targets}")
        sys.exit(1)

    if value_col not in df.columns:
        log_error(f"Target value column '{value_col}' missing from dataset.")
        sys.exit(1)

    if channel_col not in df.columns:
        log_error(f"Target channel/region column '{channel_col}' missing from dataset.")
        sys.exit(1)

    if df[value_col].null_count() > 0 or np.isnan(df[value_col].to_numpy()).any():
        log_error(f"Null or NaN values detected in target value column '{value_col}'. Imputation disabled.")
        sys.exit(1)

    if df[channel_col].null_count() > 0:
        log_error(f"Null values detected in target channel column '{channel_col}'.")
        sys.exit(1)

    group_keys = [c for c in target_cols if c in ('condition', 'epoch_id', 'frequency', 'participant_id')]
    if not group_keys:
        log_error("No valid grouping keys found in target_cols to compute pair comparisons.")
        sys.exit(1)

    records = []
    grouped = df.group_by(group_keys)

    for keys, sub_df in grouped:
        key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
        ch_val_map = dict(zip(sub_df[channel_col].to_list(), sub_df[value_col].to_list()))

        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                log_error(f"Invalid pair format encountered: {pair}. Expected 2-element tuple or list.")
                sys.exit(1)
            left, right = pair
            if left in ch_val_map and right in ch_val_map:
                v_left = float(ch_val_map[left])
                v_right = float(ch_val_map[right])

                if mode == 'log':
                    if v_left <= 0 or v_right <= 0:
                        log_error(f"Log asymmetry encountered non-positive value: {left}={v_left}, {right}={v_right}")
                        sys.exit(1)
                    asym = np.log(v_right) - np.log(v_left)
                elif mode == 'subtraction':
                    asym = v_left - v_right

                row = key_dict.copy()
                row['pair'] = f"{left}_{right}"
                row['asymmetry'] = float(asym)
                row['mode'] = mode
                records.append(row)

    if not records:
        log_error("No pair matching succeeded for asymmetry computation.")
        sys.exit(1)

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_asymmetry.parquet")
    pl.DataFrame(records).write_parquet(out_file, compression='gzip')

    log_info(f"Output generated: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if len(sys.argv) != 7:
        log_error(f"CRITICAL: Exact parameters required (received {len(sys.argv)-1}, expected 6): <input.parquet> <target_cols_str> <pairs_str> <mode> <value_col> <channel_col>")
        sys.exit(1)
        
    input_file = sys.argv[1]
    target_cols = [c.strip(" '\"\\") for c in sys.argv[2].split(',') if c.strip(" '\"\\")]
    
    raw_pairs = sys.argv[3].strip(" '\"\\")
    pairs_list = []
    try:
        for pair_str in raw_pairs.split(','):
            pair_str = pair_str.strip()
            if not pair_str:
                continue
            if ':' not in pair_str:
                log_error(f"CRITICAL: Invalid pair format '{pair_str}'. Expected 'LEFT:RIGHT'.")
                sys.exit(1)
            left, right = pair_str.split(':', 1)
            pairs_list.append((left.strip(" '\"\\"), right.strip(" '\"\\")))
    except Exception as e:
        log_error(f"CRITICAL: Failed to parse pairs argument '{raw_pairs}': {e}")
        sys.exit(1)

    if not pairs_list:
        log_error("CRITICAL: No valid pairs parsed from fai_pairs argument.")
        sys.exit(1)

    mode = sys.argv[4].strip(" '\"\\")
    val_col = sys.argv[5].strip(" '\"\\")
    ch_col = sys.argv[6].strip(" '\"\\")

    compute_asymmetry(input_file, target_cols=target_cols, pairs=pairs_list, mode=mode, value_col=val_col, channel_col=ch_col)