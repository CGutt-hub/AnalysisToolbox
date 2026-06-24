import polars as pl, numpy as np, sys, scipy.signal, os, warnings
from typing import cast
from numpy.typing import NDArray
import mne
import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Suppress MNE naming convention warnings
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[filtering] INFO: {msg}")
def log_warning(msg): print(f"[filtering] WARNING: {msg}")
def log_error(msg): print(f"[filtering] ERROR: {msg}")

def bandpass(sig: NDArray[np.float64], lf: float, hf: float, fs: float, order: int = 2) -> NDArray[np.float64]:
    if not (0 < lf < hf < fs/2): return sig
    sos = scipy.signal.butter(order, [lf, hf], btype='band', fs=fs, output='sos')
    return cast(NDArray[np.float64], scipy.signal.sosfiltfilt(sos, sig))

def lowpass(sig: NDArray[np.float64], hf: float, fs: float, order: int = 2) -> NDArray[np.float64]:
    if not (0 < hf < fs/2): return sig
    sos = scipy.signal.butter(order, hf, btype='low', fs=fs, output='sos')
    return cast(NDArray[np.float64], scipy.signal.sosfiltfilt(sos, sig))

def highpass(sig: NDArray[np.float64], lf: float, fs: float, order: int = 2) -> NDArray[np.float64]:
    if not (0 < lf < fs/2): return sig
    sos = scipy.signal.butter(order, lf, btype='high', fs=fs, output='sos')
    return cast(NDArray[np.float64], scipy.signal.sosfiltfilt(sos, sig))

def filter_signal(ip: str, col: str | None, lf: str, hf: str, fs: float = 1000.0, ftype: str = 'bandpass', out: str | None = None) -> str:
    print(f"[filtering] Filtering: {ip}")
    
    # Handle MNE .fif files for multi-channel EEG/fNIRS
    if ip.endswith('.fif'):
        raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
        print(f"[filtering] {ftype} filter on {len(raw.ch_names)} channels: {float(lf)}-{float(hf)} Hz")
        raw.filter(float(lf), float(hf), verbose=False)
        # Save in current working directory
        base = os.path.splitext(os.path.basename(ip))[0]
        out_file = out or f"{base}_filt.fif"
        raw.save(out_file, overwrite=True, verbose=False)
        print(f"[filtering] Output (MNE Raw): {out_file}")
        return out_file
    
    # Handle Polars parquet files for single-channel signals
    df = pl.read_parquet(ip)
    if not col: print(f"[filtering] Error: Column name required for parquet files"); sys.exit(1)
    target = next((c for c in df.columns if col.lower() in c.lower()), None)
    if not target: print(f"[filtering] Error: Column '{col}' not found"); sys.exit(1)
    sig: NDArray[np.float64] = df[target].to_numpy()
    print(f"[filtering] {ftype} filter on {target}: {len(sig)} samples")
    filtered = bandpass(sig, float(lf), float(hf), float(fs)) if ftype == 'bandpass' else lowpass(sig, float(hf), float(fs)) if ftype == 'lowpass' else highpass(sig, float(lf), float(fs))
    
    # Output filtered data
    result = pl.DataFrame({'time': df['time'] if 'time' in df.columns else np.arange(len(filtered))/float(fs), target.lower(): filtered, 'sfreq': [float(fs)]*len(filtered)})
    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = f"{base}_filt.parquet"
    result.write_parquet(out_file, compression='gzip')
    print(f"[filtering] Output: {out_file}")
    return out_file

if __name__ == '__main__': 
    args = sys.argv
    if len(args) < 4:
        print("[filtering] Apply Butterworth bandpass/lowpass/highpass filter to time series.")
        print("Usage: filtering_processor.py <input> <l_freq> <h_freq> [column] [fs] [ftype]")
        print("  .fif:     filtering_processor.py <input.fif> <l_freq> <h_freq>")
        print("  .parquet: filtering_processor.py <input.parquet> <l_freq> <h_freq> <column> [fs=1000] [ftype=bandpass]")
        sys.exit(1)
    
    # Parse arguments based on file type
    if args[1].endswith('.fif'):
        # .fif files: input, l_freq, h_freq, [output]
        filter_signal(args[1], None, args[2], args[3], out=args[4] if len(args) > 4 else None)
    else:
        # .parquet files: input, l_freq, h_freq, column, [fs], [ftype]
        col = args[4] if len(args) > 4 else None
        fs = float(args[5]) if len(args) > 5 else 1000.0
        ftype = args[6] if len(args) > 6 else 'bandpass'
        filter_signal(args[1], col, args[2], args[3], fs, ftype)