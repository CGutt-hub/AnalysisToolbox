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
    
    print(f"[referencing] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: apply_reference(a[1], a[2] if len(a) > 2 else 'average') if len(a) >= 2 else (print('[referencing] Apply EEG reference scheme (average, REST, or channel name).\nUsage: referencing_processor.py <input.fif> [reference=average]'), sys.exit(1)))(sys.argv)
