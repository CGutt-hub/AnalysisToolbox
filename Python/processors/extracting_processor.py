import polars as pl, sys, os, mne, numpy as np, warnings
from typing import Any, cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[extracting] INFO: {msg}")
def log_warning(msg): print(f"[extracting] WARNING: {msg}")
def log_error(msg): print(f"[extracting] ERROR: {msg}")

def resolve(s: str, dc: list[str]) -> list[str]:
    s = s.strip().rstrip(',').strip()
    if not dc or dc == ['empty']:
        log_warning(f"Input has no data columns, skipping selector '{s}'")
        return []
    
    # Handle exclusion patterns (e.g., "8:-1 -EKG" means "8 to end, but exclude EKG")
    parts = s.split()
    if len(parts) > 1:
        # First part is the main selector, rest are exclusions
        main_selector = parts[0]
        exclusions = [p[1:].lower() for p in parts[1:] if p.startswith('-')]
        
        # Resolve main pattern
        selected = resolve(main_selector, dc)
        
        # Filter out exclusions
        filtered = [c for c in selected if not any(excl in c.lower() for excl in exclusions)]
        
        if len(filtered) < len(selected):
            log_info(f"Excluded {len(selected) - len(filtered)} channels matching: {exclusions}")
        
        return filtered
    
    if ':' in s:
        p = s.split(':')
        return dc[int(p[0].strip() or 0) - 1 if p[0].strip() else 0 : (int(p[1].strip()) if len(p) > 1 and p[1].strip() else len(dc))]
    if s.lstrip('-').isdigit():
        idx = int(s)
        return [dc[idx - 1]] if idx > 0 else [dc[len(dc) + idx]]
    matched = ([c for c in dc if c.lower() == s.lower()] or [c for c in dc if s.lower() in c.lower()] or [c for c in dc if c.lower().startswith(s.lower())])
    if not matched:
        log_warning(f"Selector '{s}' matched no columns from: {dc}")
        return []
    return matched[:]

def load_input(ip: str, needed_channels: list[str] | None = None) -> tuple[pl.DataFrame, dict[str, str] | None]:
    """Load input file, optionally filtering to specific channels for memory efficiency.
    
    Args:
        ip: Input file path (.fif or .parquet)
        needed_channels: List of channel names to load (None = load all). For FIF files only.
    
    Returns:
        Tuple of (DataFrame, channel_types_dict)
    """
    if ip.endswith('.parquet'):
        df = pl.read_parquet(ip)
        # Filter to needed channels if specified
        if needed_channels is not None:
            available = [c for c in needed_channels if c in df.columns]
            cols_to_keep = ['time'] + available if 'time' in df.columns else available
            df = df.select(cols_to_keep)
        return df, None
    
    # Load FIF file with selective channel loading for memory efficiency
    raw = mne.io.read_raw_fif(ip, preload=False, verbose=False)
    
    # Determine which channels to actually load
    if needed_channels is not None:
        # Find channels that exist in the file
        picks = [ch for ch in needed_channels if ch in raw.ch_names]
        if not picks:
            log_warning(f"None of the requested channels {needed_channels} found in file")
            picks = raw.ch_names  # Fallback to all
    else:
        picks = raw.ch_names
    
    log_info(f"Loading {len(picks)}/{len(raw.ch_names)} channels from FIF file")
    
    # Only load selected channels
    raw.pick(picks)
    raw.load_data(verbose=False)
    
    # Get data efficiently
    data_array = cast(np.ndarray, raw.get_data())
    ch_types = {ch: t for ch, t in zip(raw.ch_names, raw.get_channel_types())}
    
    # Build dataframe
    df_dict = {'time': raw.times}
    df_dict.update({ch: data_array[i] for i, ch in enumerate(raw.ch_names)})
    df = pl.DataFrame(df_dict)
    
    del data_array, raw  # Explicit cleanup
    return df, ch_types

def determine_needed_channels(sels: list[str], all_channels: list[str]) -> list[str]:
    """Pre-scan selectors to determine which channels will be needed.
    
    Args:
        sels: List of selector strings
        all_channels: All available channel names (without 'time')
    
    Returns:
        List of channel names that will be selected
    """
    needed = set()
    for s in sels:
        # Quick parse to estimate needed channels
        s_clean = s.strip().split()[0]  # Remove exclusions for estimation
        
        if ':' in s_clean:
            # Range selector - need to estimate
            parts = s_clean.split(':')
            start_idx = int(parts[0].strip() or 0) - 1 if parts[0].strip() else 0
            end_idx = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else len(all_channels)
            needed.update(all_channels[max(0, start_idx):min(len(all_channels), end_idx)])
        elif s_clean.lstrip('-').isdigit():
            # Single index
            idx = int(s_clean)
            if 0 < idx <= len(all_channels):
                needed.add(all_channels[idx - 1])
            elif idx < 0 and -idx <= len(all_channels):
                needed.add(all_channels[idx])
        else:
            # Name-based selector - add all matching channels
            matched = [c for c in all_channels if s_clean.lower() in c.lower()]
            needed.update(matched)
    
    return list(needed) if needed else all_channels  # Fallback to all if nothing matched

