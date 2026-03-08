import numpy as np, sys, os, mne, warnings, polars as pl
from scipy import stats
from typing import cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def tddr(signal: np.ndarray) -> np.ndarray:
    """Temporal Derivative Distribution Repair (Fishburn et al., 2019).
    Robust outlier correction via temporal derivative and Tukey biweight.
    Works on any timeseries - modality agnostic."""
    deriv = np.diff(signal, prepend=signal[0])
    sigma = stats.median_abs_deviation(deriv, scale=1.4826)  # Normal distribution scaling factor
    if sigma < 1e-10: return signal
    weights = 1.0 / (1.0 + (deriv / (4.685 * sigma)) ** 2) ** 2
    return np.cumsum(deriv * weights) + signal[0]

def tddr_process(ip: str, out: str | None = None) -> str:
    """Apply TDDR robust correction to all channels."""
    print(f"[tddr] TDDR correction: {ip}")
    if not ip.endswith('.fif'): print(f"[tddr] Error: TDDR requires MNE .fif format"); sys.exit(1)
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    data: np.ndarray = cast(np.ndarray, raw.get_data())
    corrected = np.array([tddr(data[i, :]) for i in range(data.shape[0])])
    print(f"[tddr] TDDR applied to {len(raw.ch_names)} channels")
    raw_corrected = mne.io.RawArray(corrected, raw.info, verbose=False)
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = out or f"{base}_tddr.fif"
    raw_corrected.save(out_file, overwrite=True, verbose=False)
    # Generate inline visualization
    time_data: list[float] = raw.times.tolist()
    y_data: list[list[float]] = [corrected[i].tolist() for i in range(len(raw.ch_names))]
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
        'y_label': ['Optical Density (OD)']
    })
    vis_df.write_parquet(out_file.replace('.fif', '_vis.parquet'), compression='snappy')
    print(f"[tddr] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: tddr_process(a[1], a[2] if len(a) > 2 else None) if len(a) >= 2 else (print("[tddr] TDDR: Robust outlier correction via temporal derivative (Fishburn 2019).\nUsage: tddr_processor.py <input.fif> [output.fif]"), sys.exit(1)))(sys.argv)
