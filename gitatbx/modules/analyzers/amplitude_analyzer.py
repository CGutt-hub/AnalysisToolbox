import polars as pl, sys, os, numpy as np

# Logging helpers
def log_info(msg): print(f"[amplitude] INFO: {msg}")
def log_warning(msg): print(f"[amplitude] WARNING: {msg}")
def log_error(msg): print(f"[amplitude] ERROR: {msg}")

def analyze_amplitude(ip: str, method: str = 'peak_baseline', y_lim: float | None = None, y_label: str = 'Amplitude') -> str:
    """Analyze amplitude per condition: epoch-level or aggregated (mean ± SEM) output.
    Generic amplitude analyzer - works on any signal.
    
    Args:
        ip: Input parquet with epoched data (flat format: condition, epoch_id, time, signal_col)
        method: 'peak_baseline' (max - baseline), 'mean' (mean amplitude), 'peak' (max amplitude)
        y_lim: Optional Y-axis maximum (None = output epoch-level data for bootstrap)
        y_label: Label for y-axis (e.g., 'Conductance Change (μS)', 'Amplitude (mV)')
    
    Returns:
        Path to signal file
    
    Note: If y_lim is None, outputs epoch-level data for bootstrap; else outputs mean ± SEM
    """
    suffix = 'amp'
    print(f"[amplitude] Amplitude analysis: {ip}, method={method}, mode={'epoch-level' if y_lim is None else 'aggregated'}")
    df = pl.read_parquet(ip)
    
    if 'condition' not in df.columns or 'epoch_id' not in df.columns:
        raise ValueError("Input must have 'condition' and 'epoch_id' columns")
    
    # Auto-detect signal columns (can be multiple for regional data)
    signal_cols = [c for c in df.columns if c not in ['time', 'sfreq', 'epoch_id', 'condition']]
    if not signal_cols:
        raise ValueError(f"No signal columns found. Available: {df.columns}")
    
    conditions = sorted(df['condition'].unique().to_list())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    multi_region = len(signal_cols) > 1
    print(f"[amplitude] Processing {len(conditions)} conditions, {len(signal_cols)} signal column(s): {signal_cols}")
    
    if multi_region:
        log_info(f"Multi-region mode: will output aggregated data per region for asymmetry analysis")
    
    # Quality check: detect missing/NaN data
    for signal_col in signal_cols:
        total_values = len(df)
        nan_count = df.select(pl.col(signal_col).is_null().sum()).item()
        if nan_count > 0:
            nan_pct = (nan_count / total_values) * 100
            if nan_pct > 10:
                log_warning(f"{signal_col}: {nan_pct:.1f}% of values are NaN/missing ({nan_count}/{total_values})")
            else:
                log_info(f"{signal_col}: {nan_pct:.1f}% of values are NaN/missing ({nan_count}/{total_values})")
    
    # Compute epoch-level values for each signal column
    all_epoch_data = []
    for idx, cond in enumerate(conditions):
        cond_df = df.filter(pl.col('condition') == cond)
        epochs = cond_df['epoch_id'].unique().to_list()
        
        # Quality check: low epoch count
        n_epochs = len(epochs)
        if n_epochs < 3:
            log_warning(f"{cond}: Only {n_epochs} epoch(s), statistics may be unreliable (recommended minimum: 5)")
        elif n_epochs < 5:
            log_info(f"{cond}: {n_epochs} epochs (acceptable, but >5 preferred)")
        
        for eid in epochs:
            epoch_row = {'condition': cond, 'epoch_id': eid}
            
            # Compute metric for each signal column
            for signal_col in signal_cols:
                epoch_data = cond_df.filter(pl.col('epoch_id') == eid)[signal_col].to_numpy()
                
                if method == 'peak_baseline':
                    baseline = np.mean(epoch_data[:int(len(epoch_data)*0.2)])
                    val = np.max(epoch_data) - baseline
                elif method == 'mean':
                    val = np.mean(epoch_data)
                elif method == 'peak':
                    val = np.max(epoch_data)
                else:
                    val = np.mean(epoch_data)
                
                epoch_row[signal_col] = val
            
            all_epoch_data.append(epoch_row)
    
    epoch_df = pl.DataFrame(all_epoch_data)
    
    # Output mode: 
    # - Single-region without y_lim: epoch-level for bootstrap
    # - Single-region with y_lim: aggregated plot-ready
    # - Multi-region: always aggregated (for asymmetry analysis)
    plot_ready_mode = False  # Track if we're generating plot-ready output
    
    if multi_region:
        # Multi-region mode: output aggregated data (region as rows)
        # Format: long format with 'region', 'value', 'sem' columns
        for idx, cond in enumerate(conditions):
            cond_epoch_df = epoch_df.filter(pl.col('condition') == cond)
            
            # Aggregate each region: compute mean±SEM across epochs
            region_rows = []
            for signal_col in signal_cols:
                vals = cond_epoch_df[signal_col].to_numpy()
                mean_val = float(np.mean(vals))
                sem_val = float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
                region_rows.append({
                    'condition': cond,
                    'region': signal_col,
                    'value': mean_val,
                    'sem': sem_val,
                    'count': len(vals)
                })
            
            output_df = pl.DataFrame(region_rows)
            output_df.write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
            
            # Print summary stats
            stats_str = ", ".join([f"{r['region']}={r['value']:.3f}±{r['sem']:.3f}" for r in region_rows])
            print(f"[amplitude]   {cond}: {region_rows[0]['count']} epochs, {stats_str}")
    
    elif y_lim is None:
        # Single-region epoch-level output for bootstrap
        signal_col = signal_cols[0]
        for idx, cond in enumerate(conditions):
            cond_epoch_df = epoch_df.filter(pl.col('condition') == cond)
            # Rename signal column to standard 'value' for bootstrap compatibility
            output_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col(signal_col).alias('value')])
            output_df.write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
            print(f"[amplitude]   {cond}: {len(cond_epoch_df)} epochs (epoch-level data)")
    else:
        # Single-region aggregated output (mean ± SEM) for direct plotting
        plot_ready_mode = True
        signal_col = signal_cols[0]
        all_plot_data = []
        
        for idx, cond in enumerate(conditions):
            cond_vals = epoch_df.filter(pl.col('condition') == cond)[signal_col].to_numpy()
            mean_val = float(np.mean(cond_vals))
            sem_val = float(np.std(cond_vals, ddof=1) / np.sqrt(len(cond_vals))) if len(cond_vals) > 1 else 0.0
            
            plot_df = pl.DataFrame({
                'condition': [cond],
                'x_data': [[method]],
                'y_data': [[mean_val]],
                'y_var': [[sem_val]],
                'plot_type': ['bar'],
                'x_label': [''],
                'y_label': [y_label],
                'y_ticks': [y_lim],
                'count': [len(cond_vals)]
            })
            plot_df.write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
            all_plot_data.append(plot_df)
            print(f"[amplitude]   {cond}: {mean_val:.3f} ± {sem_val:.3f} ({len(cond_vals)} epochs)")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='gzip')
    
    print(f"[amplitude] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: analyze_amplitude(a[1], a[2] if len(a) > 2 else 'peak_baseline', 
                                  float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None,
                                  a[4] if len(a) > 4 else 'Amplitude') if len(a) >= 2 else (
        print('[amplitude] Compute amplitude metrics (peak_baseline, mean, peak) per condition. Plot-ready output.\nUsage: amplitude_analyzer.py <epochs.parquet> [method=peak_baseline] [y_lim] [y_label]'), sys.exit(1)))(sys.argv)