def save_fif(od: pl.DataFrame, pp: str, fp: str, chs: list[str], t: np.ndarray | None, sf: float, ch_types: dict[str, str] | None) -> None:
    od.write_parquet(pp, compression='snappy')
    print(f"[extracting] {os.path.basename(pp)} cols={od.columns}")
    # Generate inline visualization (downsample BEFORE converting to lists to save memory)
    if chs and 'time' in od.columns:
        if len(od) > 10000:
            step: int = len(od) // 10000
            od_vis = od[::step]
        else:
            od_vis = od
        time_data: list[float] = od_vis['time'].to_list()
        y_data: list[list[float]] = [od_vis[col].to_list() for col in chs]
        del od_vis  # Free memory immediately
        vis_df = pl.DataFrame({
            'x_data': [[[time_data] for _ in range(len(chs))]],
            'y_data': [y_data],
            'plot_type': ['line'],
            'labels': [chs],
            'x_label': ['Time (s)'],
            'y_label': ['Amplitude']
        })
        vis_df.write_parquet(pp.replace('.parquet', '_vis.parquet'), compression='snappy')
        del time_data, y_data, vis_df  # Free memory
    if not chs:
        mne.io.RawArray(np.array([[0.0]]), mne.create_info(['empty'], 1.0, ch_types='misc'), verbose=False).save(fp, overwrite=True, verbose=False)
    else:
        cht_list = [ch_types.get(c, 'misc') for c in chs] if ch_types else ['misc'] * len(chs)
        info = mne.create_info(chs, sf, ch_types=cast(Any, cht_list))
        data_array = od.select(chs).to_numpy().T
        del od  # Free DataFrame memory before creating RawArray
        raw = mne.io.RawArray(data_array, info, verbose=False)
        del data_array  # Free array memory
        raw.save(fp, overwrite=True, verbose=False)
        del raw  # Free RawArray memory
    print(f"[extracting] {os.path.basename(fp)}")

def run(ip: str, sels: list[str]) -> str:
    """Run extraction processor with memory-efficient channel loading.
    
    Args:
        ip: Input file path
        sels: List of selector strings
    
    Returns:
        Path to signal file
    """
    b = os.path.splitext(os.path.basename(ip))[0]
    wr = os.getcwd()
    of = os.path.join(wr, f"{b}_extr")
    os.makedirs(of, exist_ok=True)
    
    print(f"[extracting] Processing {b} with {len(sels)} selectors")
    
    # For FIF files, pre-determine which channels we need to avoid loading everything
    needed_channels = None
    if ip.endswith('.fif'):
        try:
            # Quick peek at channel names without loading data
            raw_info = mne.io.read_raw_fif(ip, preload=False, verbose=False)
            all_ch = raw_info.ch_names
            needed_channels = determine_needed_channels(sels, all_ch)
            log_info(f"Pre-scan: Will load {len(needed_channels)}/{len(all_ch)} channels")
            del raw_info
        except Exception as e:
            log_warning(f"Could not pre-scan channels: {e}, will load all")
    
    # Load input with selective channel loading
    df, ch_types = load_input(ip, needed_channels)
    
    print(f"[extracting] Available channels: {df.columns[1:] if df.columns and df.columns[0].lower() == 'time' else df.columns}")
    
    # Get data columns (excluding 'time')
    data_cols = df.columns[1:] if df.columns and df.columns[0].lower() == 'time' else df.columns
    
    # Process each selector
    for i, s in enumerate(sels):
        sc = resolve(s, data_cols)
        if not sc:
            continue
        
        # Select channels
        od = df.select(['time'] + sc) if 'time' in df.columns else df.select(sc)
        chs = [c for c in od.columns if c != 'time']
        
        # Compute sampling frequency
        if 'time' in df.columns and len(df['time']) > 1:
            t = od['time'].to_numpy()
            sf = float(1.0 / np.median(np.diff(t)))
        else:
            t = None
            sf = 1.0
        
        # Save outputs
        pp = os.path.join(of, f"{b}_extr{i+1}.parquet")
        fp = os.path.join(of, f"{b}_extr{i+1}.fif")
        save_fif(od, pp, fp, chs, t, sf, ch_types)
    
    # Create signal file
    signal_path = os.path.join(wr, f"{b}_extr.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'streams': [len(sels)],
        'folder_path': [os.path.abspath(of)]
    }).write_parquet(signal_path, compression='snappy')
    
    print(f"[extracting] Extraction finished: {b}_extr.parquet")
    return signal_path

if __name__ == '__main__': (lambda a: run(a[1], a[2:]) if len(a) >= 3 else (print('[extracting] Extract/select columns from data files into separate outputs.\nUsage: extracting_processor.py <input.fif|parquet> <selector1> [selector2 ...]'), sys.exit(1)))(sys.argv)
