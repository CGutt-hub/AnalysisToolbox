import sys, os, polars as pl, numpy as np, warnings
from typing import Dict, List, Tuple
from decimal import Decimal

warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[epoching] INFO: {msg}")
def log_warning(msg): print(f"[epoching] WARNING: {msg}")
def log_error(msg): print(f"[epoching] ERROR: {msg}")

def _epoch_mne(raw, events: Dict[str, List[Tuple[float, float]]], data_path: str, rec_start: float = 0.0, event_sfreq: float | None = None) -> str:
    import mne
    
    print(f"[epoching] MNE Raw: {len(raw.times)} samples, {len(raw.ch_names)} ch, {raw.info['sfreq']} Hz")
    print(f"[epoching] Events: {sum(len(v) for v in events.values())} epochs")
    
    # Detect event time format
    all_times = [t for pairs in events.values() for start, stop in pairs for t in (start, stop)]
    max_event, max_samples = max(all_times), len(raw.times)
    data_sfreq = raw.info['sfreq']
    rec_duration = len(raw.times) / data_sfreq
    
    # Determine if events are absolute samples (need offset), relative samples, seconds, or milliseconds
    # Key: if event_sfreq is provided and differs from data_sfreq, scale accordingly
    if max_event > max_samples * 0.1:  # Sample range - events are in sample numbers
        # Estimate event sampling rate from max values if not provided
        if event_sfreq is None:
            # Assume events match recording duration - derive implied sample rate
            event_sfreq = max_event / rec_duration if rec_duration > 0 else data_sfreq
        
        is_absolute = max_event > max_samples
        # Convert from event sample space to data sample space via time
        if abs(event_sfreq - data_sfreq) > 1.0:  # Different sample rates
            # Event sample -> seconds -> data sample
            convert = lambda t: int(((t / event_sfreq) - rec_start) * data_sfreq)
            print(f"[epoching] Events: samples at {event_sfreq:.1f} Hz, converting to {data_sfreq:.1f} Hz")
        elif is_absolute:
            convert = lambda t: int((t / data_sfreq - rec_start) * data_sfreq)
            print(f"[epoching] Events: absolute samples (offset: {int(rec_start * data_sfreq)})")
        else:
            convert = lambda t: int(t)
            print(f"[epoching] Events: relative samples")
    elif max_event / raw.times[-1] > 10:  # Milliseconds
        convert = lambda t: int(t * data_sfreq / 1000.0)
        print(f"[epoching] Events: milliseconds")
    else:  # Seconds
        convert = lambda t: int(t * data_sfreq)
        print(f"[epoching] Events: seconds")
    
    # Build MNE events array
    event_id, event_counter, event_list = {}, 1, []
    for condition, pairs in sorted(events.items()):
        if condition not in event_id:
            event_id[condition] = event_counter
            event_counter += 1
        for start, stop in pairs:
            sample = convert(start)
            if 0 <= sample < max_samples:
                event_list.append([sample, 0, event_id[condition]])
            else:
                print(f"[epoching] Warning: {condition} epoch at sample {sample} out of range (0-{max_samples})")
    
    if not event_list:
        print(f"[epoching] Error: No valid events")
        return ""
    
    mne_events = np.array(sorted(event_list, key=lambda x: x[0]), dtype=int)
    
    # Calculate epoch duration from first pair
    first_start, first_stop = events[sorted(events.keys())[0]][0]
    tmax = (convert(first_stop) - convert(first_start)) / raw.info['sfreq']
    
    print(f"[epoching] Epoching: 0-{tmax:.1f}s, {len(mne_events)} valid events")
    
    # Check which epochs might be dropped due to data boundary
    for evt in mne_events:
        sample, _, cond_id = evt
        end_sample = sample + int(tmax * raw.info['sfreq'])
        if end_sample > max_samples:
            cond_name = [k for k, v in event_id.items() if v == cond_id][0]
            print(f"[epoching] Warning: {cond_name} epoch at sample {sample} will be truncated (end {end_sample} > {max_samples})")
    
    # Create and flatten epochs
    epochs_obj = mne.Epochs(raw, mne_events, event_id=event_id, tmin=0.0, tmax=tmax, 
                           baseline=None, preload=True, verbose=False)
    
    print(f"[epoching] Created: {len(epochs_obj)} epochs")
    
    # Log which epochs were actually created vs dropped
    if len(epochs_obj) < len(mne_events):
        dropped = len(mne_events) - len(epochs_obj)
        # Count epochs per condition
        created_counts = {cond: len(epochs_obj[cond]) for cond in event_id.keys()}
        requested_counts = {cond: sum(1 for e in mne_events if e[2] == event_id[cond]) for cond in event_id.keys()}
        for cond in event_id.keys():
            if created_counts[cond] < requested_counts[cond]:
                print(f"[epoching] Warning: {cond} lost {requested_counts[cond] - created_counts[cond]} epoch(s) (had {requested_counts[cond]}, got {created_counts[cond]})")
    
    dfs = []
    for cond in sorted(event_id.keys()):
        for idx, epoch_data in enumerate(epochs_obj[cond].get_data()):
            dfs.append(pl.DataFrame({
                'condition': [cond] * len(epochs_obj.times),
                'epoch_id': [f"{cond}_{idx}"] * len(epochs_obj.times),
                'time': epochs_obj.times,
                **{ch: epoch_data[i, :] for i, ch in enumerate(raw.ch_names)}
            }))
    
    out = f"{os.path.splitext(os.path.basename(data_path))[0]}_epochs.parquet"
    (pl.concat(dfs) if dfs else pl.DataFrame()).write_parquet(out)
    print(f"[epoching] Output: {out} ({len(pl.concat(dfs)) if dfs else 0} rows)")
    return out

