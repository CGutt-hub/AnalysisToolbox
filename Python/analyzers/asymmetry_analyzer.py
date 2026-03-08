import polars as pl, numpy as np, sys, ast, os
# Logging helpers
def log_info(msg): print(f"[asymmetry] INFO: {msg}")
def log_warning(msg): print(f"[asymmetry] WARNING: {msg}")
def log_error(msg): print(f"[asymmetry] ERROR: {msg}")
def compute_asymmetry(ip: str, pairs: list[tuple[str,str]], mode: str = 'log', 
                      band: str | None = None, y_lim: float | None = None, 
                      y_label: str | None = None, suffix: str = 'asym') -> str:
    """Compute asymmetry between paired regions from raw or plot data.
    
    Input: Parquet with region/channel data or plot-formatted data
    Output: Plot-ready asymmetry data
    
    Args:
        ip: Input parquet file with raw data (channel/region, value, sem) or plot data
        pairs: List of (left, right) region/channel pairs
        mode: 'log' for ln(R)-ln(L), 'diff' for R-L
        band: Filter to specific band (for raw PSD data)
        y_lim: Optional Y-axis limit
        y_label: Optional Y-axis label
        suffix: Output file suffix
    
    Returns: Path to output parquet with plot-ready asymmetry data
    """
    print(f"[asymmetry] Asymmetry analysis: {ip}, pairs={pairs}, mode={mode}")
    
    df = pl.read_parquet(ip)

    # Resolve Nextflow signal pointer files (signal + folder_path, no real data columns)
    if ('signal' in df.columns and 'folder_path' in df.columns
            and 'region' not in df.columns and 'channel' not in df.columns
            and 'x_data' not in df.columns and 'epoch_id' not in df.columns):
        folder = str(df['folder_path'][0])
        if os.path.isdir(folder):
            parquets = sorted([
                os.path.join(folder, fn)
                for fn in os.listdir(folder)
                if fn.endswith('.parquet') and not fn.endswith('_vis.parquet')
            ])
            if parquets:
                print(f"[asymmetry] Resolved signal pointer: loaded {len(parquets)} file(s) from {os.path.basename(folder)}")
                df = pl.concat([pl.read_parquet(p) for p in parquets], how='diagonal')
            else:
                log_warning(f"Signal folder is empty: {folder}, cannot compute asymmetry")
                # Return empty output
                out_path = os.path.join(os.getcwd(), f"{os.path.splitext(os.path.basename(ip))[0]}_{suffix}.parquet")
                pl.DataFrame({'condition': [os.path.splitext(os.path.basename(ip))[0]], 'x_data': [[]], 'y_data': [[]], 'y_var': [[]], 'plot_type': ['bar'], 'x_label': ['Pair'], 'y_label': [y_label or 'Asymmetry']}).write_parquet(out_path, compression='snappy')
                print(f"[asymmetry] Output: {out_path}")
                return out_path
        else:
            log_warning(f"Signal folder not found: {folder}, cannot compute asymmetry")
            out_path = os.path.join(os.getcwd(), f"{os.path.splitext(os.path.basename(ip))[0]}_{suffix}.parquet")
            pl.DataFrame({'condition': [os.path.splitext(os.path.basename(ip))[0]], 'x_data': [[]], 'y_data': [[]], 'y_var': [[]], 'plot_type': ['bar'], 'x_label': ['Pair'], 'y_label': [y_label or 'Asymmetry']}).write_parquet(out_path, compression='snappy')
            print(f"[asymmetry] Output: {out_path}")
            return out_path

    # Check data format
    if 'channel' in df.columns or 'region' in df.columns:
        # Raw data format: channel/region, value/power, sem (optional)
        print(f"[asymmetry] Raw data detected (long format)")
        base = os.path.splitext(os.path.basename(ip))[0]
        cond = df['condition'][0] if 'condition' in df.columns else base
        region_col = 'channel' if 'channel' in df.columns else 'region'
        
        # Detect value column name
        value_col = 'value' if 'value' in df.columns else 'power' if 'power' in df.columns else None
        if not value_col:
            raise ValueError(f"No value column found in data. Expected 'value' or 'power', found columns: {df.columns}")
        
        # Detect sem column name
        sem_col = 'sem' if 'sem' in df.columns else 'power_std' if 'power_std' in df.columns else None
        
        # Filter by band if specified
        if band and 'band' in df.columns:
            df = df.filter(pl.col('band') == band)
        
        # Helper function to compute asymmetry for a dataset
        def compute_asym(data_df):
            # Aggregate if multiple rows per region/channel
            if len(data_df) > data_df[region_col].n_unique():
                region_data = data_df.group_by(region_col).agg([
                    pl.col(value_col).mean().alias('value'),
                    pl.col(sem_col).mean().alias('sem') if sem_col and sem_col in data_df.columns else pl.lit(0.0).alias('sem')
                ])
            else:
                region_data = data_df.rename({value_col: 'value'})
                if sem_col and sem_col in data_df.columns:
                    region_data = region_data.rename({sem_col: 'sem'})
                elif 'sem' not in region_data.columns:
                    region_data = region_data.with_columns(pl.lit(0.0).alias('sem'))
            
            region_dict = {row[region_col]: row['value'] for row in region_data.to_dicts()}
            region_sem = {row[region_col]: row.get('sem', 0.0) for row in region_data.to_dicts()}
            
            asym_vals, asym_sems = [], []
            for left, right in pairs:
                left_val = region_dict.get(left)
                right_val = region_dict.get(right)
                sem_L = region_sem.get(left, 0.0)
                sem_R = region_sem.get(right, 0.0)
                
                # Quality check: missing regions
                if left_val is None:
                    log_warning(f"Missing data for {left} in condition/band")
                if right_val is None:
                    log_warning(f"Missing data for {right} in condition/band")
                
                if left_val is None or right_val is None:
                    continue
                
                if mode == 'log' and left_val > 0 and right_val > 0:
                    asym = np.log(right_val) - np.log(left_val)
                    sem = np.sqrt((sem_L/left_val)**2 + (sem_R/right_val)**2)
                    # Quality check: extreme asymmetry
                    if abs(asym) > 2.0:
                        log_warning(f"Extreme asymmetry: {left}-{right} log ratio={asym:.2f} (>2.0 suggests large imbalance)")
                else:
                    asym = left_val - right_val
                    sem = np.sqrt(sem_L**2 + sem_R**2)
                    # Quality check: extreme asymmetry for difference mode
                    if left_val != 0 and abs(asym / left_val) > 1.5:
                        log_warning(f"Extreme asymmetry: {left}-{right} difference={asym:.2f} (>150% of left value)")
                
                asym_vals.append(float(asym))
                asym_sems.append(float(sem))
            
            return asym_vals, asym_sems
        
        # Check if multiple bands
        if 'band' in df.columns and df['band'].n_unique() > 1:
            bands = sorted(df['band'].unique().to_list())
            print(f"[asymmetry] Computing asymmetry for {len(bands)} bands: {bands}")
            
            series_data = [compute_asym(df.filter(pl.col('band') == b)) for b in bands]
            series_asyms, series_sems = zip(*series_data)
            
            # Multi-series output
            out_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
            pl.DataFrame({
                'condition': [cond],
                'x_data': [[f"{left}-{right}" for left, right in pairs]],
                'y_data': list(series_asyms),
                'y_var': list(series_sems),
                'labels': [bands],
                'plot_type': ['grid'],
                'x_label': ['Pair'],
                'y_label': [y_label or 'Asymmetry'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(out_path, compression='snappy')
        else:
            # Single series
            asym_vals, asym_sems = compute_asym(df)
            available_regions = list({row[region_col]: row[value_col] for row in (df.group_by(region_col).agg(pl.col(value_col).mean().alias(value_col)) if len(df) > df[region_col].n_unique() else df).to_dicts()}.keys())
            print(f"[asymmetry] Available {region_col}s: {available_regions}")
            
            # Average asymmetry across all pairs for condition-level comparison
            # This allows proper concatenation where x_data = condition names (NEG/NEU/POS)
            avg_asym = float(np.mean(asym_vals)) if asym_vals else 0.0
            # Propagate uncertainty: SE of mean = sqrt(sum(SE^2))/n
            avg_sem = float(np.sqrt(sum(s**2 for s in asym_sems)) / len(asym_sems)) if asym_sems else 0.0
            
            print(f"[asymmetry] Averaged {len(pairs)} pairs: {avg_asym:.3f} ± {avg_sem:.3f}")
            
            # Single series output - pair names as x_data so that when 3 condition files are
            # concatenated the x_data is shared metadata and y_data becomes per-condition bars.
            pair_labels = [f"{left}-{right}" for left, right in pairs]
            out_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
            pl.DataFrame({
                'condition': [cond],
                'x_data': [pair_labels],  # Pair names (shared across conditions)
                'y_data': [[avg_asym]],   # Averaged asymmetry value for this condition
                'y_var': [[avg_sem]],
                'plot_type': ['bar'],
                'x_label': ['Pair'],
                'y_label': [y_label or 'Asymmetry'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(out_path, compression='snappy')
    
    elif 'epoch_id' in df.columns and any(p[0] in df.columns and p[1] in df.columns for p in pairs):
        # Wide-format epoch data: condition, epoch_id, time, ch1, ch2, ...
        # This is raw data - need to aggregate first before computing asymmetry
        log_error(f"Input appears to be raw epoch data with columns: {list(df.columns)}")
        log_error(f"Asymmetry computation requires aggregated data (mean/SEM per condition), not raw epochs")
        log_error(f"Please aggregate the data first using an appropriate analyzer (e.g., psd_analyzer, amplitude_analyzer)")
        log_error(f"Then compute asymmetry on the aggregated output")
        raise ValueError(f"Cannot compute asymmetry on raw epoch data. Available columns: {list(df.columns)}. Expected either: 1) Long format with 'region'/'channel' + 'value'/'power' columns, or 2) Plot format with 'x_data'/'y_data' columns.")
    
    else:
        # Plot format data - handle plot-ready input with proper error propagation
        row = df.to_dicts()[0]
        base = os.path.splitext(os.path.basename(ip))[0]
        cond = row.get('condition', base)
        x_data = row.get('x_data', [])
        y_data = row.get('y_data', [])
        y_var = row.get('y_var', [])
        labels = row.get('labels', [])
        
        print(f"[asymmetry] Plot data: {len(x_data)} regions")
        out_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
        
        # Handle empty plot data
        if not x_data or len(x_data) == 0:
            log_warning(f"No regions in plot data, outputting minimal valid structure")
            plot_df = pl.DataFrame({
                'condition': [cond],
                'x_data': [[]],
                'y_data': [[]],
                'y_var': [[]],
                'plot_type': ['bar'],
                'x_label': ['Pair'],
                'y_label': [y_label or 'Asymmetry']
            })
            plot_df.write_parquet(out_path, compression='snappy')
            plot_df.write_parquet(out_path.replace('.parquet', '_vis.parquet'), compression='snappy')
            print(f"[asymmetry] Output: {out_path}")
            return out_path
        
        if isinstance(y_data, list) and len(y_data) > 0 and isinstance(y_data[0], list):
            # Multiple series
            series_asyms = []
            series_sems = []
            for i, series_values in enumerate(y_data):
                series_var = y_var[i] if i < len(y_var) and y_var[i] else [0.0] * len(series_values)
                region_dict = {x_data[j]: series_values[j] for j in range(min(len(x_data), len(series_values)))}
                region_var = {x_data[j]: series_var[j] for j in range(min(len(x_data), len(series_var)))}
                asym_vals = []
                asym_sems = []
                for left, right in pairs:
                    left_val = region_dict.get(left)
                    right_val = region_dict.get(right)
                    left_sem = region_var.get(left, 0.0)
                    right_sem = region_var.get(right, 0.0)
                    if left_val is not None and right_val is not None:
                        if mode == 'log' and left_val > 0 and right_val > 0:
                            asym = np.log(right_val) - np.log(left_val)
                            sem = np.sqrt((left_sem/left_val)**2 + (right_sem/right_val)**2)
                        else:
                            asym = right_val - left_val
                            sem = np.sqrt(left_sem**2 + right_sem**2)
                        asym_vals.append(asym)
                        asym_sems.append(sem)
                    else:
                        asym_vals.append(0.0)
                        asym_sems.append(0.0)
                series_asyms.append(asym_vals)
                series_sems.append(asym_sems)
            
            pl.DataFrame({
                'condition': [cond],
                'x_data': [[f"{left}-{right}" for left, right in pairs]],
                'y_data': [series_asyms],
                'y_var': [series_sems],
                'labels': [labels or [f'Series{i+1}' for i in range(len(series_asyms))]],
                'plot_type': ['grid'],
                'x_label': ['Pair'],
                'y_label': [y_label or 'Asymmetry']
            }).write_parquet(out_path, compression='snappy')
        else:
            # Single series - compute asymmetry with proper error propagation
            region_dict = {x_data[j]: y_data[j] for j in range(min(len(x_data), len(y_data)))}
            region_var = {x_data[j]: (y_var[j] if j < len(y_var) else 0.0) for j in range(len(x_data))}
            asym_vals = []
            asym_sems = []
            for left, right in pairs:
                left_val = region_dict.get(left)
                right_val = region_dict.get(right)
                left_sem = region_var.get(left, 0.0)
                right_sem = region_var.get(right, 0.0)
                if left_val is not None and right_val is not None:
                    if mode == 'log' and left_val > 0 and right_val > 0:
                        asym = np.log(right_val) - np.log(left_val)
                        sem = np.sqrt((left_sem/left_val)**2 + (right_sem/right_val)**2)
                    else:
                        asym = right_val - left_val
                        sem = np.sqrt(left_sem**2 + right_sem**2)
                    asym_vals.append(asym)
                    asym_sems.append(sem)
            
            plot_df = pl.DataFrame({
                'condition': [cond],
                'x_data': [[f"{left}-{right}" for left, right in pairs]],
                'y_data': [asym_vals],
                'y_var': [asym_sems],
                'plot_type': ['bar'],
                'x_label': ['Pair'],
                'y_label': [y_label or 'Asymmetry'],
                'title': [f"{base} - Asymmetry"]
            })
            plot_df.write_parquet(out_path, compression='snappy')
            plot_df.write_parquet(out_path.replace('.parquet', '_vis.parquet'), compression='snappy')
    print(f"[asymmetry] Output: {out_path}")
    print(f"[asymmetry] Created procedure visualization: {out_path.replace('.parquet', '_vis.parquet')}")
    return out_path

if __name__ == '__main__':
    (lambda a: compute_asymmetry(a[1], ast.literal_eval(a[2]),
                                  a[3] if len(a) > 3 and a[3] not in ('None', '') else 'log',
                                  a[4] if len(a) > 4 and a[4] not in ('None', '') else None,
                                  float(a[5]) if len(a) > 5 and a[5] not in ('None', '') else None,
                                  a[6] if len(a) > 6 and a[6] not in ('None', '') else None,
                                  a[7] if len(a) > 7 else 'asym') if len(a) >= 3 else (
        print('[asymmetry] Compute asymmetry between paired regions.'),
        print('Usage: asymmetry_analyzer.py <input.parquet> <pairs> [mode] [band] [y_lim] [y_label] [suffix]'),
        print('  pairs: Python list, e.g. "[(\'F3\',\'F4\'),(\'F7\',\'F8\')]"'),
        print('  mode: "log" for ln(R)-ln(L) (default), "diff" for R-L'),
        print('  band: Filter to specific band (for PSD data)'),
        sys.exit(1)))(sys.argv)
