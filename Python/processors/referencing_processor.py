"""Referencing Processor - Apply EEG reference schemes using MNE."""
import sys, mne, os, warnings, polars as pl
import numpy as np
from typing import cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def apply_reference(ip: str, ref: str = 'average') -> str:
    """Apply EEG reference. ref: 'average', 'REST', or channel name(s) like 'Cz' or ['A1','A2']."""
    if not os.path.exists(ip): print(f"[referencing] File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): print("[referencing] Error: Requires .fif format"); sys.exit(1)
    print(f"[referencing] Referencing: {ip}, ref={ref}")
    
    try:
        raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    except Exception as e:
        print(f"[referencing] ERROR: Failed to load {ip}: {e}")
        sys.exit(1)
    
    print(f"[referencing] Applying {ref} reference to {len(raw.ch_names)} channels")
    
    try:
        raw.set_eeg_reference(ref, verbose=False)
    except Exception as e:
        print(f"[referencing] ERROR: Failed to apply {ref} reference: {e}")
        sys.exit(1)
    
    out_file = f"{os.path.splitext(os.path.basename(ip))[0]}_reref.fif"
    
    try:
        raw.save(out_file, overwrite=True, verbose=False)
    except Exception as e:
        print(f"[referencing] ERROR: Failed to save {out_file}: {e}")
        sys.exit(1)
    
    # Generate inline visualization
    try:
        time_data_raw = raw.times.tolist()
        ch_data = cast(np.ndarray, raw.get_data())
        y_data_raw: list[list[float]] = []
        for i in range(len(raw.ch_names)):
            channel_data: list[float] = ch_data[i, :].tolist()
            y_data_raw.append(channel_data)
        
        if len(time_data_raw) > 10000:
            step: int = len(time_data_raw) // 10000
            time_data: list[float] = time_data_raw[::step]
            y_data: list[list[float]] = []
            for yd in y_data_raw:
                downsampled: list[float] = yd[::step]
                y_data.append(downsampled)
        else:
            time_data: list[float] = time_data_raw
            y_data: list[list[float]] = y_data_raw
        
        vis_df = pl.DataFrame({
            'x_data': [[[time_data] for _ in range(len(raw.ch_names))]],
            'y_data': [y_data],
            'plot_type': ['line'],
            'labels': [raw.ch_names],
            'x_label': ['Time (s)'],
            'y_label': ['Amplitude (µV)']
        })
        vis_df.write_parquet(out_file.replace('.fif', '_vis.parquet'))
    except Exception as e:
        print(f"[referencing] WARNING: Failed to create visualization: {e}")
        # Don't fail the whole process if visualization fails
    
    print(f"[referencing] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: apply_reference(a[1], a[2] if len(a) > 2 else 'average') if len(a) >= 2 else (print('[referencing] Apply EEG reference scheme (average, REST, or channel name).\nUsage: referencing_processor.py <input.fif> [reference=average]'), sys.exit(1)))(sys.argv)
