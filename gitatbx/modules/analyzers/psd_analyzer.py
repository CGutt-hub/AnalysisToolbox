import polars as pl, numpy as np, sys, ast, os
from scipy import signal

# Logging helpers
def log_info(msg): print(f"[psd] INFO: {msg}")
def log_warning(msg): print(f"[psd] WARNING: {msg}")
def log_error(msg): print(f"[psd] ERROR: {msg}")

def compute_psd(ip: str, bands: dict, channels: list | None = None, regions: dict | None = None,
                y_lim: float | None = None) -> str:
    """Compute PSD from epoched data using scipy.signal.welch. No MNE dependency.
    
    Args:
        ip: Input parquet with epoched data [condition, epoch_id, time, channel_cols...]
        bands: Dictionary of frequency bands, e.g. {'alpha': [8, 12], 'beta': [13, 30]}
        channels: Optional list of channels to analyze (ignored if regions is specified)
        regions: Optional dict of regions, e.g. {'Frontal': ['F3','F4'], 'Parietal': ['P3','P4']}
                If specified, computes PSD per region instead of per channel
        y_lim: Optional Y-axis maximum limit
    """
    print(f"[psd] Loading: {ip}")
    df = pl.read_parquet(ip)
    
    if len(df) == 0:
        log_error("Empty input DataFrame — no epochs to compute PSD, halting branch.")
        sys.exit(1)
    
    ch_names = [c for c in df.columns if c not in ['condition', 'epoch_id', 'time']]
    
    # Regional mode: average channels within regions
    if isinstance(regions, dict) and len(regions) > 0:
        print(f"[psd] Regional mode: {len(regions)} regions")
        region_mode = True
        region_names = list(regions.keys())
        regions_dict = regions  # Type-narrowed variable for type checker
    else:
        region_mode = False
        regions_dict = {}  # Empty dict for type consistency
        if regions is not None and not isinstance(regions, dict):
            log_warning(f"Ignoring invalid regions parameter (type={type(regions).__name__}), expected dict")
        if channels:
            ch_names = [c for c in ch_names if c in channels]
    
    # Detect sampling frequency from time column
    first_epoch = df.filter(pl.col('epoch_id') == df['epoch_id'][0])
    times = first_epoch['time'].to_numpy()
    dt = float(times[1]) - float(times[0]) if len(times) > 1 else 1.0/256.0
    sfreq = 1.0 / dt
    
    epoch_ids = df['epoch_id'].unique().to_list()
    conditions = [str(df.filter(pl.col('epoch_id') == eid)['condition'][0]) for eid in epoch_ids]
    
    if region_mode:
        print(f"[psd] Data: {len(epoch_ids)} epochs, {len(regions_dict)} regions, {sfreq:.1f} Hz, Bands: {list(bands.keys())}")
    else:
        print(f"[psd] Data: {len(epoch_ids)} epochs, {len(ch_names)} ch, {sfreq:.1f} Hz, Bands: {list(bands.keys())}")
    
    # Compute PSD per epoch
    results = []
    nperseg = min(256, len(times))
    
    if region_mode:
        # Regional PSD: average channels within regions first, then compute PSD
        for ep_idx, eid in enumerate(epoch_ids):
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            cond = conditions[ep_idx]
            
            # Compute PSD for each channel first
            channel_psds = {}
            for ch in ch_names:
                if ch in epoch_df.columns:
                    data = epoch_df[ch].to_numpy()
                    freqs, psd = signal.welch(data, fs=sfreq, nperseg=nperseg)
                    channel_psds[ch] = (freqs, psd)
            
            # Average channels within each region
            for region_name, region_channels in regions_dict.items():
                available_chs = [ch for ch in region_channels if ch in channel_psds]
                
                if not available_chs:
                    continue
                
                # Average PSDs across channels in region
                avg_psd = np.mean([channel_psds[ch][1] for ch in available_chs], axis=0)
                freqs = channel_psds[available_chs[0]][0]
                
                # Compute band powers from regional average
                for band_name, (fmin, fmax) in bands.items():
                    mask = (freqs >= fmin) & (freqs <= fmax)
                    power = float(np.mean(avg_psd[mask])) if mask.any() else 0.0
                    
                    results.append({
                        'condition': cond,
                        'epoch_id': eid,
                        'region': region_name,
                        'band': band_name,
                        'power': power
                    })
    else:
        # Channel-level PSD
        for ep_idx, eid in enumerate(epoch_ids):
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            cond = conditions[ep_idx]
            
            for ch in ch_names:
                data = epoch_df[ch].to_numpy()
                freqs, psd = signal.welch(data, fs=sfreq, nperseg=nperseg)
                
                for band_name, (fmin, fmax) in bands.items():
                    mask = (freqs >= fmin) & (freqs <= fmax)
                    power = float(np.mean(psd[mask])) if mask.any() else 0.0
                    results.append({
                        'condition': cond,
                        'epoch_id': eid,
                        'channel': ch,
                        'band': band_name,
                        'power': power
                    })
    
    # Convert results to DataFrame
    result_df = pl.DataFrame(results)
    
    # Extract unique conditions and prepare output
    conds = sorted(result_df['condition'].unique().to_list())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_psd")
    os.makedirs(out_folder, exist_ok=True)
    
    print(f"[psd] Processing {len(conds)} conditions, outputting epoch-level data")
    
    # Determine grouping column based on mode
    group_col = 'region' if region_mode else 'channel'
    
    for idx, cond in enumerate(conds):
        cond_data = result_df.filter(pl.col('condition') == cond)
        
        # Epoch-level aggregated data
        if region_mode:
            epoch_agg = cond_data.group_by(['epoch_id', 'region', 'band']).agg([
                pl.col('power').mean().alias('value')
            ]).with_columns(pl.lit(cond).alias('condition'))
            epoch_pivot = epoch_agg.pivot(values='value', index=['condition', 'epoch_id', 'region'], on='band')
            # Write per-condition epoch-level data (mirrors the channel-mode write below)
            epoch_pivot.write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}.parquet"))
        else:
            # Channel mode: write long-format per-channel data as the standard output
            # (preserves channel resolution needed for downstream asymmetry/FAI analysis)
            raw_df = cond_data.select(['condition', 'epoch_id', 'channel', 'band', 'power'])
            raw_df.write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}.parquet"))
            # Also compute pivoted version for plotting below
            epoch_agg = cond_data.group_by(['epoch_id', 'band']).agg([
                pl.col('power').mean().alias('value')
            ]).with_columns(pl.lit(cond).alias('condition'))
            epoch_pivot = epoch_agg.pivot(values='value', index=['condition', 'epoch_id'], on='band')
        
        # Plot-ready output
        if region_mode:
            # Regional: regions on x-axis, bands as series
            band_names = sorted([c for c in epoch_pivot.columns if c not in ['condition', 'epoch_id', 'region']])
            region_list = sorted(epoch_pivot['region'].unique().to_list())
            
            series_data = []
            series_sems = []
            for band_name in band_names:
                band_means = []
                band_sems = []
                for region in region_list:
                    region_data = epoch_pivot.filter(pl.col('region') == region)[band_name]
                    mean_val = region_data.mean()
                    std_val = region_data.std()
                    band_means.append(float(mean_val) if mean_val is not None else 0.0)  # type: ignore[arg-type]
                    if std_val is not None:
                        sem = float(std_val) / (len(region_data) ** 0.5)  # type: ignore[arg-type]
                        band_sems.append(sem)
                    else:
                        band_sems.append(0.0)
                series_data.append(band_means)
                series_sems.append(band_sems)
            
            pl.DataFrame({
                'condition': [cond],
                'x_data': [region_list],
                'y_data': [series_data],
                'y_var': [series_sems],
                'labels': [band_names],
                'plot_type': ['line'],
                'x_label': ['Region'],
                'y_label': ['Power (μV²/Hz)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}_plot.parquet"))
        else:
            # Channel-level: bands on x-axis
            band_names = [c for c in epoch_pivot.columns if c not in ['condition', 'epoch_id']]
            band_means: list[float] = []
            band_sems: list[float] = []
            for b in band_names:
                mean_val = epoch_pivot[b].mean()
                std_val = epoch_pivot[b].std()
                band_means.append(float(mean_val) if mean_val is not None else 0.0)  # type: ignore[arg-type]
                if std_val is not None:
                    sem_calc = float(std_val) / (len(epoch_pivot) ** 0.5)  # type: ignore[arg-type]
                    band_sems.append(sem_calc)
                else:
                    band_sems.append(0.0)
            pl.DataFrame({
                'condition': [cond],
                'x_data': [band_names],
                'y_data': [band_means],
                'y_var': [band_sems],
                'plot_type': ['bar'],
                'x_label': ['Frequency Band'],
                'y_label': ['Power (μV²/Hz)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}_plot.parquet"))
        
        print(f"[psd]   {cond}: {len(cond_data['epoch_id'].unique())} epochs")
    
    # Combined flat file across all conditions (used by label_binner/row_filter in multi-trial pipelines)
    if region_mode:
        all_agg = result_df.group_by(['condition', 'epoch_id', 'region', 'band']).agg(
            pl.col('power').mean().alias('value')
        )
        all_pivot = all_agg.pivot(values='value', index=['condition', 'epoch_id', 'region'], on='band')
        all_pivot.write_parquet(os.path.join(out_folder, f"{base}_psd_all.parquet"))

    signal_path = os.path.join(os.getcwd(), f"{base}_psd.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conds)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='snappy')
    
    print(f"[psd] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: compute_psd(a[1], 
                          ast.literal_eval(a[2]), 
                          ast.literal_eval(a[3]) if len(a) > 3 and a[3] and a[3] not in ['None', 'null'] else None,
                          ast.literal_eval(a[4]) if len(a) > 4 and a[4] and a[4] not in ['None', 'null'] else None,
                          float(a[5]) if len(a) > 5 and a[5] and a[5] not in ['None', 'null'] else None) if len(a) >= 3 else (
        print('Power spectral density via Welch method per frequency band. Plot-ready output.'),
        print('[psd] Usage: python psd_analyzer.py <epochs.parquet> <bands_dict> [channels] [regions_dict] [y_lim]'),
        print('[psd] Example (channel): python psd_analyzer.py data.parquet "{\'alpha\':[8,12]}" "[\'Fz\',\'Cz\']" None 50'),
        print('[psd] Example (regional): python psd_analyzer.py data.parquet "{\'alpha\':[8,12]}" None "{\'Frontal\':[\'F3\',\'F4\']}" 50'),
        sys.exit(1)))(sys.argv)
