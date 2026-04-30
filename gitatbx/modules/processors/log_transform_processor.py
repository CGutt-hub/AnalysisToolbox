import numpy as np, sys, os, mne, warnings, polars as pl
from mne.io.constants import FIFF
from numpy.typing import NDArray
from typing import cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def log_transform(data: NDArray[np.float64], baseline_samples: int) -> NDArray[np.float64]:
    """Apply -log10(x/baseline) transform. Generic intensity→absorbance conversion.
    Used in spectroscopy (Beer-Lambert), fNIRS optical density, etc."""
    baseline_mean = data[:, :baseline_samples].mean(axis=1, keepdims=True)
    baseline_mean = np.where(baseline_mean > 0, baseline_mean, 1e-10)  # Avoid div by 0
    # Clamp data to positive values to avoid log(0) or log(negative)
    data_safe = np.where(data > 0, data, 1e-10)
    return -np.log10(data_safe / baseline_mean)

def log_transform_process(ip: str, baseline_sec: str = '5.0', out: str | None = None) -> str:
    """Apply log transform to all channels. Input: .fif, Output: .fif"""
    print(f"[log_transform] Log transform: {ip}")
    if not ip.endswith('.fif'): print(f"[log_transform] Error: Requires .fif format"); sys.exit(1)
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    data = cast(NDArray[np.float64], raw.get_data())
    baseline_samples = int(float(baseline_sec) * raw.info['sfreq'])
    transformed = log_transform(data, baseline_samples)
    print(f"[log_transform] -log10(x/baseline) on {len(raw.ch_names)} ch, baseline={baseline_sec}s")
    raw_out = mne.io.RawArray(transformed, raw.info, verbose=False)
    # Mark fNIRS channels as optical density so downstream MNE-NIRS functions
    # (e.g. short_channel_regression, beer_lambert_law) accept the data.
    for ch in raw_out.info['chs']:
        if ch['coil_type'] == FIFF.FIFFV_COIL_FNIRS_CW_AMPLITUDE:
            ch['coil_type'] = FIFF.FIFFV_COIL_FNIRS_OD
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = out or f"{base}_log.fif"
    raw_out.save(out_file, overwrite=True, verbose=False)
    print(f"[log_transform] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: log_transform_process(a[1], a[2] if len(a) > 2 else '5.0', a[3] if len(a) > 3 else None) if len(a) >= 2 else (print("[log_transform] Apply -log10(x/baseline) transform. Converts intensity to absorbance.\nUsage: log_transform_processor.py <input.fif> [baseline_sec=5.0] [output.fif]"), sys.exit(1)))(sys.argv)
