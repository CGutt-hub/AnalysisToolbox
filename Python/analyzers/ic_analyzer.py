"""ICA Analyzer - Perform ICA on EEG data, output cleaned .fif and component variance."""
import polars as pl, mne, sys, os, json, warnings
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def analyze_ica(ip: str, n_components: float = 0.99, y_lim: float | None = None, exclude: str | None = None) -> str:
    if not os.path.exists(ip): print(f"[ic] File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): print("[ic] Error: Requires .fif format"); sys.exit(1)
    print(f"[ic] ICA analysis: {ip}, n_components={n_components}")
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    original_sfreq = raw.info['sfreq']
    print(f"[ic] Loaded: {len(raw.ch_names)} channels, sfreq={original_sfreq} Hz")
    # Drop noisy edge channels before ICA (blacklist approach)
    if exclude:
        try:
            exclude_list = json.loads(exclude) if exclude.startswith('[') else [ch.strip() for ch in exclude.split(',')]
        except json.JSONDecodeError:
            exclude_list = [ch.strip() for ch in exclude.split(',')]
        to_drop = [ch for ch in exclude_list if ch in raw.ch_names]
        if to_drop:
            raw.drop_channels(to_drop)
            print(f"[ic] Excluded {len(to_drop)} channels: {to_drop}")
        else:
            print(f"[ic] Warning: None of the excluded channels found in data, using all {len(raw.ch_names)} channels")
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
    cleaned_fif = os.path.join(out_folder, f"{base}_ica.fif")
    cleaned_raw.save(cleaned_fif, overwrite=True, verbose=False)
    print(f"[ic] Cleaned: {os.path.basename(cleaned_fif)}")
    variance_data = pl.DataFrame({
        'x_data': [[f'IC{i}' for i in range(n_ics)]], 'y_data': [ica.pca_explained_variance_[:n_ics].tolist()],
        'plot_type': ['bar'], 'x_label': ['Independent Component'], 'y_label': ['Explained Variance (%)'],
        'y_ticks': [y_lim] if y_lim is not None else [None]})
    variance_data.write_parquet(os.path.join(out_folder, f"{base}_ica1.parquet"))
    signal_path = os.path.join(os.getcwd(), f"{base}_ica.parquet")
    pl.DataFrame({'signal': [1], 'source': [os.path.basename(ip)], 'n_components': [n_ics], 'cleaned_fif': [cleaned_fif], 'folder_path': [os.path.abspath(out_folder)]}).write_parquet(signal_path, compression='snappy')
    print(f"[ic] Output: {signal_path}")
    return signal_path

if __name__ == '__main__': (lambda a: analyze_ica(a[1], float(a[2]) if len(a) > 2 else 0.99, float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None, a[4] if len(a) > 4 else None) if len(a) >= 2 else (print('ICA decomposition with component variance output. Plot-ready output.\n[ic] Usage: ic_analyzer.py <input.fif> [n_components] [y_lim] [exclude_json]'), sys.exit(1)))(sys.argv)