def window_epochs(data_path: str, window_size: float = 30.0, step_size: float = 10.0) -> str:
    """Create sliding windows from existing epochs (for bootstrap resampling).
    
    Args:
        data_path: Parquet file with epoched data (columns: condition, epoch_id, time, ...)
        window_size: Window duration in seconds
        step_size: Step size for sliding window in seconds
    
    Returns:
        Path to windowed parquet file
    """
    print(f"[epoching] Windowing mode: window={window_size}s, step={step_size}s")
    df = pl.read_parquet(data_path)

    # Validate structure for time-series windowing
    required = ['condition', 'epoch_id', 'time']
    missing = [c for c in required if c not in df.columns]
    if missing:
        log_warning(f"Input not epoched (missing: {missing}) - windowing requires pre-epoched data")
        # Create empty output signal file
        base = os.path.splitext(os.path.basename(data_path))[0]
        signal_path = f"{base}_windowed.parquet"
        pl.DataFrame({'signal': [1]}).write_parquet(signal_path)
        return signal_path
    
    data_cols = [c for c in df.columns if c not in ['condition', 'epoch_id', 'time']]
    if not data_cols:
        log_error("No data columns found")
        sys.exit(1)
    
    # Get unique epochs
    epochs = df.select(['condition', 'epoch_id']).unique().to_dicts()
    print(f"[epoching] Processing {len(epochs)} epochs with {len(data_cols)} channels")
    
    windowed_dfs = []
    total_windows = 0
    
    for epoch in epochs:
        cond = epoch['condition']
        eid = epoch['epoch_id']
        
        epoch_df = df.filter(
            (pl.col('condition') == cond) & 
            (pl.col('epoch_id') == eid)
        ).sort('time')
        
        if len(epoch_df) == 0:
            continue
        
        times = epoch_df['time'].to_numpy()
        t_min, t_max = times.min(), times.max()
        duration = t_max - t_min
        
        if duration < window_size:
            log_warning(f"{eid}: Duration {duration:.1f}s < window {window_size}s, skipping")
            continue
        
        # Generate window starts
        window_starts = np.arange(t_min, t_max - window_size + 0.001, step_size)
        
        for win_idx, win_start in enumerate(window_starts):
            win_end = win_start + window_size
            window_data = epoch_df.filter(
                pl.col('time').is_between(float(win_start), float(win_end), closed='both')
            )
            
            if len(window_data) == 0:
                continue
            
            window_id = f"{eid}_w{win_idx}"
            window_data = window_data.with_columns(pl.lit(window_id).alias('epoch_id'))
            windowed_dfs.append(window_data)
        
        total_windows += len(window_starts)
    
    if not windowed_dfs:
        if len(epochs) == 0:
            log_warning("No epochs in input data - creating empty output")
        else:
            log_warning(f"No windows created from {len(epochs)} epochs (all too short for {window_size}s window?)")
        base = os.path.splitext(os.path.basename(data_path))[0]
        out = f"{base}_windowed.parquet"
        # Write minimal schema so downstream readers don't crash
        signal_cols = data_cols if data_cols else ['value']
        empty = {c: pl.Series([], dtype=pl.Float64) for c in signal_cols}
        empty.update({'condition': pl.Series([], dtype=pl.Utf8),
                      'epoch_id': pl.Series([], dtype=pl.Utf8),
                      'time': pl.Series([], dtype=pl.Float64)})
        pl.DataFrame(empty).write_parquet(out)
        print(f"[epoching] Output: {out} (0 rows, 0 windows)")
        return out
    
    result_df = pl.concat(windowed_dfs)
    
    # Log window counts
    window_counts = result_df.select(['condition', 'epoch_id']).unique().group_by('condition').agg(
        pl.count('epoch_id').alias('n_windows')
    ).to_dicts()
    for wc in window_counts:
        print(f"[epoching] {wc['condition']}: {wc['n_windows']} windows")
    
    base = os.path.splitext(os.path.basename(data_path))[0]
    out = f"{base}_windowed.parquet"
    result_df.write_parquet(out)
    print(f"[epoching] Output: {out} ({len(result_df)} rows, {total_windows} windows)")
    return out

