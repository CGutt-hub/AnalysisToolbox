import polars as pl, sys, os, numpy as np

def analyze_amplitude(ip: str, method: str = 'peak_baseline', y_lim: float | None = None, y_label: str = 'Amplitude', suffix: str = 'amp') -> str:
    """Analyze amplitude per condition: epoch-level or aggregated (mean ± SEM) output.
    Generic amplitude analyzer - works on any signal.
    
    Args:
        ip: Input parquet with epoched data (flat format: condition, epoch_id, time, signal_col)
        method: 'peak_baseline' (max - baseline), 'mean' (mean amplitude), 'peak' (max amplitude)
        y_lim: Optional Y-axis maximum (None = output epoch-level data for bootstrap)
        y_label: Label for y-axis (e.g., 'Conductance Change (μS)', 'Amplitude (mV)')
        suffix: Output file suffix (default 'amp', use 'eda' for EDA compatibility)
    
    Returns:
        Path to signal file
    
    Note: If y_lim is None, outputs epoch-level data for bootstrap; else outputs mean ± SEM
    """
    print(f"[amplitude] Amplitude analysis: {ip}, method={method}, mode={'epoch-level' if y_lim is None else 'aggregated'}")
    df = pl.read_parquet(ip)
    
    if 'condition' not in df.columns or 'epoch_id' not in df.columns:
        raise ValueError("Input must have 'condition' and 'epoch_id' columns")
    
    # Auto-detect signal column
    signal_col = [c for c in df.columns if c not in ['time', 'sfreq', 'epoch_id', 'condition']][0]
    conditions = sorted(df['condition'].unique().to_list())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    print(f"[amplitude] Processing {len(conditions)} conditions, signal column: {signal_col}")
    
    # Compute epoch-level values
    all_epoch_data = []
    for idx, cond in enumerate(conditions):
        cond_df = df.filter(pl.col('condition') == cond)
        epochs = cond_df['epoch_id'].unique().to_list()
        
        for eid in epochs:
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
            
            all_epoch_data.append({'condition': cond, 'epoch_id': eid, method: val})
    
    epoch_df = pl.DataFrame(all_epoch_data)
    
    # Output mode: epoch-level (for bootstrap) or aggregated (mean ± SEM)
    if y_lim is None:
        # Epoch-level output for bootstrap - rename method column to standard 'value' for bootstrap
        for idx, cond in enumerate(conditions):
            cond_epoch_df = epoch_df.filter(pl.col('condition') == cond)
            # Rename method column to 'value' for bootstrap compatibility
            output_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col(method).alias('value')])
            output_df.write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
            print(f"[amplitude]   {cond}: {len(cond_epoch_df)} epochs (epoch-level data)")
    else:
        # Aggregated output (mean ± SEM) for direct plotting
        for idx, cond in enumerate(conditions):
            cond_vals = epoch_df.filter(pl.col('condition') == cond)[method].to_numpy()
            mean_val = float(np.mean(cond_vals))
            sem_val = float(np.std(cond_vals, ddof=1) / np.sqrt(len(cond_vals))) if len(cond_vals) > 1 else 0.0
            
            pl.DataFrame({
                'condition': [cond],
                'x_data': [[method]],
                'y_data': [[mean_val]],
                'y_var': [[sem_val]],
                'plot_type': ['bar'],
                'x_label': [''],
                'y_label': [y_label],
                'y_ticks': [y_lim],
                'count': [len(cond_vals)]
            }).write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
            print(f"[amplitude]   {cond}: {mean_val:.3f} ± {sem_val:.3f} ({len(cond_vals)} epochs)")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path)
    
    print(f"[amplitude] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: analyze_amplitude(a[1], a[2] if len(a) > 2 else 'peak_baseline', 
                                  float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None,
                                  a[4] if len(a) > 4 else 'Amplitude',
                                  a[5] if len(a) > 5 else 'amp') if len(a) >= 2 else (
        print('[amplitude] Compute amplitude metrics (peak_baseline, mean, peak) per condition. Plot-ready output.\nUsage: amplitude_analyzer.py <epochs.parquet> [method=peak_baseline] [y_lim] [y_label] [suffix]'), sys.exit(1)))(sys.argv)
