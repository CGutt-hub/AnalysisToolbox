import polars as pl, sys, os

# Logging helpers
def log_info(msg): print(f"[relative] INFO: {msg}")
def log_warning(msg): print(f"[relative] WARNING: {msg}")
def log_error(msg): print(f"[relative] ERROR: {msg}")

def relative_normalize(ip: str, baseline_cond: str = 'NEU', y_lim: float | None = None) -> str:
    """Convert concatenated analyzer output to relative change from baseline condition.
    
    Takes output from concatenating_processor which has structure:
    - labels: list of condition names ['NEG', 'NEU', 'POS']
    - y_data: list of lists, one per condition (values)
    - y_var: list of lists, one per condition (errors/variance)
    
    Converts to relative change (baseline-subtracted) from baseline condition.
    The baseline condition (NEU) is removed from output since it would be zero.
    
    Args:
        ip: Input parquet file from concatenating_processor
        baseline_cond: Condition to use as baseline (default: 'NEU')
        y_lim: Optional Y-axis maximum limit for consistent scaling of relative plots
    
    Output: Parquet with same structure, y_data as relative change from baseline
    """
    print(f"[relative] Loading: {ip}")
    df = pl.read_parquet(ip)
    row = df.to_dicts()[0]
    
    # Get labels and find baseline index
    labels = row.get('labels', [])
    if not labels:
        log_error(f"No 'labels' field found. Columns: {df.columns}")
        sys.exit(1)
    
    baseline_idx = next((i for i, l in enumerate(labels) if l == baseline_cond), None)
    if baseline_idx is None:
        log_warning(f"Baseline '{baseline_cond}' not found in {labels}, using first condition")
        baseline_idx = 0
        baseline_cond = labels[0]
    
    # Get baseline values
    y_data = row.get('y_data', [])
    y_var = row.get('y_var', [])
    x_data = row.get('x_data', [])
    
    if not y_data or baseline_idx >= len(y_data):
        log_error("Invalid y_data structure")
        sys.exit(1)
    
    baseline_values = y_data[baseline_idx]
    print(f"[relative] Baseline condition: {baseline_cond} (index {baseline_idx})")
    print(f"[relative] Baseline values: {[f'{v:.2f}' for v in baseline_values]}")
    
    # Convert each condition's values to relative change from baseline (baseline subtracted)
    def to_relative(values, errors, baseline):
        rel_values = []
        rel_errors = []
        for i, (v, e) in enumerate(zip(values, errors)):
            base = baseline[i] if i < len(baseline) else baseline[0]
            rel_values.append(v - base)  # Simple subtraction, not percentage
            rel_errors.append(e)  # Keep original error
        return rel_values, rel_errors
    
    new_y_data = []
    new_y_var = []
    new_labels = []
    for i, (label, values, errors) in enumerate(zip(labels, y_data, y_var)):
        # Skip the baseline condition (it would be all zeros)
        if i == baseline_idx:
            continue
        rel_vals, rel_errs = to_relative(values, errors, baseline_values)
        new_y_data.append(rel_vals)
        new_y_var.append(rel_errs)
        new_labels.append(label)
    
    # Update row with relative values (excluding baseline)
    # Note: x_data stays unchanged - it's shared across conditions (e.g., ROI names)
    row['labels'] = new_labels
    row['y_data'] = new_y_data
    row['y_var'] = new_y_var
    # Update y_label to indicate relative change
    if 'y_label' in row:
        original_label = row['y_label']
        row['y_label'] = f'Δ {original_label} (rel. to {baseline_cond})'
    # Override y_ticks with relative y_lim if provided
    if y_lim is not None:
        row['y_ticks'] = y_lim

    out_df = pl.DataFrame([row])
    
    # Output
    base = os.path.splitext(os.path.basename(ip))[0]
    out_dir = os.path.dirname(ip) or '.'
    out_path = os.path.join(out_dir, f"{base}_rel.parquet")
    out_df.write_parquet(out_path)
    
    # Create procedure visualization
    vis_path = out_path.replace('.parquet', '_vis.parquet')
    out_df.write_parquet(vis_path)
    print(f"[relative] Created procedure visualization: {vis_path}")
    
    print(f"[relative] Normalized {len(new_labels)} conditions relative to {baseline_cond} (baseline excluded):")
    for i, label in enumerate(new_labels):
        mean_rel = sum(new_y_data[i]) / len(new_y_data[i]) if new_y_data[i] else 0
        print(f"[relative]   {label}: mean Δ{mean_rel:+.3f}")
    print(f"[relative] Output: {out_path}")
    
    return out_path

if __name__ == '__main__':
    (lambda a: relative_normalize(
        a[1], 
        a[2] if len(a) > 2 and a[2] else 'NEU',
        float(a[3]) if len(a) > 3 and a[3] and a[3].lower() != 'none' else None
    ) if len(a) >= 2 else (
        print('Convert values to relative change from baseline condition. Plot-ready output.'),
        print('[relative] Usage: python relative_analyzer.py <concatenated.parquet> [baseline_cond] [y_lim]'), 
        sys.exit(1)
    ))(sys.argv)
