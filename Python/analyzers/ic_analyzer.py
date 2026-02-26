"""ICA Analyzer - Perform ICA on EEG data, output cleaned .fif and component variance."""
import polars as pl, mne, sys, os, warnings
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def analyze_ica(ip: str, n_components: float = 0.99, y_lim: float | None = None) -> str:
    if not os.path.exists(ip): print(f"[ic] File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): print("[ic] Error: Requires .fif format"); sys.exit(1)
    print(f"[ic] ICA analysis: {ip}, n_components={n_components}")
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    original_sfreq = raw.info['sfreq']
    print(f"[ic] Loaded: {len(raw.ch_names)} channels, sfreq={original_sfreq} Hz")
    target_sfreq = 250.0
    raw_for_ica = raw.copy().resample(target_sfreq, verbose=False) if original_sfreq > target_sfreq else raw.copy()
    ica = mne.preprocessing.ICA(n_components=n_components, random_state=42, verbose=False)
    ica.fit(raw_for_ica)
    n_ics = ica.n_components_
    print(f"[ic] Fitted: {n_ics} components")
    cleaned_raw = ica.apply(raw.copy())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_ica")
    os.makedirs(out_folder, exist_ok=True)
    cleaned_fif = os.path.join(out_folder, f"{base}_ica_cleaned.fif")
    cleaned_raw.save(cleaned_fif, overwrite=True, verbose=False)
    print(f"[ic] Cleaned: {os.path.basename(cleaned_fif)}")
    variance_data = pl.DataFrame({
        'x_data': [[f'IC{i}' for i in range(n_ics)]], 'y_data': [ica.pca_explained_variance_[:n_ics].tolist()],
        'plot_type': ['bar'], 'x_label': ['Independent Component'], 'y_label': ['Explained Variance (%)'],
        'y_ticks': [y_lim] if y_lim is not None else [None]})
    variance_data.write_parquet(os.path.join(out_folder, f"{base}_ica1.parquet"))
    # Create procedure visualization
    vis_path = os.path.join(os.getcwd(), f"{base}_ica_vis.parquet")
    variance_data.write_parquet(vis_path)
    print(f"[ic] Created procedure visualization: {vis_path}")
    signal_path = os.path.join(os.getcwd(), f"{base}_ica.parquet")
    pl.DataFrame({'signal': [1], 'source': [os.path.basename(ip)], 'n_components': [n_ics], 'cleaned_fif': [cleaned_fif], 'folder_path': [os.path.abspath(out_folder)]}).write_parquet(signal_path)
    print(f"[ic] Output: {signal_path}")
    return signal_path

if __name__ == '__main__': (lambda a: analyze_ica(a[1], float(a[2]) if len(a) > 2 else 0.99, float(a[3]) if len(a) > 3 and a[3] else None) if len(a) >= 2 else (print('ICA decomposition with component variance output. Plot-ready output.\n[ic] Usage: ic_analyzer.py <input.fif> [n_components] [y_lim]'), sys.exit(1)))(sys.argv)
