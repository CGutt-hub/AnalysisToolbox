import polars as pl, sys, os

# Logging helpers
def log_info(msg): print(f"[waveform] INFO: {msg}")
def log_warning(msg): print(f"[waveform] WARNING: {msg}")
def log_error(msg): print(f"[waveform] ERROR: {msg}")

def analyze_waveform(ip: str, y_lim: float | None = None, y_label: str = 'Mean amplitude', 
                     downsample: int = 50, suffix: str = 'waveform') -> str:
    """
    Compute epoch-averaged waveform with SEM across epochs.
    Generic waveform analyzer - works on any epoched timeseries.
    
    Args:
        ip: Input parquet file with epoched data (flat format with condition/epoch_id)
        y_lim: Optional Y-axis maximum limit for consistent scaling
        y_label: Label for y-axis (e.g., 'Mean EDA (μS)', 'Amplitude (μV)')
        downsample: Keep every Nth point (default 50 for manageable file size)
        suffix: Output file suffix (default 'waveform', use 'scr' for SCR compatibility)
    
    Returns:
        Path to signal file
    """
    print(f"[waveform] Waveform analysis: {ip}")
    df = pl.read_parquet(ip)
    
    if 'condition' not in df.columns or 'epoch_id' not in df.columns:
        raise ValueError("Input must have 'condition' and 'epoch_id' columns (flat epoched format)")
    
    # Auto-detect signal column
    signal_col = [c for c in df.columns if c not in ['time', 'sfreq', 'epoch_id', 'condition']][0]
    conditions = sorted(df['condition'].unique().to_list())
    
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    print(f"[waveform] Processing {len(conditions)} conditions, signal column: {signal_col}")
    
    for idx, cond in enumerate(conditions):
        cond_df = df.filter(pl.col('condition') == cond)
        
        # Quality check: low epoch count
        n_epochs = len(cond_df['epoch_id'].unique())
        if n_epochs < 3:
            log_warning(f"{cond}: Only {n_epochs} epoch(s), waveform SEM may be unreliable (recommended minimum: 5)")
        
        # Compute relative time within each epoch
        cond_df = cond_df.with_columns([
            (pl.col('time') - pl.col('time').min().over('epoch_id')).alias('relative_time')
        ])
        
        # Average across epochs with SEM, then downsample
        result = cond_df.group_by('relative_time').agg([
            pl.col(signal_col).mean().alias('mean_signal'),
            (pl.col(signal_col).std() / pl.col(signal_col).count().sqrt()).alias('sem_signal')
        ]).sort('relative_time').with_row_index().filter(
            pl.col('index') % downsample == 0
        ).drop('index')
        
        # Output format for plotter with error bands
        output = pl.DataFrame({
            'condition': [str(cond)],
            'x_data': [result['relative_time'].to_list()],
            'y_data': [result['mean_signal'].to_list()],
            'y_var': [result['sem_signal'].to_list()],
            'plot_type': ['line_grid'],
            'x_label': ['Time from onset (s)'],
            'y_label': [y_label],
            'y_ticks': [y_lim] if y_lim is not None else [None]
        })
        
        out_path = os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet")
        output.write_parquet(out_path)
        print(f"[waveform]   {cond}: {len(result)} points -> {os.path.basename(out_path)}")
    
    # Create procedure visualization file (aggregate all conditions)
    plot_files = [os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet") for idx in range(len(conditions))]
    if all(os.path.exists(f) for f in plot_files):
        try:
            all_plots = [pl.read_parquet(f) for f in plot_files]
            combined = pl.concat(all_plots)
            vis_path = os.path.join(os.getcwd(), f"{base}_{suffix}_vis.parquet")
            combined.write_parquet(vis_path)
            print(f"[waveform] Created procedure visualization: {vis_path}")
        except Exception as e:
            print(f"[waveform] WARNING: Could not create procedure visualization: {e}")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path)
    
    print(f"[waveform] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: analyze_waveform(a[1], 
                                 float(a[2]) if len(a) > 2 and a[2] else None,
                                 a[3] if len(a) > 3 else 'Mean amplitude',
                                 int(a[4]) if len(a) > 4 and a[4] else 50,
                                 a[5] if len(a) > 5 else 'waveform') if len(a) >= 2 else (        print('Compute epoch-averaged waveforms with SEM error bands. Plot-ready output.'),        print('[waveform] Usage: python waveform_analyzer.py <epochs.parquet> [y_lim] [y_label] [downsample] [suffix]'),
        print('[waveform] Example: python waveform_analyzer.py data_epochs.parquet 5.0 "Mean EDA (μS)" 50 scr'),
        sys.exit(1)))(sys.argv)
