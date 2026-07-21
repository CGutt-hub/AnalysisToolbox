import polars as pl, numpy as np, sys, ast, os
from scipy.signal import welch as scipy_welch

def log_info(msg):    print(f"[spectrum] INFO: {msg}")
def log_warning(msg): print(f"[spectrum] WARNING: {msg}")
def log_error(msg):   print(f"[spectrum] ERROR: {msg}")

def _build_condition_map(labels_path: str,
                         val_thr: float,
                         ar_thr: float,
                         neutral_band: float) -> dict:
    lbl = pl.read_parquet(labels_path)
    required = {'trial_id', 'valence', 'arousal'}
    missing = required - set(lbl.columns)
    if missing:
        log_error(f"Labels parquet missing columns: {missing}"); sys.exit(1)

    mapping = {}
    for row in lbl.select(['trial_id', 'valence', 'arousal']).iter_rows(named=True):
        tid = str(row['trial_id'])
        v   = float(row['valence'])
        a   = float(row['arousal'])
        bins = []
        if v >= val_thr + neutral_band:
            bins.append('valence_high')
        elif v <= val_thr - neutral_band:
            bins.append('valence_low')
        elif neutral_band > 0:
            bins.append('valence_neutral')
        if a >= ar_thr + neutral_band:
            bins.append('arousal_high')
        elif a <= ar_thr - neutral_band:
            bins.append('arousal_low')
        elif neutral_band > 0:
            bins.append('arousal_neutral')
        mapping[tid] = bins
    return mapping

