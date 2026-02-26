"""Merging Processor - Merge data files by columns or combine plot-ready data.
Column merge: SQL-style join on shared keys
Plot merge: Combine x_data/y_data arrays from multiple sources per condition"""
import polars as pl, sys, os, functools, re

def merge_columns(ip: list[str], keys: list[str], output_suffix: str = 'merged') -> str:
    """Merge files by joining on shared key columns (SQL-style join)."""
    for f in ip:
        if not os.path.exists(f): print(f"[merging] File not found: {f}"); sys.exit(1)
    if not keys: print("[merging] Error: No join keys specified"); sys.exit(1)
    if len(ip) < 2: print("[merging] Error: Need at least 2 files to merge"); sys.exit(1)
    print(f"[merging] Column merge: {len(ip)} files on keys: {keys}")
    dfs = [pl.read_parquet(f) for f in ip]
    for i, df in enumerate(dfs):
        missing = [k for k in keys if k not in df.columns]
        if missing: print(f"[merging] Error: Keys {missing} not in {ip[i]}"); sys.exit(1)
    merged = functools.reduce(lambda acc, df: acc.join(df, on=keys, how='inner', suffix='_mod'), dfs[1:], dfs[0])
    out_file = f"{os.path.splitext(os.path.basename(ip[0]))[0]}_{output_suffix}.parquet"
    merged.write_parquet(out_file)
    print(f"[merging] Output: {out_file} ({merged.shape})")
    return out_file

