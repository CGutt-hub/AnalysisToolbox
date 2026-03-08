"""Channel Selector Processor - Select channels by pattern, indices, or quality criteria."""
import sys, os, mne, warnings, re, polars as pl
import numpy as np
from typing import cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[channel_selector] INFO: {msg}")
def log_warning(msg): print(f"[channel_selector] WARNING: {msg}")
def log_error(msg): print(f"[channel_selector] ERROR: {msg}")

def select_channels(ip: str, selector: str = '.*', mode: str = 'regex') -> str:
    """Select channels from .fif file.
    
    Modes:
        regex: Select channels matching regex pattern (default: .* = all)
        indices: Comma-separated indices like "0,1,5,6"
        names: Comma-separated exact names like "Fp1,Fp2,Fz"
    """
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): log_error("Requires .fif format"); sys.exit(1)
    print(f"[channel_selector] Channel selection: {ip}, mode={mode}, selector={selector}")
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    all_ch = raw.ch_names
    if mode == 'regex':
        picks = [i for i, ch in enumerate(all_ch) if re.match(selector, ch)]
    elif mode == 'indices':
        picks = [int(i) for i in selector.split(',')]
    elif mode == 'names':
        names = [n.strip() for n in selector.split(',')]
        picks = [i for i, ch in enumerate(all_ch) if ch in names]
    else:
        log_error(f"Unknown mode: {mode}"); sys.exit(1)
    
    if not picks: log_error("No channels matched selector"); sys.exit(1)
    
    # Quality check: very few channels selected
    selection_pct = (len(picks) / len(all_ch)) * 100 if len(all_ch) > 0 else 0
    if len(picks) < 3:
        log_warning(f"Only {len(picks)} channel(s) selected, some analyses require minimum 3-5 channels")
    elif selection_pct < 10:
        log_info(f"Selected {len(picks)}/{len(all_ch)} channels ({selection_pct:.1f}%)")
    
    raw.pick(picks)
    print(f"[channel_selector] Selected {len(picks)}/{len(all_ch)} channels")
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_sel.fif"
    raw.save(out_file, overwrite=True, verbose=False)
    
    # Generate inline visualization
    time_data: list[float] = raw.times.tolist()
    ch_data = cast(np.ndarray, raw.get_data())
    y_data: list[list[float]] = []
    for i in range(len(raw.ch_names)):
        channel_data: list[float] = ch_data[i, :].tolist()
        y_data.append(channel_data)
    if len(time_data) > 10000:
        step: int = len(time_data) // 10000
        time_data = time_data[::step]
        y_data = [yd[::step] for yd in y_data]
    vis_df = pl.DataFrame({
        'x_data': [[[time_data] for _ in range(len(raw.ch_names))]],
        'y_data': [y_data],
        'plot_type': ['line'],
        'labels': [raw.ch_names],
        'x_label': ['Time (s)'],
        'y_label': ['Amplitude']
    })
    vis_df.write_parquet(out_file.replace('.fif', '_vis.parquet'), compression='snappy')
    
    print(f"[channel_selector] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: select_channels(a[1], a[2] if len(a) > 2 else '.*', a[3] if len(a) > 3 else 'regex') if len(a) >= 2 else (print('[channel_selector] Select channels by regex pattern, indices, or names.\nUsage: channel_selector_processor.py <input.fif> [selector=.*] [mode:regex|indices|names]'), sys.exit(1)))(sys.argv)
