"""OLS Processor - Fit OLS regression per channel on epoched data.
Input: parquet with [condition, epoch_id, time, channel_cols...]
Output: parquet with [channel, condition, beta, tvalue, pvalue, se]"""
import polars as pl, numpy as np, sys, os
import statsmodels.api as sm

def ols_process(ip: str, output_suffix: str = 'ols') -> str:
    if not os.path.exists(ip): print(f"[ols] File not found: {ip}"); sys.exit(1)
    print(f"[ols] OLS regression: {ip}")
    df = pl.read_parquet(ip)
    ch_cols = [c for c in df.columns if c not in ['condition', 'epoch_id', 'time', 'sfreq']]
    if not ch_cols: print("[ols] No channel columns found"); sys.exit(1)
    conditions = sorted(df['condition'].unique().to_list())
    if not conditions: print("[ols] No conditions found"); sys.exit(1)
    print(f"[ols] {len(ch_cols)} channels, {len(conditions)} conditions")
    
    # Compute mean per epoch per channel
    epoch_means = []
    for eid in df['epoch_id'].unique().to_list():
        epoch_df = df.filter(pl.col('epoch_id') == eid)
        cond = str(epoch_df['condition'][0])
        for ch in ch_cols:
            vals = epoch_df[ch].to_numpy()
            mean_val = float(np.mean(vals)) if len(vals) > 0 else 0.0
            epoch_means.append({
                'epoch_id': eid,
                'condition': cond,
                'channel': ch,
                'value': mean_val
            })
    
    means_df = pl.DataFrame(epoch_means)
    
    # Build one-hot design matrix
    n_epochs = len(means_df.filter(pl.col('channel') == ch_cols[0]))
    cond_list = means_df.filter(pl.col('channel') == ch_cols[0])['condition'].to_list()
    X = np.zeros((n_epochs, len(conditions)))
    for i, c in enumerate(cond_list):
        X[i, conditions.index(c)] = 1.0
    X = sm.add_constant(X)
    
    # Run OLS per channel
    results = []
    for ch in ch_cols:
        y = means_df.filter(pl.col('channel') == ch)['value'].to_numpy()
        model = sm.OLS(y, X).fit()
        
        # Condition betas (skip intercept at index 0)
        for i, cond in enumerate(conditions):
            results.append({
                'channel': ch,
                'condition': cond,
                'beta': float(model.params[i + 1]),
                'tvalue': float(model.tvalues[i + 1]),
                'pvalue': float(model.pvalues[i + 1]),
                'se': float(model.bse[i + 1])
            })
    
    result_df = pl.DataFrame(results)
    base, out_file = os.path.splitext(os.path.basename(ip))[0], None
    out_file = f"{base}_{output_suffix}.parquet"
    result_df.write_parquet(out_file, compression='snappy')
    
    # Generate inline visualization - bar plot of beta coefficients per condition
    vis_df = pl.DataFrame({
        'x_data': [result_df['channel'].to_list()],
        'y_data': [[result_df.filter(pl.col('condition') == c)['beta'].to_list() for c in conditions]],
        'y_var': [[result_df.filter(pl.col('condition') == c)['se'].to_list() for c in conditions]],
        'plot_type': ['grid'],
        'labels': [conditions],
        'x_label': ['Channel'],
        'y_label': ['Beta Coefficient']
    })
    vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')
    
    print(f"[ols] Output: {out_file} ({len(results)} rows)")
    return out_file

if __name__ == '__main__': (lambda a: ols_process(a[1], a[2] if len(a) > 2 else 'ols') if len(a) >= 2 else (print('[ols] Fit OLS regression per channel on epoched data. Outputs condition betas.\nUsage: ols_processor.py <epochs.parquet> [suffix=ols]'), sys.exit(1)))(sys.argv)
