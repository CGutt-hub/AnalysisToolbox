import polars as pl, numpy as np, sys, os, json, fnmatch, re

def analyze_groups(ip: str, groups_config: str, y_lim: float | None = None, 
                   x_label: str = 'Group', y_label: str = 'Mean', suffix: str = 'grp',
                   baseline_sec: float = 2.0) -> str:
    """
    Aggregate channels by groups and compute group-level statistics per condition.
    Generic grouping analyzer - works on any epoched data with channel columns.
    
    Args:
        ip: Input parquet with epoched data [condition, epoch_id, time, channel_cols...]
        groups_config: JSON string defining groups, or 'auto' for auto-detection.
                       Channel patterns support: exact, glob (*, ?, []), regex (re:pattern)
                       Example: {"Left PFC": ["re:^[12]-"], "Right PFC": ["re:^[34]-"]}
        y_lim: Optional Y-axis limit (symmetric around zero)
        x_label: Label for x-axis (e.g., 'ROI', 'Region', 'Group')
        y_label: Label for y-axis (e.g., 'Mean Value', 'Amplitude')
        suffix: Output file suffix (default 'grp')
        baseline_sec: Seconds at epoch start for baseline correction (default 2.0)
    
    Returns:
        Path to signal file
    """
    print(f"[group] Group analysis: {ip}")
    
    # Parse groups config
    if groups_config.lower() == 'auto':
        groups = {}
    elif os.path.isfile(groups_config):
        with open(groups_config) as f:
            groups = json.loads(f.read())
    else:
        groups = json.loads(groups_config)
    
    df = pl.read_parquet(ip)
    meta_cols = ['time', 'sfreq', 'epoch_id', 'condition']
    all_ch_cols = [c for c in df.columns if c not in meta_cols]
    
    # Auto-detect groups if needed
    if not groups:
        groups = _auto_detect_groups(all_ch_cols)
    
    # Validate and match groups
    valid_groups = {}
    for name, patterns in groups.items():
        matched = []
        for pattern in patterns:
            if pattern.startswith('re:'):
                regex = re.compile(pattern[3:])
                matched.extend([ch for ch in all_ch_cols if regex.search(ch) and ch not in matched])
            elif '*' in pattern or '?' in pattern or '[' in pattern:
                matched.extend([ch for ch in all_ch_cols if fnmatch.fnmatch(ch, pattern) and ch not in matched])
            else:
                # Exact match first, then prefix match
                if pattern in all_ch_cols:
                    matched.append(pattern)
                else:
                    matched.extend([ch for ch in all_ch_cols if ch.startswith(pattern) and ch not in matched])
        if matched:
            valid_groups[name] = matched
    
    if not valid_groups:
        valid_groups = _auto_detect_groups(all_ch_cols)
    
    group_names = list(valid_groups.keys())
    conditions = sorted(df['condition'].unique().to_list())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    # Determine sampling frequency for baseline
    sfreq = float(df['sfreq'][0]) if 'sfreq' in df.columns else None
    if sfreq is None and 'time' in df.columns:
        times = df['time'].unique().sort().to_list()
        if len(times) > 1:
            sfreq = 1.0 / (times[1] - times[0])
    baseline_samples = int(baseline_sec * sfreq) if sfreq else 0
    
    print(f"[group] Groups: {group_names}, Conditions: {conditions}")
    if baseline_samples > 0:
        print(f"[group] Baseline: {baseline_sec}s ({baseline_samples} samples)")
    
    for idx, cond in enumerate(conditions):
        cond_df = df.filter(pl.col('condition') == cond)
        epochs = cond_df['epoch_id'].unique().to_list()
        
        group_means, group_sems = [], []
        for group_name in group_names:
            group_channels = valid_groups[group_name]
            if not group_channels:
                group_means.append(0.0)
                group_sems.append(0.0)
                continue
            
            epoch_values = []
            for eid in epochs:
                epoch_df = cond_df.filter(pl.col('epoch_id') == eid)
                group_data = epoch_df.select(group_channels).to_numpy()
                
                if baseline_samples > 0 and group_data.shape[0] > baseline_samples:
                    baseline_mean = group_data[:baseline_samples, :].mean(axis=0, keepdims=True)
                    group_data = group_data - baseline_mean
                    post_baseline = group_data[baseline_samples:, :]
                    epoch_values.append(float(np.mean(post_baseline)))
                else:
                    epoch_values.append(float(np.mean(group_data)))
            
            group_means.append(float(np.mean(epoch_values)))
            group_sems.append(float(np.std(epoch_values, ddof=1) / np.sqrt(len(epoch_values))) if len(epoch_values) > 1 else 0.0)
        
        # Output one file per condition
        pl.DataFrame({
            'condition': [cond],
            'x_data': [group_names],
            'y_data': [group_means],
            'y_var': [group_sems],
            'plot_type': ['bar'],
            'x_label': [x_label],
            'y_label': [y_label],
            'y_ticks': [y_lim] if y_lim is not None else [None]
        }).write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
        
        print(f"[group]   {cond}: {len(epochs)} epochs, {len(group_names)} groups")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(conditions)],
        'groups': [group_names],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path)
    
    print(f"[group] Output: {signal_path}")
    return signal_path

def _auto_detect_groups(ch_cols: list[str]) -> dict[str, list[str]]:
    """Auto-detect channel groups from naming patterns."""
    groups = {}
    
    for ch in ch_cols:
        # Try source-detector pattern (e.g., "1-1:0", "2-3")
        match = re.match(r'^(\d+)-(\d+)', ch)
        if match:
            source = match.group(1)
            group_name = f'Source{source}'
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append(ch)
            continue
        
        # Try S1_D1 pattern
        match = re.match(r'^S(\d+)_D(\d+)', ch)
        if match:
            source = match.group(1)
            group_name = f'Source{source}'
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append(ch)
            continue
    
    return groups if groups else {'All': ch_cols}

if __name__ == '__main__':
    (lambda a: analyze_groups(a[1], a[2],
                               float(a[3]) if len(a) > 3 and a[3] and a[3] != 'None' else None,
                               a[4] if len(a) > 4 else 'Group',
                               a[5] if len(a) > 5 else 'Mean',
                               a[6] if len(a) > 6 else 'grp',
                               float(a[7]) if len(a) > 7 and a[7] and a[7] != 'None' else 2.0) if len(a) >= 3 else (
        print('Aggregate channels by groups per condition. Plot-ready output with baseline correction.'),
        print('[group] Usage: python group_analyzer.py <epoched.parquet> <groups_json> [y_lim] [x_label] [y_label] [suffix] [baseline_sec]'),
        print('[group] Channel patterns: exact match, glob (*, ?, []), or regex (re:pattern)'),
        print('[group] Example: python group_analyzer.py data.parquet \'{"Left": ["re:^[12]-"], "Right": ["re:^[34]-"]}\''),
        sys.exit(1)))(sys.argv)