def compute_spectrum(ip: str,
                     channels=None,
                     regions=None,
                     max_freq: float = 45.0,
                     labels_path=None,
                     val_thr: float = 5.0,
                     ar_thr: float = 5.0,
                     neutral_band: float = 1.0) -> str:
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)

    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if len(df) == 0:
        log_error("Empty input -- no epochs to process, halting branch.")
        sys.exit(1)

    condition_map = None
    if labels_path and labels_path not in ('None', 'null', ''):
        if not os.path.exists(labels_path):
            log_error(f"Labels file not found: {labels_path}"); sys.exit(1)
        condition_map = _build_condition_map(labels_path, val_thr, ar_thr, neutral_band)
        all_bins = sorted(set(b for bins in condition_map.values() for b in bins))
        log_info(f"Label binning: {len(condition_map)} trials -> {all_bins}")

    ch_names = [c for c in df.columns if c not in ('condition', 'epoch_id', 'time')]

    # BEHOBEN: Absolut wasserdichter Type-Guard für regions_dict (Löst den items() Fehler)
    region_mode = isinstance(regions, dict) and len(regions) > 0
    regions_dict = regions if (isinstance(regions, dict) and region_mode) else {}
    
    if not region_mode:
        if channels:
            ch_names = [c for c in ch_names if c in channels]

    # Sampling-Frequenz sicher auslesen
    first_epoch = df.filter(pl.col('epoch_id') == df['epoch_id'][0])
    times = first_epoch['time'].to_numpy()
    dt = float(times[1] - times[0]) if len(times) > 1 else 1.0 / 256.0
    sfreq = 1.0 / dt
    n_times = len(times)

    nperseg = min(int(sfreq), n_times // 2)
    _f_ref = scipy_welch(np.zeros(n_times), fs=sfreq, nperseg=nperseg)[0]
    fmask = (_f_ref >= 1.0) & (_f_ref <= max_freq)
    freq_vals = _f_ref[fmask].tolist()

    epoch_ids = df['epoch_id'].unique().to_list()

    if condition_map is not None:
        cond_to_eids = {}
        skipped = 0
        for eid in epoch_ids:
            trial_id = str(df.filter(pl.col('epoch_id') == eid)['condition'][0])
            bins = condition_map.get(trial_id, [])
            if not bins:
                skipped += 1
            for cbin in bins:
                cond_to_eids.setdefault(cbin, []).append(eid)
        if skipped:
            log_warning(f"{skipped} epoch(s) had no matching label -- skipped")
    else:
        cond_to_eids = {}
        for eid in epoch_ids:
            c = str(df.filter(pl.col('epoch_id') == eid)['condition'][0])
            cond_to_eids.setdefault(c, []).append(eid)

    conds = sorted(cond_to_eids.keys())
    log_info(f"Data: {len(epoch_ids)} epochs, {len(conds)} conditions, {sfreq:.1f} Hz")

    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_spectrum")
    os.makedirs(out_folder, exist_ok=True)

    def _psd_for_channels(eids, ch_list):
        epoch_psds = []
        for eid in eids:
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            avail = [ch for ch in ch_list if ch in epoch_df.columns]
            if not avail:
                continue
            composite = np.mean([epoch_df[ch].to_numpy() for ch in avail], axis=0)
            _, psd = scipy_welch(composite, fs=sfreq, nperseg=nperseg)
            epoch_psds.append(psd[fmask])
        if not epoch_psds:
            return None
        return np.mean(np.array(epoch_psds), axis=0).tolist()

    for idx, cond in enumerate(conds):
        cond_eids = cond_to_eids[cond]

        if region_mode:
            rows = []
            for rname, rchans in regions_dict.items():
                result = _psd_for_channels(cond_eids, rchans)
                if result is None:
                    log_warning(f"No valid epochs for region '{rname}', condition '{cond}'")
                    continue
                rows.append({
                    'condition': cond, 'region': rname, 'x_data': freq_vals, 'y_data': result,
                    'y_var': None, 'plot_type': 'line', 'x_label': 'Frequency (Hz)', 'y_label': 'Power (uV2/Hz)', 'y_ticks': None
                })
            if rows:
                pl.DataFrame(rows).write_parquet(os.path.join(out_folder, f"{base}_spectrum{idx+1}.parquet"), compression='gzip')
        else:
            result = _psd_for_channels(cond_eids, ch_names)
            if result is None:
                log_warning(f"No valid epochs for condition '{cond}'")
                continue
            pl.DataFrame([{
                'condition': cond, 'x_data': freq_vals, 'y_data': result, 'y_var': None,
                'plot_type': 'line', 'x_label': 'Frequency (Hz)', 'y_label': 'Power (uV2/Hz)', 'y_ticks': None
            }]).write_parquet(os.path.join(out_folder, f"{base}_spectrum{idx+1}.parquet"), compression='gzip')

        log_info(f"  {cond}: {len(cond_eids)} epochs")

    # === DIESEN ABSCHNITT AM ENDE DER FUNKTION COMPUTE_SPECTRUM ERSETZEN ===
    signal_path = os.path.join(os.getcwd(), f"{base}_spectrum.parquet")
    pl.DataFrame({
        'signal': [signal_path],  # BEHOBEN: Wert eingetragen und syntaktisch geschlossen!
        'source': [os.path.basename(ip)], 
        'conditions': [len(conds)], 
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='gzip')

    log_info(f"Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[spectrum] Mean PSD per condition. Plot-ready output.')
        sys.exit(1)

    # BEHOBEN: Index [1] extrahiert den echten String-Pfad aus der Liste
    epochs = a[1]

    # BEHOBEN: Index [2] sichert, dass os.path.exists() einen validen str-Pfad erhält
    idx = 2
    labels = None
    if len(a) > 2 and str(a[2]).endswith('.parquet') and os.path.exists(a[2]):
        labels = a[2]
        idx = 3

    def _get(i: int) -> str | None:
        if i >= len(a): return None
        v = a[i]
        return None if v in (None, 'None', 'null', '', 'result') else str(v)

    # Typ-Guard für Channels
    channels = None
    raw_ch = _get(idx)
    if raw_ch is not None:
        try:
            if not raw_ch.startswith("{"):
                channels = ast.literal_eval(raw_ch)
        except Exception:
            channels = None

    # Typ-Guard für Regions
    regions = None
    raw_reg = _get(idx + 1)
    if raw_reg is not None:
        try:
            regions = ast.literal_eval(raw_reg)
        except Exception:
            regions = None

    # BEHOBEN: Explizite 'is not None' Prüfungen verhindern den ConvertibleToFloat-Konflikt bei float()
    val_max_freq = _get(idx + 2)
    max_freq = float(val_max_freq) if val_max_freq is not None else 45.0
    
    val_v_thr = _get(idx + 3)
    val_thr  = float(val_v_thr) if val_v_thr is not None else 5.0
    
    val_a_thr = _get(idx + 4)
    ar_thr   = float(val_a_thr) if val_a_thr is not None else 5.0
    
    val_nb = _get(idx + 5)
    nb       = float(val_nb) if val_nb is not None else 1.0

    compute_spectrum(epochs, channels, regions, max_freq, labels, val_thr, ar_thr, nb)
