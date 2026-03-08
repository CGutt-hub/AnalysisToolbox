"""Connectivity Analyzer - Compute functional connectivity between channels."""
import polars as pl, mne, sys, os, warnings, numpy as np
from mne_connectivity import spectral_connectivity_epochs
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[connectivity] INFO: {msg}")
def log_warning(msg): print(f"[connectivity] WARNING: {msg}")
def log_error(msg): print(f"[connectivity] ERROR: {msg}")

def analyze_connectivity(ip: str, method: str = 'coh', y_lim: float | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): log_error("Requires .fif format"); sys.exit(1)
    print(f"[connectivity] Connectivity analysis: {ip}, method={method}")
    
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    epochs = mne.make_fixed_length_epochs(raw, duration=30.0, overlap=15.0, verbose=False)
    conn = spectral_connectivity_epochs(epochs, method=method, fmin=0.01, fmax=0.1, verbose=False)
    conn_matrix = conn.get_data(output='dense').mean(axis=2)  # Average across frequencies
    
    # Extract upper triangle (avoid duplicates)
    n_ch = conn_matrix.shape[0]
    ch_names = raw.ch_names
    pairs, values = [], []
    for i in range(n_ch):
        for j in range(i+1, n_ch):
            pairs.append(f"{ch_names[i]}-{ch_names[j]}")
            values.append(conn_matrix[i, j])
    
    base = os.path.splitext(os.path.basename(ip))[0]
    workspace_root = os.getcwd()
    out_folder = os.path.join(workspace_root, f"{base}_connectivity")
    os.makedirs(out_folder, exist_ok=True)
    
    # Single condition output (all connectivity pairs)
    out = pl.DataFrame({
        'condition': ['connectivity'],
        'x_data': [pairs],
        'y_data': [values],
        'y_var': [[0.0] * len(values)],  # No variance for single connectivity estimate
        'plot_type': ['bar'],
        'x_label': ['Channel Pairs'],
        'y_label': [f'{method.upper()} Connectivity'],
        'y_ticks': [y_lim] if y_lim is not None else [None],
        'count': [len(pairs)]
    })
    
    out.write_parquet(os.path.join(out_folder, f"{base}_connectivity1.parquet"))
    print(f"[connectivity] {len(pairs)} channel pairs (mean {method}: {np.mean(values):.3f})")
    
    # Quality check: very weak connectivity
    mean_conn = float(np.mean(values))
    if mean_conn < 0.1:
        log_warning(f"Very weak mean connectivity ({mean_conn:.3f}), check signal quality or frequency band")
    
    # Create procedure visualization
    vis_path = os.path.join(workspace_root, f"{base}_connectivity_vis.parquet")
    out.write_parquet(vis_path, compression='snappy')
    print(f"[connectivity] Created procedure visualization: {vis_path}")
    
    signal_path = os.path.join(workspace_root, f"{base}_connectivity.parquet")
    pl.DataFrame({'signal': [1], 'source': [os.path.basename(ip)], 'conditions': [1], 'folder_path': [os.path.abspath(out_folder)]}).write_parquet(signal_path, compression='snappy')
    print(f"[connectivity] Output: {signal_path}")
    return signal_path

if __name__ == '__main__': (lambda a: analyze_connectivity(a[1], a[2] if len(a) > 2 else 'coh', float(a[3]) if len(a) > 3 and a[3] else None) if len(a) >= 2 else (print('[connectivity] Compute functional connectivity (coherence, PLI, wPLI) between channels. Plot-ready output.\nUsage: connectivity_analyzer.py <input.fif> [method=coh] [y_lim]'), sys.exit(1)))(sys.argv)