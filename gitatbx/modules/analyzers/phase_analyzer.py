"""Phase Analyzer - Compute circular mean phase spectrum from epoched EEG data.

Takes epoch-level EEG data (same format as psd_analyzer input), computes per-epoch
FFT phase angles, then averages circularly across epochs per condition.

Outputs per-condition plot-ready parquets with:
  x_data: frequency axis (Hz, 0–45 Hz)
  y_data: circular mean phase (radians)
  y_var:  circular variance = 1 - ITPC (0 = perfectly phase-locked, 1 = random)

Supports both channel mode (averages all channels) and region mode (per-ROI).

Usage:
    phase_analyzer.py <epochs.parquet> [channels] [regions_dict] [max_freq=45]

Examples:
    phase_analyzer.py eeg_epoched.parquet None None 45
    phase_analyzer.py eeg_epoched.parquet None '{"Frontal":["F3","F4"],"Parietal":["P3","P4"]}' 45
"""
import polars as pl, numpy as np, sys, ast, os


def log_info(msg):    print(f"[phase] INFO: {msg}")
def log_warning(msg): print(f"[phase] WARNING: {msg}")
def log_error(msg):   print(f"[phase] ERROR: {msg}")


def compute_phase(ip: str,
                  channels: list | None = None,
                  regions: dict | None = None,
                  max_freq: float = 45.0) -> str:
    """Compute circular mean phase spectrum from epoched data.

    Args:
        ip:        Input parquet [condition, epoch_id, time, channel_cols...]
        channels:  Optional list of channels to use (ignored when regions given)
        regions:   Optional dict of ROI → channel list for per-region output
        max_freq:  Upper frequency limit for output (default 45 Hz)

    Returns:
        Path to signal file pointing at per-condition output folder.
    """
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)

    log_info(f"Loading: {ip}")
    df = pl.read_parquet(ip)

    if len(df) == 0:
        log_error("Empty input — no epochs to compute phase, halting branch.")
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
    fmask = freqs <= max_freq
    freq_vals = freqs[fmask].tolist()

    epoch_ids = df['epoch_id'].unique().to_list()
    conds = sorted(set(
        str(df.filter(pl.col('epoch_id') == eid)['condition'][0])
        for eid in epoch_ids
    ))

    log_info(f"Data: {len(epoch_ids)} epochs, {len(conds)} conditions, {sfreq:.1f} Hz")

    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_phase")
    os.makedirs(out_folder, exist_ok=True)

    def _phase_for_channels(eids: list, ch_list: list) -> tuple[list, list] | None:
        """Circular mean phase + circular variance for a set of channels over given epochs."""
        phases = []
        for eid in eids:
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            avail = [ch for ch in ch_list if ch in epoch_df.columns]
            if not avail:
                continue
            composite = np.mean([epoch_df[ch].to_numpy() for ch in avail], axis=0)
            phases.append(np.angle(np.fft.rfft(composite)))
        if not phases:
            return None
        exp_ph = np.exp(1j * np.array(phases))
        mean_ph = np.angle(np.mean(exp_ph, axis=0))[fmask].tolist()
        circ_var = (1.0 - np.abs(np.mean(exp_ph, axis=0)))[fmask].tolist()
        return mean_ph, circ_var

    for idx, cond in enumerate(conds):
        cond_eids = df.filter(pl.col('condition') == cond)['epoch_id'].unique().to_list()

        if region_mode:
            rows = []
            for rname, rchans in regions_dict.items():
                result = _phase_for_channels(cond_eids, rchans)
                if result is None:
                    log_warning(f"No valid epochs for region '{rname}', condition '{cond}'")
                    continue
                mean_ph, circ_var = result
                rows.append({
                    'condition': cond,
                    'region': rname,
                    'x_data': freq_vals,
                    'y_data': mean_ph,
                    'y_var': circ_var,
                    'plot_type': 'line',
                    'x_label': 'Frequency (Hz)',
                    'y_label': 'Phase (rad)',
                    'y_ticks': None,
                })
            if rows:
                pl.DataFrame(rows).write_parquet(
                    os.path.join(out_folder, f"{base}_phase{idx+1}.parquet"))
        else:
            result = _phase_for_channels(cond_eids, ch_names)
            if result is None:
                log_warning(f"No valid epochs for condition '{cond}'")
                continue
            mean_ph, circ_var = result
            pl.DataFrame([{
                'condition': cond,
                'x_data': freq_vals,
                'y_data': mean_ph,
                'y_var': circ_var,
                'plot_type': 'line',
                'x_label': 'Frequency (Hz)',
                'y_label': 'Phase (rad)',
                'y_ticks': None,
            }]).write_parquet(os.path.join(out_folder, f"{base}_phase{idx+1}.parquet"))

        log_info(f"  {cond}: {len(cond_eids)} epochs")

    signal_path = os.path.join(os.getcwd(), f"{base}_phase.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conds)],
        'folder_path': [os.path.abspath(out_folder)],
    }).write_parquet(signal_path, compression='snappy')

    log_info(f"Output: {signal_path}")
    return signal_path


if __name__ == '__main__':
    (lambda a: compute_phase(
        a[1],
        ast.literal_eval(a[2]) if len(a) > 2 and a[2] not in ('None', 'null', '') else None,
        ast.literal_eval(a[3]) if len(a) > 3 and a[3] not in ('None', 'null', '') else None,
        float(a[4]) if len(a) > 4 and a[4] else 45.0,
    ) if len(a) >= 2 else (
        print('[phase] Circular mean phase spectrum per condition. Plot-ready output.'),
        print('[phase] Usage: phase_analyzer.py <epochs.parquet> [channels] [regions_dict] [max_freq=45]'),
        sys.exit(1),
    ))(sys.argv)
