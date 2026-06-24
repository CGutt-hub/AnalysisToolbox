"""Power Spectrum Analyzer - Mean power spectral density (dB) from epoched EEG data.

Takes epoch-level EEG data, computes per-epoch Welch/FFT PSD, then averages across
epochs per condition.  Replaces the earlier circular-phase approach which produced
uninterpretable wrapped-phase plots.

Outputs per-condition plot-ready parquets with:
  x_data: frequency axis (Hz, 0â€“max_freq)
  y_data: mean PSD across epochs (µV²/Hz, linear)
  y_var:  SEM across epochs (µV²/Hz, linear)

Supports both channel mode (averages all channels) and region mode (per-ROI).

Usage:
    phase_analyzer.py <epochs.parquet> [channels] [regions_dict] [max_freq=45]

Examples:
    spectrum_analyzer.py eeg_epoched.parquet None None 45
    spectrum_analyzer.py eeg_epoched.parquet None '{"Frontal":["F3","F4"],"Parietal":["P3","P4"]}' 45
"""
import polars as pl, numpy as np, sys, ast, os


def log_info(msg):    print(f"[spectrum] INFO: {msg}")
def log_warning(msg): print(f"[spectrum] WARNING: {msg}")
def log_error(msg):   print(f"[spectrum] ERROR: {msg}")


def compute_spectrum(ip: str,
                  channels: list | None = None,
                  regions: dict | None = None,
                  max_freq: float = 45.0) -> str:
    """Compute mean power spectral density (µV²/Hz) per condition from epoched EEG.

    Args:
        ip:        Input parquet [condition, epoch_id, time, channel_cols...]
        channels:  Optional list of channels to use (ignored when regions given)
        regions:   Optional dict of ROI â†’ channel list for per-region output
        max_freq:  Upper frequency limit for output (default 45 Hz)

    Returns:
        Path to signal file pointing at per-condition output folder.
    """
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)

    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if len(df) == 0:
        log_error("Empty input â€” no epochs to process, halting branch.")
        sys.exit(1)

    ch_names = [c for c in df.columns if c not in ('condition', 'epoch_id', 'time')]

    # Determine mode
    region_mode = isinstance(regions, dict) and len(regions) > 0
    if not region_mode:
        if channels:
            ch_names = [c for c in ch_names if c in channels]
        regions_dict: dict = {}
    else:
        regions_dict = regions  # type: ignore[assignment]

    # Detect sampling frequency
    first_epoch = df.filter(pl.col('epoch_id') == df['epoch_id'][0])
    times = first_epoch['time'].to_numpy()
    dt = float(times[1] - times[0]) if len(times) > 1 else 1.0 / 256.0
    sfreq = 1.0 / dt
    n_times = len(times)

    freqs = np.fft.rfftfreq(n_times, d=1.0 / sfreq)
    fmask = (freqs >= 1.0) & (freqs <= max_freq)   # skip DC
    freq_vals = freqs[fmask].tolist()

    epoch_ids = df['epoch_id'].unique().to_list()
    conds = sorted(set(
        str(df.filter(pl.col('epoch_id') == eid)['condition'][0])
        for eid in epoch_ids
    ))

    log_info(f"Data: {len(epoch_ids)} epochs, {len(conds)} conditions, {sfreq:.1f} Hz")

    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_spectrum")
    os.makedirs(out_folder, exist_ok=True)

    def _psd_for_channels(eids: list, ch_list: list) -> tuple[list, list] | None:
        """Mean PSD (µV²/Hz) + SEM across epochs for a set of channels."""
        epoch_psds = []
        for eid in eids:
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            avail = [ch for ch in ch_list if ch in epoch_df.columns]
            if not avail:
                continue
            # Average channels first, then compute PSD
            composite = np.mean([epoch_df[ch].to_numpy() for ch in avail], axis=0)
            # One-sided PSD: |FFT|Â² / (N * sfreq)
            fft_vals = np.fft.rfft(composite)
            psd = (np.abs(fft_vals) ** 2) / (n_times * sfreq)
            # Double non-DC bins to account for negative frequencies
            psd[1:-1] *= 2
            epoch_psds.append(psd[fmask])
        if not epoch_psds:
            return None
        arr = np.array(epoch_psds)           # shape: (n_epochs, n_freqs)
        mean_psd = np.mean(arr, axis=0).tolist()
        sem_psd  = (np.std(arr, axis=0, ddof=1) / np.sqrt(len(arr))).tolist() if len(arr) > 1 else [0.0] * len(freq_vals)
        return mean_psd, sem_psd

    for idx, cond in enumerate(conds):
        cond_eids = df.filter(pl.col('condition') == cond)['epoch_id'].unique().to_list()

        if region_mode:
            rows = []
            for rname, rchans in regions_dict.items():
                result = _psd_for_channels(cond_eids, rchans)
                if result is None:
                    log_warning(f"No valid epochs for region '{rname}', condition '{cond}'")
                    continue
                mean_psd, sem_psd = result
                rows.append({
                    'condition': cond,
                    'region': rname,
                    'x_data': freq_vals,
                    'y_data': mean_psd,
                    'y_var': sem_psd,
                    'plot_type': 'line',
                    'x_label': 'Frequency (Hz)',
                    'y_label': 'Power (µV²/Hz)',
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
            mean_psd, sem_psd = result
            pl.DataFrame([{
                'condition': cond,
                'x_data': freq_vals,
                'y_data': mean_psd,
                'y_var': sem_psd,
                'plot_type': 'line',
                'x_label': 'Frequency (Hz)',
                'y_label': 'Power (µV²/Hz)',
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
    (lambda a: compute_spectrum(
        a[1],
        ast.literal_eval(a[2]) if len(a) > 2 and a[2] not in ('None', 'null', '') else None,
        ast.literal_eval(a[3]) if len(a) > 3 and a[3] not in ('None', 'null', '') else None,
        float(a[4]) if len(a) > 4 and a[4] else 45.0,
    ) if len(a) >= 2 else (
        print('[spectrum] Mean power spectrum (µV²/Hz) per condition. Plot-ready output.'),
        print('[spectrum] Usage: spectrum_analyzer.py <epochs.parquet> [channels] [regions_dict] [max_freq=45]'),
        sys.exit(1),
    ))(sys.argv)

