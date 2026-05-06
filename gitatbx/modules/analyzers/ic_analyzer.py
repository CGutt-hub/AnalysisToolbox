"""ICA Analyzer - Perform ICA on EEG data, output cleaned .fif and component variance."""
import polars as pl, mne, sys, os, json, warnings
import numpy as np
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def _interpolate_channels(raw: mne.io.BaseRaw, channels: list[str]) -> mne.io.BaseRaw:
    """Recover EEG channels via spherical spline interpolation.

    Handles two cases:
    - Present channels: marked as bad and recovered from neighbors.
    - Absent channels: synthesized as zero-data placeholders first, then
      recovered from neighbors. This is done AFTER ICA so the source channels
      are artifact-free, giving the best possible reconstruction quality.
    """
    montage = mne.channels.make_standard_montage('standard_1020')
    absent = [ch for ch in channels if ch not in raw.ch_names and ch in montage.ch_names]
    present = [ch for ch in channels if ch in raw.ch_names]

    if absent:
        # Synthesize placeholder channels so MNE can interpolate them
        new_info = mne.create_info(absent, raw.info['sfreq'], ch_types='eeg')
        new_raw = mne.io.RawArray(np.zeros((len(absent), raw.n_times)), new_info, verbose=False)
        raw.add_channels([new_raw], force_update_info=True)
        print(f"[ic] Synthesizing {len(absent)} absent channel(s) via spherical spline: {absent}")

    to_interp = present + absent
    if not to_interp:
        return raw

    raw.set_montage(montage, on_missing='ignore', match_case=False, verbose=False)
    raw.info['bads'].extend(to_interp)
    raw.interpolate_bads(reset_bads=True, verbose=False)
    if present:
        print(f"[ic] Interpolated {len(present)} noisy channel(s): {present}")
    return raw

def analyze_ica(ip: str, n_components: float = 0.99, y_lim: float | None = None, exclude: str | None = None, interpolate: str | None = None) -> str:
    if not os.path.exists(ip): print(f"[ic] File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): print("[ic] Error: Requires .fif format"); sys.exit(1)
    print(f"[ic] ICA analysis: {ip}, n_components={n_components}")
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    original_sfreq = raw.info['sfreq']
    print(f"[ic] Loaded: {len(raw.ch_names)} channels, sfreq={original_sfreq} Hz")
    # Drop noisy edge channels before ICA (blacklist approach)
    if interpolate:
        try:
            interp_list = json.loads(interpolate) if interpolate.startswith('[') else [ch.strip() for ch in interpolate.split(',')]
        except json.JSONDecodeError:
            interp_list = [ch.strip() for ch in interpolate.split(',')]
    else:
        interp_list = []
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
    # Interpolate/synthesize target channels AFTER ICA so source channels are artifact-free
    if interp_list:
        cleaned_raw = _interpolate_channels(cleaned_raw, interp_list)
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

if __name__ == '__main__': (lambda a: analyze_ica(a[1], float(a[2]) if len(a) > 2 else 0.99, float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None, a[4] if len(a) > 4 else None, a[5] if len(a) > 5 else None) if len(a) >= 2 else (print('ICA decomposition with component variance output. Plot-ready output.\n[ic] Usage: ic_analyzer.py <input.fif> [n_components] [y_lim] [exclude_json] [interpolate_json]'), sys.exit(1)))(sys.argv)
