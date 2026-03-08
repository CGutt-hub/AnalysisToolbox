import polars as pl, numpy as np, sys, os

# Logging helpers
def log_info(msg): print(f"[peak] INFO: {msg}")
def log_warning(msg): print(f"[peak] WARNING: {msg}")
def log_error(msg): print(f"[peak] ERROR: {msg}")

def analyze_peaks(ip: str, method: str = 'max_abs', time_window: str | None = None, 
                  y_lim: float | None = None, y_label: str = 'Amplitude', suffix: str = 'peak') -> str:
    """
    Find peak latency and amplitude per channel per condition from epoched data.
    Generic peak analyzer - works on any epoched timeseries (EEG ERP, fNIRS HRF, etc.)
    
    Args:
        ip: Input parquet with epoched data [condition, epoch_id, time, channel_cols...]
        method: 'max_abs' (max absolute), 'max' (maximum), 'min' (minimum)
        time_window: Optional "start,stop" in seconds to restrict search (e.g., "0.1,0.5")
        y_lim: Optional Y-axis maximum limit
        y_label: Label for amplitude axis (e.g., 'Amplitude (μV)')
        suffix: Output file suffix (default 'peak', use 'erp' for ERP compatibility)
    
    Returns:
        Path to signal file
    """
    print(f"[peak] Peak analysis: {ip}, method={method}")
    df = pl.read_parquet(ip)
    
    if 'condition' not in df.columns or 'epoch_id' not in df.columns:
        raise ValueError("Input must have 'condition' and 'epoch_id' columns")
    
    # Auto-detect channel columns and time
    meta_cols = ['time', 'sfreq', 'epoch_id', 'condition']
    ch_cols = [c for c in df.columns if c not in meta_cols]
    conditions = sorted(df['condition'].unique().to_list())
    
    # Parse time window
    t_start, t_stop = None, None
    if time_window:
        parts = time_window.split(',')
        t_start, t_stop = float(parts[0]), float(parts[1])
    
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    print(f"[peak] Processing {len(conditions)} conditions, {len(ch_cols)} channels")
    
    for idx, cond in enumerate(conditions):
        cond_df = df.filter(pl.col('condition') == cond)
        
        # Average across epochs first (like ERP)
        if 'time' in cond_df.columns:
            # Group by time, average across epochs
            avg_df = cond_df.group_by('time').agg([
                pl.col(ch).mean().alias(ch) for ch in ch_cols
            ]).sort('time')
            times = avg_df['time'].to_numpy()
        else:
            # No time column - use sample index
            avg_df = cond_df.select(ch_cols)
            times = np.arange(len(avg_df))
        
        # Apply time window mask
        if t_start is not None and t_stop is not None:
            mask = (times >= t_start) & (times <= t_stop)
        else:
            mask = np.ones(len(times), dtype=bool)
        
        peak_results = []
        for ch in ch_cols:
            data = avg_df[ch].to_numpy()
            masked_data = data[mask]
            masked_times = times[mask]
            
            if len(masked_data) == 0:
                continue
            
            # Find peak based on method
            if method == 'max_abs':
                peak_idx = np.argmax(np.abs(masked_data))
            elif method == 'max':
                peak_idx = np.argmax(masked_data)
            elif method == 'min':
                peak_idx = np.argmin(masked_data)
            else:
                peak_idx = np.argmax(np.abs(masked_data))
            
            peak_results.append({
                'channel': ch,
                'latency': float(masked_times[peak_idx]),
                'amplitude': float(masked_data[peak_idx]),
                'condition': str(cond)
            })
        
        # Quality check: no peaks found
        if len(peak_results) == 0:
            log_warning(f"{cond}: No peaks found in time window {time_window or 'all'}")
        
        # Output in plotter format
        output = pl.DataFrame({
            'condition': [str(cond)],
            'x_data': [[r['channel'] for r in peak_results]],
            'y_data': [[r['amplitude'] for r in peak_results]],
            'latency': [[r['latency'] for r in peak_results]],
            'plot_type': ['bar'],
            'x_label': ['Channel'],
            'y_label': [y_label],
            'y_ticks': [y_lim] if y_lim is not None else [None]
        })
        
        out_path = os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet")
        output.write_parquet(out_path, compression='snappy')
        print(f"[peak]   {cond}: {len(peak_results)} channels -> {os.path.basename(out_path)}")
        
        # Also save detailed per-channel results
        detail_path = os.path.join(out_folder, f"{base}_{suffix}{idx+1}_detail.parquet")
        pl.DataFrame(peak_results).write_parquet(detail_path, compression='snappy')
    
    # Create procedure visualization file
    plot_files = [os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet") for idx in range(len(conditions))]
    if all(os.path.exists(f) for f in plot_files):
        try:
            all_plots = [pl.read_parquet(f) for f in plot_files]
            combined = pl.concat(all_plots)
            vis_path = os.path.join(os.getcwd(), f"{base}_{suffix}_vis.parquet")
            combined.write_parquet(vis_path, compression='snappy')
            print(f"[peak] Created procedure visualization: {vis_path}")
        except Exception as e:
            print(f"[peak] WARNING: Could not create procedure visualization: {e}")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='snappy')
    
    print(f"[peak] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: analyze_peaks(a[1], 
                              a[2] if len(a) > 2 else 'max_abs',
                              a[3] if len(a) > 3 and a[3] else None,
                              float(a[4]) if len(a) > 4 and a[4] else None,
                              a[5] if len(a) > 5 else 'Amplitude',
                              a[6] if len(a) > 6 else 'peak') if len(a) >= 2 else (
        print('Find peak latency and amplitude per condition. Plot-ready output.'),
        print('[peak] Usage: python peak_analyzer.py <epochs.parquet> [method] [time_window] [y_lim] [y_label] [suffix]'),
        print('[peak] Methods: max_abs (default), max, min'),
        print('[peak] Example: python peak_analyzer.py data_epochs.parquet max_abs "0.1,0.5" 10 "Amplitude (μV)" erp'),
        sys.exit(1)))(sys.argv)
