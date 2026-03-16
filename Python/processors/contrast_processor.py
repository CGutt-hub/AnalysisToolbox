"""Contrast Processor - Compute linear contrasts from OLS results.
Input: parquet with [channel, condition, beta, se, ...]
Output: parquet with [channel, contrast, value, se, tvalue]"""
import polars as pl, numpy as np, sys, os, ast

def contrast_process(ip: str, contrasts_str: str, output_suffix: str = 'contrast') -> str:
    if not os.path.exists(ip): print(f"[contrast] File not found: {ip}"); sys.exit(1)
    print(f"[contrast] Contrast computation: {ip}")
    df = pl.read_parquet(ip)
    contrasts = ast.literal_eval(contrasts_str) if contrasts_str else {}
    if not contrasts: print("[contrast] No contrasts specified, passing through"); return ip
    channels = df['channel'].unique().to_list()
    conditions = df['condition'].unique().to_list()
    print(f"[contrast] {len(channels)} channels, {len(contrasts)} contrasts: {list(contrasts.keys())}")
    
    results = []
    for name, weights in contrasts.items():
        for ch in channels:
            ch_data = df.filter(pl.col('channel') == ch)
            
            # Compute contrast value: sum of weighted betas
            contrast_val = 0.0
            contrast_var = 0.0
            for cond in conditions:
                weight = weights.get(cond, 0)
                if weight != 0:
                    cond_row = ch_data.filter(pl.col('condition') == cond)
                    if len(cond_row) > 0:
                        beta = float(cond_row['beta'][0])
                        se = float(cond_row['se'][0])
                        contrast_val += weight * beta
                        contrast_var += (weight ** 2) * (se ** 2)
            
            contrast_se = float(np.sqrt(contrast_var))
            contrast_t = float(contrast_val / contrast_se) if contrast_se > 0 else 0.0
            
            results.append({
                'channel': ch,
                'contrast': name,
                'value': contrast_val,
                'se': contrast_se,
                'tvalue': contrast_t
            })
    
    base, out_file = os.path.splitext(os.path.basename(ip))[0], f"{os.path.splitext(os.path.basename(ip))[0]}_{output_suffix}.parquet"
    if not results:
        print(f"[contrast] No results (empty input) — writing empty output: {out_file}")
        pl.DataFrame(schema={'channel': pl.Utf8, 'contrast': pl.Utf8, 'value': pl.Float64,
                             'se': pl.Float64, 'tvalue': pl.Float64}).write_parquet(out_file, compression='snappy')
        pl.DataFrame({'x_data': [[]], 'y_data': [[]], 'y_var': [[]], 'plot_type': ['grid'],
                      'labels': [[]], 'x_label': ['Channel'], 'y_label': ['Contrast Value']
                     }).write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')
        return out_file
    result_df = pl.DataFrame(results)
    result_df.write_parquet(out_file, compression='snappy')
    
    # Generate inline visualization - bar plot of contrast values per channel
    contrast_names = result_df['contrast'].unique().to_list()
    vis_df = pl.DataFrame({
        'x_data': [result_df['channel'].to_list()],
        'y_data': [[result_df.filter(pl.col('contrast') == c)['value'].to_list() for c in contrast_names]],
        'y_var': [[result_df.filter(pl.col('contrast') == c)['se'].to_list() for c in contrast_names]],
        'plot_type': ['grid'],
        'labels': [contrast_names],
        'x_label': ['Channel'],
        'y_label': ['Contrast Value']
    })
    vis_df.write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')
    
    print(f"[contrast] Output: {out_file} ({len(results)} rows)")
    return out_file

if __name__ == '__main__': (lambda a: contrast_process(a[1], a[2], a[3] if len(a) > 3 else 'contrast') if len(a) >= 3 else (print("[contrast] Compute linear contrasts (weighted sums) from OLS betas.\nUsage: contrast_processor.py <ols.parquet> <contrasts_dict> [suffix=contrast]\nExample: contrast_processor.py data_ols.parquet \"{'A-B': {'A': 1, 'B': -1}}\""), sys.exit(1)))(sys.argv)
