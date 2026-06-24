"""Power Spectrum Analyzer - Mean power spectral density from epoched EEG data.

Takes epoch-level EEG data, computes per-epoch Welch PSD, then averages across
epochs per condition.

Outputs per-condition plot-ready parquets with:
  x_data: frequency axis (Hz, 0-max_freq)
  y_data: mean PSD across epochs (uV2/Hz, linear)
  y_var:  None

Supports both channel mode (averages all channels) and region mode (per-ROI).

Optional label binning: when a labels parquet is supplied the 'condition' column
(which may contain raw trial IDs) is mapped to valence/arousal condition bins before
grouping.  Each epoch contributes to both its valence bin AND its arousal bin
independently.

Usage:
    spectrum_analyzer.py <epochs.parquet> [channels|None] [regions_dict|None]
        [max_freq=45] [labels.parquet|None] [val_thr=5] [ar_thr=5] [neutral_band=1.0]

Examples:
    spectrum_analyzer.py eeg_epoched.parquet None None 45
    spectrum_analyzer.py eeg_epoched.parquet None '{"Frontal":["F3","F4"]}' 45
    spectrum_analyzer.py eeg_windowed.parquet None None 45 labels.parquet 5 5 1.0
"""
import polars as pl, numpy as np, sys, ast, os
from scipy.signal import welch as scipy_welch


def log_info(msg):    print(f"[spectrum] INFO: {msg}")
def log_warning(msg): print(f"[spectrum] WARNING: {msg}")
def log_error(msg):   print(f"[spectrum] ERROR: {msg}")


def _build_condition_map(labels_path: str,
                         val_thr: float,
                         ar_thr: float,
                         neutral_band: float) -> dict:
    """Return {trial_id: [bin_label, ...]} from a labels parquet.

    Each trial maps to one valence bin AND one arousal bin so that PSD
    averages are computed independently per dimension.
    """
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
    """Compute mean PSD (uV2/Hz) per condition from epoched EEG.

    Args:
        ip:           Input parquet [condition, epoch_id, time, channel_cols...]
        channels:     Optional list of channels (ignored when regions given)
        regions:      Optional dict of ROI -> channel list
        max_freq:     Upper frequency limit (default 45 Hz)
        labels_path:  Optional labels parquet [trial_id, valence, arousal].
                      When supplied, the condition column (trial IDs) is replaced
                      by valence/arousal bins before grouping.
        val_thr:      Valence threshold for binning
        ar_thr:       Arousal threshold for binning
        neutral_band: Half-width of neutral zone around threshold

    Returns:
        Path to signal file pointing at per-condition output folder.
    """
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)

    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if len(df) == 0:
        log_error("Empty input -- no epochs to process, halting branch.")
        sys.exit(1)

    # -- Optional label binning --
    condition_map = None
    if labels_path and labels_path not in ('None', 'null', ''):
        if not os.path.exists(labels_path):
            log_error(f"Labels file not found: {labels_path}"); sys.exit(1)
        condition_map = _build_condition_map(labels_path, val_thr, ar_thr, neutral_band)
        all_bins = sorted(set(b for bins in condition_map.values() for b in bins))
        log_info(f"Label binning: {len(condition_map)} trials -> {all_bins}")

    ch_names = [c for c in df.columns if c not in ('condition', 'epoch_id', 'time')]

    region_mode = isinstance(regions, dict) and len(regions) > 0
    if not region_mode:
        if channels:
            ch_names = [c for c in ch_names if c in channels]
        regions_dict = {}
    else:
        regions_dict = regions

    # Detect sampling frequency
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

    # Build condition -> epoch_ids (with optional bin remapping)
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
                    'condition': cond,
                    'region': rname,
                    'x_data': freq_vals,
                    'y_data': result,
                    'y_var': None,
                    'plot_type': 'line',
                    'x_label': 'Frequency (Hz)',
                    'y_label': 'Power (uV2/Hz)',
                    'y_ticks': None,
                })
            if rows:
                pl.DataFrame(rows).write_parquet(
                    os.path.join(out_folder, f"{base}_spectrum{idx+1}.parquet"))
        else:
            result = _psd_for_channels(cond_eids, ch_names)
            if result is None:
                log_warning(f"No valid epochs for condition '{cond}'")
                continue
            pl.DataFrame([{
                'condition': cond,
                'x_data': freq_vals,
                'y_data': result,
                'y_var': None,
                'plot_type': 'line',
                'x_label': 'Frequency (Hz)',
                'y_label': 'Power (uV2/Hz)',
                'y_ticks': None,
            }]).write_parquet(os.path.join(out_folder, f"{base}_spectrum{idx+1}.parquet"))

        log_info(f"  {cond}: {len(cond_eids)} epochs")

    signal_path = os.path.join(os.getcwd(), f"{base}_spectrum.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conds)],
        'folder_path': [os.path.abspath(out_folder)],
    }).write_parquet(signal_path, compression='gzip')

    log_info(f"Output: {signal_path}")
    return signal_path


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[spectrum] Mean PSD per condition. Plot-ready output.')
        print('[spectrum] Usage: spectrum_analyzer.py <epochs.parquet>')
        print('[spectrum]   [labels.parquet|None] [channels|None] [regions_dict|None]')
        print('[spectrum]   [max_freq=45] [val_thr=5] [ar_thr=5] [neutral_band=1.0]')
        print('[spectrum] When the second positional arg is an existing .parquet file it')
        print('[spectrum] is treated as a labels file; otherwise it is treated as channels.')
        sys.exit(1)

    epochs = a[1]

    # Auto-detect whether the second arg is a labels file (existing parquet)
    # or a channels specification string.  This keeps backward-compatibility with
    # the EV1 pipeline which passes channels as the second positional arg.
    idx = 2
    labels = None
    if len(a) > 2 and a[2].endswith('.parquet') and os.path.exists(a[2]):
        labels = a[2]
        idx = 3   # labels consumed; channels/regions/max_freq follow

    def _get(i, default=None):
        v = a[i] if len(a) > i else None
        return None if v in (None, 'None', 'null', '') else v

    channels = ast.literal_eval(_get(idx)) if _get(idx) else None
    regions  = ast.literal_eval(_get(idx + 1)) if _get(idx + 1) else None
    max_freq = float(_get(idx + 2)) if _get(idx + 2) else 45.0
    val_thr  = float(_get(idx + 3)) if _get(idx + 3) else 5.0
    ar_thr   = float(_get(idx + 4)) if _get(idx + 4) else 5.0
    nb       = float(_get(idx + 5)) if _get(idx + 5) else 1.0

    compute_spectrum(epochs, channels, regions, max_freq, labels, val_thr, ar_thr, nb)
