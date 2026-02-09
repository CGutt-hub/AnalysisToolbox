"""Bootstrap Analyzer - Generic bootstrap resampling for confidence intervals on grouped data."""
import polars as pl, numpy as np, sys, os
from scipy import stats

def bootstrap_analyze(ip: str, group_col: str = 'condition', sample_col: str | None = None,
                     value_col: str | None = None, n_boot: int = 10000, 
                     ci_method: str = 'percentile', alpha: float = 0.05,
                     y_lim: float | None = None, y_label: str = 'Mean', 
                     suffix: str = 'bootstrap') -> str:
    """Generic bootstrap resampling: compute CIs by resampling within groups.
    
    Flexible grouping: Works on any data structure with grouping levels.
    - Within-participant per condition: group_col='condition', sample_col='epoch_id'
    - Group-level per condition: group_col='condition', sample_col='participant_id'
    - Any custom grouping: specify appropriate group and sample columns
    
    Args:
        ip: Input parquet file
        group_col: Column defining groups (e.g., 'condition') - creates separate output per group
        sample_col: Column defining resample units (e.g., 'epoch_id', 'participant_id') - auto-detected if None
        value_col: Column with values to aggregate (auto-detected if None)
        n_boot: Bootstrap iterations (default 10000)
        ci_method: 'percentile' (default, robust), 'bca' (bias-corrected), 'normal' (parametric)
        alpha: Significance level (default 0.05 for 95% CI)
        y_lim: Y-axis limit for plots
        y_label: Y-axis label
        suffix: Output file suffix
    
    Returns:
        Path to signal file
    """
    print(f"[bootstrap] Bootstrap: {ip}, group={group_col}, n={n_boot}, method={ci_method}")
    df = pl.read_parquet(ip)
    
    # Check if this is a signal file pointing to a folder of per-condition files
    if 'folder_path' in df.columns and 'conditions' in df.columns:
        folder_path = df['folder_path'][0]
        print(f"[bootstrap] Detected signal file, reading from folder: {folder_path}")
        
        # Read all parquet files from the folder and concatenate
        import glob
        data_files = sorted(glob.glob(os.path.join(folder_path, "*.parquet")))
        if not data_files:
            print(f"[bootstrap] ERROR: No data files found in {folder_path}"); sys.exit(1)
        
        dfs = [pl.read_parquet(f) for f in data_files]
        df = pl.concat(dfs)
        print(f"[bootstrap] Loaded {len(data_files)} condition files from folder")
    
    # Validate group column
    if group_col not in df.columns:
        print(f"[bootstrap] ERROR: Group column '{group_col}' not found"); sys.exit(1)
    
    # Auto-detect sample column (resample unit)
    if sample_col is None:
        candidates = ['epoch_id', 'trial_id', 'sample_id', 'participant_id', 'subject_id']
        sample_col = next((c for c in candidates if c in df.columns), None)
        if not sample_col:
            print(f"[bootstrap] ERROR: No sample column found (tried: {candidates})"); sys.exit(1)
    if sample_col not in df.columns:
        print(f"[bootstrap] ERROR: Sample column '{sample_col}' not found"); sys.exit(1)
    
    # Auto-detect value column
    if value_col is None:
        meta = ['time', 'sfreq', group_col, sample_col, 'source', 'folder_path', 'signal', 'conditions']
        candidates = [c for c in df.columns if c not in meta]
        if not candidates:
            print(f"[bootstrap] ERROR: No value column found"); sys.exit(1)
        value_col = candidates[0]
    if value_col not in df.columns:
        print(f"[bootstrap] ERROR: Value column '{value_col}' not found"); sys.exit(1)
    
    print(f"[bootstrap] Columns: group={group_col}, sample={sample_col}, value={value_col}")
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
    os.makedirs(out_folder, exist_ok=True)
    
    groups = sorted(df[group_col].unique().to_list())
    print(f"[bootstrap] Processing {len(groups)} groups")
    
    for idx, grp in enumerate(groups):
        grp_df = df.filter(pl.col(group_col) == grp)
        
        # Aggregate value per sample using group_by (more efficient than filtering)
        sample_agg = grp_df.group_by(sample_col).agg(pl.col(value_col).mean().alias('mean_val'))
        sample_vals = sample_agg['mean_val'].to_numpy().astype(float)
        n_samples = len(sample_vals)
        
        if n_samples < 2:
            print(f"[bootstrap] Warning: {grp} has <2 samples, skipping")
            continue
        
        # Bootstrap: resample samples with replacement, compute mean
        rng = np.random.default_rng(seed=42)
        boot_means = np.array([np.mean(rng.choice(sample_vals, size=n_samples, replace=True)) for _ in range(n_boot)])
        observed_mean = float(np.mean(sample_vals))
        
        # CI computation (vectorized where possible)
        if ci_method == 'percentile':
            ci_lower, ci_upper = float(np.percentile(boot_means, 100*alpha/2)), float(np.percentile(boot_means, 100*(1-alpha/2)))
        elif ci_method == 'bca':
            z0 = stats.norm.ppf(np.sum(boot_means < observed_mean) / n_boot)
            jack_means = np.array([np.mean(np.delete(sample_vals, i)) for i in range(n_samples)])
            jack_mean = np.mean(jack_means)
            num, denom = np.sum((jack_mean - jack_means)**3), 6 * (np.sum((jack_mean - jack_means)**2)**1.5)
            a = num / denom if denom > 0 else 0
            z_l, z_u = stats.norm.ppf(alpha/2), stats.norm.ppf(1-alpha/2)
            p_l = stats.norm.cdf(z0 + (z0+z_l)/(1-a*(z0+z_l))) if abs(a*(z0+z_l)) < 1 else alpha/2
            p_u = stats.norm.cdf(z0 + (z0+z_u)/(1-a*(z0+z_u))) if abs(a*(z0+z_u)) < 1 else 1-alpha/2
            ci_lower, ci_upper = float(np.percentile(boot_means, 100*p_l)), float(np.percentile(boot_means, 100*p_u))
        elif ci_method == 'normal':
            z_crit, boot_se = stats.norm.ppf(1 - alpha/2), float(np.std(boot_means, ddof=1))
            ci_lower, ci_upper = observed_mean - z_crit * boot_se, observed_mean + z_crit * boot_se
        else:
            print(f"[bootstrap] ERROR: Unknown method '{ci_method}'"); sys.exit(1)
        
        error = max(abs(ci_upper - observed_mean), abs(observed_mean - ci_lower))
        
        pl.DataFrame({
            'condition': [str(grp)],
            'x_data': [[str(grp)]],
            'y_data': [[observed_mean]],
            'y_var': [[error]],
            'ci_lower': [[ci_lower]],
            'ci_upper': [[ci_upper]],
            'plot_type': ['bar'],
            'x_label': ['Condition'],
            'y_label': [y_label],
            'y_ticks': [y_lim] if y_lim is not None else [None]
        }).write_parquet(os.path.join(out_folder, f"{base}_{suffix}{idx+1}.parquet"))
        print(f"[bootstrap]   {grp}: {observed_mean:.3f} CI=[{ci_lower:.3f}, {ci_upper:.3f}] (n={n_samples})")
    
    signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
    pl.DataFrame({
        'signal': [1],
        'source': [os.path.basename(ip)],
        'conditions': [len(groups)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path)
    print(f"[bootstrap] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    (lambda a: bootstrap_analyze(a[1],
                                 a[2] if len(a) > 2 and a[2] != 'None' else 'condition',
                                 a[3] if len(a) > 3 and a[3] != 'None' else None,
                                 a[4] if len(a) > 4 and a[4] != 'None' else None,
                                 int(a[5]) if len(a) > 5 and a[5] != 'None' else 10000,
                                 a[6] if len(a) > 6 and a[6] != 'None' else 'percentile',
                                 float(a[7]) if len(a) > 7 and a[7] != 'None' else 0.05,
                                 float(a[8]) if len(a) > 8 and a[8] != 'None' else None,
                                 a[9] if len(a) > 9 else 'Mean',
                                 a[10] if len(a) > 10 else 'bootstrap') if len(a) >= 2 else (
        print('Bootstrap resampling for robust confidence intervals on grouped data.'),
        print('[bootstrap] Usage: python bootstrap_analyzer.py <input.parquet> [group_col] [sample_col] [value_col] [n_boot] [ci_method] [alpha] [y_lim] [y_label] [suffix]'),
        print('[bootstrap] ci_method: percentile (default), bca, normal'),
        print('[bootstrap] Example: python bootstrap_analyzer.py eda_epochs.parquet condition epoch_id EDA 10000 percentile 0.05 None "EDA (μS)" bootstrap'),
        sys.exit(1)))(sys.argv)