def epoch_and_flatten(data_path: str, events_path: str, orig_path: str | None = None, mode: str = 'events', window_size: float = 30.0, step_size: float = 10.0) -> str:
    """Segment data into epochs.
    
    Args:
        data_path: Input data file (.fif or .parquet)
        events_path: Events file (.parquet) - only used for mode='events'
        orig_path: Original data path for time offset - only used for mode='events'
        mode: 'events' (default) or 'sliding' for sliding windows
        window_size: Window duration in seconds (for mode='sliding')
        step_size: Step size in seconds (for mode='sliding')
    """
    # Sliding window mode
    if mode == 'sliding':
        return window_epochs(data_path, window_size, step_size)
    
    # Event-based epoching (original behavior)
    events = pl.read_parquet(events_path)['data'][0]
    
    if data_path.endswith('.fif'):
        import mne
        print(f"[epoching] Loading: {data_path}")
        raw = mne.io.read_raw_fif(data_path, preload=True, verbose=False)
        
        # Get recording start time
        rec_start = 0.0
        if orig_path and os.path.exists(orig_path):
            rec_start = float(pl.read_parquet(orig_path)['time'][0])
        else:
            base = os.path.splitext(os.path.basename(data_path))[0].split('_reref')[0].split('_filt')[0].split('_regr')[0]
            for path in [f"{os.path.dirname(data_path)}/{base}.parquet", f"{os.path.dirname(data_path)}/{base}/{base}.parquet"]:
                if os.path.exists(path):
                    rec_start = float(pl.read_parquet(path)['time'][0])
                    break
        
        if rec_start > 0:
            print(f"[epoching] Recording start: {rec_start:.1f}s")
        return _epoch_mne(raw, events, data_path, rec_start)
    
    # Parquet data
    df = pl.read_parquet(data_path)
    time_col = 'time' if 'time' in df.columns else df.columns[0]
    data_cols = [c for c in df.columns if c != time_col]
    
    print(f"[epoching] Data: {len(df)} samples, {len(data_cols)} ch")
    print(f"[epoching] Events: {sum(len(v) for v in events.values())} epochs")
    
    # Unit normalization - cast to numeric types explicitly
    data_min_val = df[time_col].min()
    data_max_val = df[time_col].max()
    
    def to_float(val) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        elif isinstance(val, Decimal):
            return float(val)
        elif isinstance(val, str):
            return float(val)
        else:
            return 0.0
    
    data_min = to_float(data_min_val)
    data_max = to_float(data_max_val)
    event_max = float(max(sp for eps in events.values() for _, sp in eps))
    scale_data, scale_event = (1.0, 1000.0) if data_max * 10 < event_max else (1000.0, 1.0) if event_max * 10 < data_max else (1.0, 1.0)
    
    print(f"[epoching] Time ranges: data={data_max:.1f}, events={event_max:.1f}, scales: {scale_data}×{scale_event}")
    
    out = f"{os.path.splitext(os.path.basename(data_path))[0]}_epochs.parquet"
    
    dfs = [
        pl.DataFrame({
            'condition': [c] * len(arr),
            'epoch_id': [f"{c}_{i}"] * len(arr),
            time_col: [r[0] for r in arr],
            **{data_cols[j]: [r[j+1] for r in arr] for j in range(len(data_cols))}
        })
        for c, pairs in events.items()
        for i, arr in enumerate([
            df.filter((pl.col(time_col) * scale_data >= st / scale_event).and_(
                     pl.col(time_col) * scale_data <= sp / scale_event))
            .select([time_col] + data_cols).to_numpy().tolist() 
            for st, sp in pairs
        ]) if arr
    ]
    
    if not dfs:
        log_warning(f"No epochs created - all epoch windows were empty or out of range")
        print(f"[epoching] Data range: {data_min:.1f} - {data_max:.1f} (scaled: {data_min * scale_data:.1f} - {data_max * scale_data:.1f})")
        print(f"[epoching] Event range: {min(st for eps in events.values() for st, _ in eps):.1f} - {max(sp for eps in events.values() for _, sp in eps):.1f} (scaled: {min(st for eps in events.values() for st, _ in eps) / scale_event:.1f} - {max(sp for eps in events.values() for _, sp in eps) / scale_event:.1f})")
        print(f"[epoching] Check that event times and data times are in the same units (may occur if recording started late or after extensive artifact rejection)")
        print(f"[epoching] Creating empty output file (0 epochs)")
        # Create empty output with proper schema
        result_df = pl.DataFrame({
            'condition': [],
            'epoch_id': [],
            time_col: [],
            **{col: [] for col in data_cols}
        })
    else:
        result_df = pl.concat(dfs)
    result_df.write_parquet(out)
    # Generate inline visualization - show example epochs from first few conditions
    if dfs and len(result_df) > 0:
        signal_cols: list[str] = [c for c in result_df.columns if c not in [time_col, 'condition', 'epoch_id', 'sfreq']]
        if signal_cols:
            # Sample first few epochs from first few conditions
            vis_conds: list[str] = result_df['condition'].unique().to_list()[:3]
            vis_sample = result_df.filter(pl.col('condition').is_in(vis_conds)).head(1000)
            time_data: list[float] = vis_sample[time_col].to_list()
            y_data: list[list[float]] = [vis_sample[col].to_list() for col in signal_cols[:5]]  # Max 5 channels
            if len(time_data) > 5000:
                step: int = len(time_data) // 5000
                time_data = time_data[::step]
                y_data = [yd[::step] for yd in y_data]
            vis_df = pl.DataFrame({
                'x_data': [[[time_data] for _ in range(len(y_data))]],
                'y_data': [y_data],
                'plot_type': ['line'],
                'labels': [signal_cols[:5]],
                'x_label': ['Time (s)'],
                'y_label': ['Amplitude']
            })
            vis_df.write_parquet(out.replace('.parquet', '_vis.parquet'))
    print(f"[epoching] Output: {out} ({len(result_df) if dfs else 0} rows)")
    return out

if __name__ == '__main__':
    def main(args):
        if len(args) < 2:
            print('[epoching] Segment data into epochs.')
            print('Event mode: epoching_processor.py <data.fif|parquet> <events.parquet> [original.parquet]')
            print('Sliding mode: epoching_processor.py <epochs.parquet> sliding [window_size=30.0] [step_size=10.0]')
            sys.exit(1)
        
        # Detect mode
        if len(args) >= 3 and args[2] == 'sliding':
            # Sliding window mode
            epoch_and_flatten(
                args[1], 
                '', 
                None,
                mode='sliding',
                window_size=float(args[3]) if len(args) > 3 else 30.0,
                step_size=float(args[4]) if len(args) > 4 else 10.0
            )
        else:
            # Event-based mode
            epoch_and_flatten(args[1], args[2], args[3] if len(args) > 3 else None, mode='events')
    
    main(sys.argv)
