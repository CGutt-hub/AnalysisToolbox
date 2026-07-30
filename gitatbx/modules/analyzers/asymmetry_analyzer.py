"""Asymmetry Analyzer Module - Generic, parameter-driven strict implementation with uniform argument ordering."""
import os, sys, polars as pl, numpy as np, ast

def log_info(msg: str):  print(f"[asymmetry] INFO: {msg}")
def log_error(msg: str): print(f"[asymmetry] ERROR: {msg}")

def compute_asymmetry(ip: str, 
                      target_cols: list[str],
                      pairs: list[tuple[str, str]], 
                      mode: str = 'log',
                      value_col: str = 'power',
                      channel_col: str = 'channel') -> str:
    log_info(f"Asymmetry analysis execution: {ip}, pairs={pairs}, mode={mode}")
    
    if not os.path.exists(ip) or os.path.getsize(ip) <= 12:
        log_error(f"Input file not found or empty: {ip}")
        sys.exit(1)

    if not target_cols:
        log_error("Target columns list must be explicitly declared.")
        sys.exit(1)

    if not pairs:
        log_error("No channel/region pairs specified for asymmetry analysis.")
        sys.exit(1)

    df = pl.read_parquet(ip)
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

    # Strict contract: use explicit metadata group keys passed via target_cols
    group_keys = [c for c in target_cols if c in ('condition', 'epoch_id', 'frequency', 'participant_id')]
    if not group_keys:
        log_error("No valid grouping keys found in target_cols to compute pair comparisons.")
        sys.exit(1)

    records = []
    grouped = df.group_by(group_keys)

    for keys, sub_df in grouped:
        key_dict = dict(zip(group_keys, keys if isinstance(keys, tuple) else [keys]))
        ch_val_map = dict(zip(sub_df[channel_col].to_list(), sub_df[value_col].to_list()))

        for left, right in pairs:
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
                else:
                    log_error(f"Unsupported asymmetry mode: '{mode}'")
                    sys.exit(1)

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
    if len(sys.argv) < 3:
        log_error("Usage: python asymmetry_analyzer.py <input.parquet> <target_cols_list> <pairs_list> [mode] [value_col] [channel_col]")
        sys.exit(1)
        
    input_file = sys.argv[1]
    
    raw_targets = sys.argv[2]
    if raw_targets.startswith('[') and raw_targets.endswith(']'):
        target_cols = ast.literal_eval(raw_targets)
    else:
        target_cols = [c.strip() for c in raw_targets.split(',') if c.strip()]

    pairs_list = ast.literal_eval(sys.argv[3])

    mode = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] not in ('None', '') else 'log'
    val_col = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] not in ('None', '') else 'power'
    ch_col = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] not in ('None', '') else 'channel'

    compute_asymmetry(input_file, target_cols=target_cols, pairs=pairs_list, mode=mode, value_col=val_col, channel_col=ch_col)