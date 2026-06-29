import polars as pl, numpy as np, sys, os

# Logging helpers
def log_info(msg): print(f"[interval] INFO: {msg}")
def log_warning(msg): print(f"[interval] WARNING: {msg}")
def log_error(msg): print(f"[interval] ERROR: {msg}")

def analyze_intervals(ip: str, event_col: str | None = None, y_lim: float | None = None,
                      y_label: str = 'Value (ms)',
                      metrics_mode: str = 'auto',
                      output_format: str = 'signal_pointer') -> str:
    """
    Generic interval statistics analyzer — computes inter-event intervals (e.g., SDNN, RMSSD)
    on any point process: R-peaks, blinks, keystrokes, neural spike times, etc.
    
    Supports both epoched and flat input data. Outputs can be formatted for different
    downstream statistical workflows: direct joining, bootstrap resampling, or plotting.
    
    Args:
        ip: Input parquet with event data (epoched or flat)
        event_col: Column containing event samples/times (auto-detected if None)
        y_lim: Y-axis maximum (None = epoch-level data)
        y_label: Label for y-axis
        metrics_mode: 'auto' (both SDNN+RMSSD), 'SDNN', or 'RMSSD'
        output_format: 'signal_pointer' (default), 'flat_table' (for joining), or 'aggregated'
    
    Returns:
        Path to output parquet (format depends on output_format parameter)
    """
    suffix = 'interv'
    print(f"[interval] Interval analysis: {ip}, mode={'epoch-level' if y_lim is None else 'aggregated'}")
    df = pl.read_parquet(ip)
    
    if 'condition' not in df.columns or 'epoch_id' not in df.columns:
        log_error(f"Missing required columns. Available: {list(df.columns)}")
        log_error(f"Required: ['condition', 'epoch_id']. Please check input data.")
        raise ValueError("Input must have 'condition' and 'epoch_id' columns")
    
    # Auto-detect event column
    if event_col is None:
        candidates = ['R_Peak_Sample', 'rpeaks', 'peaks', 'events', 'samples']
        event_col = next((c for c in candidates if c in df.columns), None)
        if event_col is None:
            # Use first non-meta column
            meta_cols = ['time', 'sfreq', 'epoch_id', 'condition']
            event_col = [c for c in df.columns if c not in meta_cols][0]
    
    print(f"[interval] Using event column: {event_col}")
    sfreq = float(df['sfreq'][0]) if 'sfreq' in df.columns and len(df) > 0 else 1000.0
    
    # Handle empty DataFrame (no epochs to analyze)
    if len(df) == 0:
        log_warning("Empty input — no epochs to analyze, halting branch.")
        sys.exit(1)
    
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    conditions = sorted(df['condition'].unique().to_list())
    print(f"[interval] Processing {len(conditions)} conditions (sfreq={sfreq} Hz)")
    
    # Quality check: epoch counts per condition
    for cond in conditions:
        n_epochs = len(df.filter(pl.col('condition') == cond)['epoch_id'].unique())
        if n_epochs < 3:
            log_warning(f"Only {n_epochs} epoch(s) in {cond} (recommended minimum: 5 for reliable statistics)")
        elif n_epochs < 5:
            log_info(f"{n_epochs} epochs in {cond} (acceptable, but >5 preferred)")
    
    # Compute epoch-level metrics
    all_epoch_data = []
    for cond in conditions:
        cond_df = df.filter(pl.col('condition') == cond)
        epoch_ids = cond_df['epoch_id'].unique().to_list()
        
        for eid in epoch_ids:
            epoch_df = cond_df.filter(pl.col('epoch_id') == eid)
            events = epoch_df[event_col].to_numpy()
            
            if len(events) < 2:
                continue
            
            # Calculate inter-event intervals in milliseconds
            # Prefer the time column (absolute timestamps in seconds) over sample indices:
            # peak_sample indices are into the post-rejection array and are non-contiguous,
            # so np.diff(peak_sample) gives near-zero deltas across rejection gaps.
            if 'time' in epoch_df.columns and event_col != 'time':
                time_events = epoch_df.filter(pl.col('epoch_id') == eid).sort(event_col)['time'].to_numpy()
                intervals = np.diff(time_events) * 1000.0  # seconds → ms
            else:
                intervals = np.diff(events) / sfreq * 1000.0  # samples → ms
            
            if len(intervals) < 2:
                continue
            
            # SDNN: Standard deviation of intervals
            sdnn_val = float(np.std(intervals, ddof=1))
            
            # RMSSD: Root mean square of successive differences
            rmssd_val = float(np.sqrt(np.mean(np.diff(intervals) ** 2)))
            
            # Quality check: high variance in IBIs suggests artifacts
            ibi_mean = float(np.mean(intervals))
            ibi_std = sdnn_val
            if ibi_mean > 0 and (ibi_std / ibi_mean) > 0.7:
                log_warning(f"{cond} epoch {eid}: High IBI variability (CV={ibi_std/ibi_mean:.2f}), possible artifacts")
            
            all_epoch_data.append({'condition': cond, 'epoch_id': eid, 'SDNN': sdnn_val, 'RMSSD': rmssd_val})
    
    epoch_df = pl.DataFrame(all_epoch_data)
    
    # Output mode: epoch-level (for bootstrap) or aggregated (mean ± SEM)
    if y_lim is None:
        # Epoch-level output for bootstrap (long format with metric column for SDNN/RMSSD)
        for idx, cond in enumerate(conditions):
            cond_epoch_df = epoch_df.filter(pl.col('condition') == cond)
            if len(cond_epoch_df) == 0:
                print(f"[interval] Warning: {cond} has no valid epochs, skipping")
                continue
            
            # Determine which metrics to output based on metrics_mode
            if metrics_mode.upper() == 'SDNN':
                # Keep only SDNN column (renamed to 'value' for bootstrap)
                output_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col('SDNN').alias('value')])
            elif metrics_mode.upper() == 'RMSSD':
                # Keep only RMSSD column (renamed to 'value' for bootstrap)
                output_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col('RMSSD').alias('value')])
            else:
                # Both metrics - output in long format with 'metric' column
                # Bootstrap will need to handle this by grouping on both condition and metric
                sdnn_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col('SDNN').alias('value')]).with_columns(pl.lit('SDNN').alias('metric'))
                rmssd_df = cond_epoch_df.select(['condition', 'epoch_id', pl.col('RMSSD').alias('value')]).with_columns(pl.lit('RMSSD').alias('metric'))
                output_df = pl.concat([sdnn_df, rmssd_df])
            
            output_df.write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"), compression='gzip')
            n_epochs = len(cond_epoch_df)
            print(f"[interval]   {cond}: {n_epochs} epochs, {len(output_df)} rows (epoch-level data)")
    else:
        # Aggregated output (mean ± SEM) for direct plotting
        for idx, cond in enumerate(conditions):
            cond_epoch_df = epoch_df.filter(pl.col('condition') == cond)
            if len(cond_epoch_df) == 0:
                print(f"[interval] Warning: {cond} has no valid epochs, skipping")
                continue
            
            sdnn_vals = cond_epoch_df['SDNN'].to_numpy()
            rmssd_vals = cond_epoch_df['RMSSD'].to_numpy()
            
            sdnn_mean = float(np.mean(sdnn_vals))
            sdnn_sem = float(np.std(sdnn_vals, ddof=1) / np.sqrt(len(sdnn_vals))) if len(sdnn_vals) > 1 else 0.0
            rmssd_mean = float(np.mean(rmssd_vals))
            rmssd_sem = float(np.std(rmssd_vals, ddof=1) / np.sqrt(len(rmssd_vals))) if len(rmssd_vals) > 1 else 0.0
            
            # Build output based on metrics_mode
            if metrics_mode.upper() == 'SDNN':
                x_data, y_data, y_var = ['SDNN'], [sdnn_mean], [sdnn_sem]
            elif metrics_mode.upper() == 'RMSSD':
                x_data, y_data, y_var = ['RMSSD'], [rmssd_mean], [rmssd_sem]
            else:  # auto - both metrics
                x_data, y_data, y_var = ['SDNN', 'RMSSD'], [sdnn_mean, rmssd_mean], [sdnn_sem, rmssd_sem]
            
            output = pl.DataFrame({
                'condition': [str(cond)],
                'x_data': [x_data],
                'y_data': [y_data],
                'y_var': [y_var],
                'plot_type': ['bar'],
                'x_label': ['Interval Metric'],
                'y_label': [y_label],
                'y_ticks': [y_lim]
            })
            
            out_path = os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet")
            output.write_parquet(out_path, compression='gzip')
            print(f"[interval]   {cond}: SDNN={sdnn_mean:.2f}±{sdnn_sem:.2f}, RMSSD={rmssd_mean:.2f}±{rmssd_sem:.2f} ({len(sdnn_vals)} epochs)")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")

    # Output format routing: 'signal_pointer' (default) for bootstrap workflows,
    # 'flat_table' for correlation/statistical joining, 'aggregated' for direct plots.
    if output_format == 'flat_table' and y_lim is None:
        # Emit raw epoch-level data as a joinable table for downstream statistical
        # processes (e.g., correlation_analyzer, custom group-level analyses).
        if metrics_mode.upper() == 'RMSSD':
            out_df = epoch_df.select(['condition', 'epoch_id', pl.col('RMSSD').alias('value')])
        elif metrics_mode.upper() == 'SDNN':
            out_df = epoch_df.select(['condition', 'epoch_id', pl.col('SDNN').alias('value')])
        else:  # auto: long format with metric column
            sdnn_out = epoch_df.select(['condition', 'epoch_id', pl.col('SDNN').alias('value')]).with_columns(pl.lit('SDNN').alias('metric'))
            rmssd_out = epoch_df.select(['condition', 'epoch_id', pl.col('RMSSD').alias('value')]).with_columns(pl.lit('RMSSD').alias('metric'))
            out_df = pl.concat([sdnn_out, rmssd_out])
        out_df.write_parquet(signal_path, compression='gzip')
        print(f"[interval] Output (flat_table): {signal_path} ({len(out_df)} rows for joining)")
    else:
        # Default: signal pointer format. Per-condition epoch files written to subfolder;
        # root file points to folder for bootstrap-resampling workflows.
        pl.DataFrame({
            'signal': [1],
            'source': [os.path.basename(ip)],
            'conditions': [len(conditions)],
            'folder_path': [os.path.abspath(out_folder)]
        }).write_parquet(signal_path, compression='gzip')
        print(f"[interval] Output (signal_pointer): {signal_path}")

    return signal_path

if __name__ == '__main__':
    (lambda a: analyze_intervals(a[1],
                                  a[2] if len(a) > 2 and a[2] and a[2] != 'None' else None,
                                  float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None,
                                  a[4] if len(a) > 4 else 'Value (ms)',
                                  a[5] if len(a) > 5 and a[5] not in ('signal_pointer', 'flat_table') else 'auto',
                                  next((x for x in a[5:] if x in ('signal_pointer', 'flat_table')), 'signal_pointer')) if len(a) >= 2 else (
        print('Compute interval statistics for any point process (SDNN, RMSSD, etc.)'),
        print('[interval] Usage: interval_analyzer.py <events.parquet> [event_col] [y_lim] [y_label] [metrics] [output_format]'),
        print('[interval]   output_format: signal_pointer (default) or flat_table'),
        print('[interval] Example: interval_analyzer.py peaks.parquet peak_sample None "ms" auto flat_table'),
        sys.exit(1)))(sys.argv)