def merge_plot_data(ip: list[str], prefixes: list[str] | None = None, output_suffix: str = 'merged') -> str:
    """Merge plot-ready parquet files by combining x_data/y_data arrays per condition.
    
    Supports two input formats:
    1. Multi-row: Each file has rows with condition, x_data, y_data, y_var (one row per condition)
    2. Concatenated: Single row with labels list and nested y_data/y_var lists
    
    Output combines x_data/y_data from all sources for matching conditions."""
    for f in ip:
        if not os.path.exists(f): print(f"[merging] File not found: {f}"); sys.exit(1)
    if len(ip) < 2: print("[merging] Error: Need at least 2 files to merge"); sys.exit(1)
    
    prefixes = prefixes or [f"src{i+1}" for i in range(len(ip))]
    print(f"[merging] Plot data merge: {len(ip)} files with prefixes: {prefixes}")
    
    # Load and normalize to per-condition format
    def load_and_expand(filepath: str) -> list[dict]:
        """Load file and expand to list of per-condition dicts."""
        df = pl.read_parquet(filepath)
        row = df.to_dicts()[0]
        
        # Check format: concatenated (has 'labels') vs multi-row (has 'condition')
        if 'labels' in row:
            # Concatenated format: single row with labels list and nested data
            labels = row['labels']
            x_data = row.get('x_data', [])
            y_data_nested = row.get('y_data', [])
            y_var_nested = row.get('y_var', [])
            
            result = []
            for i, label in enumerate(labels):
                result.append({
                    'condition': label,
                    'x_data': x_data,  # x_data is shared across conditions
                    'y_data': y_data_nested[i] if i < len(y_data_nested) else [],
                    'y_var': y_var_nested[i] if i < len(y_var_nested) else [],
                    'plot_type': row.get('plot_type', 'bar'),
                    'y_label': row.get('y_label', 'Value'),
                    'y_ticks': row.get('y_ticks')
                })
            return result
        elif 'condition' in df.columns:
            # Multi-row format: one row per condition
            return df.to_dicts()
        else:
            print(f"[merging] Error: File {filepath} has neither 'labels' nor 'condition' column")
            sys.exit(1)
    
    # Load all files
    all_data = [load_and_expand(f) for f in ip]
    
    # Find common conditions
    all_conds = [set(d['condition'] for d in data) for data in all_data]
    common_conds = sorted(functools.reduce(lambda a, b: a & b, all_conds))
    if not common_conds:
        print(f"[merging] Warning: No common conditions, using union")
        common_conds = sorted(functools.reduce(lambda a, b: a | b, all_conds))
    
    print(f"[merging] Conditions: {common_conds}")
    
    # Extract participant ID from first file
    match = re.match(r'^([A-Za-z]+_\d+)', os.path.basename(ip[0]))
    pid = match.group(1) + '_' if match else ''
    out_folder = os.path.join(os.getcwd(), f"{pid}{output_suffix}_data")
    os.makedirs(out_folder, exist_ok=True)
    
    # Merge per condition
    for idx, cond in enumerate(common_conds):
        combined_x, combined_y, combined_var = [], [], []
        first_row = None
        
        for data, prefix in zip(all_data, prefixes):
            rows = [d for d in data if d['condition'] == cond]
            if rows:
                row = rows[0]
                if first_row is None:
                    first_row = row
                x_data = row.get('x_data', [])
                y_data = row.get('y_data', [])
                y_var = row.get('y_var', [0.0] * len(y_data))
                combined_x.extend([f"{prefix} {x}" for x in x_data])
                combined_y.extend(y_data)
                combined_var.extend(y_var if y_var else [0.0] * len(y_data))
        
        print(f"[merging]   {cond}: {len(combined_y)} values from {len(ip)} sources")
        plot_type = first_row.get('plot_type', 'bar') if first_row else 'bar'
        y_label = first_row.get('y_label', 'Value') if first_row else 'Value'
        y_ticks = first_row.get('y_ticks', None) if first_row else None
        
        pl.DataFrame({
            'condition': [cond], 'x_data': [combined_x], 'y_data': [combined_y],
            'y_var': [combined_var], 'plot_type': [plot_type], 'x_label': ['Measure'],
            'y_label': [y_label], 'y_ticks': [y_ticks]
        }).write_parquet(os.path.join(out_folder, f"{pid}{output_suffix}{idx+1}.parquet"))
    
    # Create concatenated output file (compatible with plotter/relative_analyzer)
    # Output goes to {pid}{output_suffix}.parquet (no _concat suffix) to match pipeline expectations
    concat_path = os.path.join(os.getcwd(), f"{pid}{output_suffix}.parquet")
    all_x, all_y, all_var = [], [], []
    for cond in common_conds:
        combined_x, combined_y, combined_var = [], [], []
        for data, prefix in zip(all_data, prefixes):
            rows = [d for d in data if d['condition'] == cond]
            if rows:
                row = rows[0]
                x_data = row.get('x_data', [])
                y_data = row.get('y_data', [])
                y_var = row.get('y_var', [0.0] * len(y_data))
                combined_x.extend([f"{prefix} {x}" for x in x_data])
                combined_y.extend(y_data)
                combined_var.extend(y_var if y_var else [0.0] * len(y_data))
        all_x.append(combined_x)
        all_y.append(combined_y)
        all_var.append(combined_var)
    
    # Get plot metadata from first source
    first_row = all_data[0][0] if all_data and all_data[0] else {}
    pl.DataFrame({
        'labels': [common_conds],
        'x_data': [all_x[0] if all_x else []],  # x_data is shared (same categories)
        'y_data': [all_y],
        'y_var': [all_var],
        'plot_type': [first_row.get('plot_type', 'bar')],
        'x_label': ['Measure'],
        'y_label': [first_row.get('y_label', 'Value')],
        'y_ticks': [first_row.get('y_ticks')]
    }).write_parquet(concat_path)
    print(f"[merging] Concatenated output: {concat_path}")
    print(concat_path)
    return concat_path

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print('[merging] Generic data merger with two modes:')
        print('  Column merge: merging_processor.py <f1.parquet> <f2.parquet> ... <key1> [key2...]')
        print('  Plot merge:   merging_processor.py <f1.parquet> <f2.parquet> ... --plot <prefix1,prefix2,...> <suffix>')
        sys.exit(1)
    
    # Separate files from other args (order-independent)
    files = [a for a in args if a.endswith('.parquet')]
    other = [a for a in args if not a.endswith('.parquet')]
    
    print(f"[merging] Args: {args}")
    print(f"[merging] Args repr: {[repr(a) for a in args]}")
    print(f"[merging] Files: {files}")
    print(f"[merging] Other: {other}")

    # Check for --plot flag with flexible matching (handle quote leakage)
    plot_arg = next((a for a in other if '--plot' in a), None)
    
    if plot_arg is not None:
        other_clean = [a for a in other if a != plot_arg]
        prefixes = other_clean[0].split(',') if len(other_clean) > 0 else None
        suffix = other_clean[1] if len(other_clean) > 1 else 'merged'
        print(f"[merging] Plot mode: prefixes={prefixes}, suffix={suffix}")
        result = merge_plot_data(files, prefixes, suffix)
    else:
        # Column merge: remaining args are keys
        keys = other
        result = merge_columns(files, keys)
    print(result)
