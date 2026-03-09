"""OLS Processor - Fit OLS regression per channel on epoched data.
Input: parquet with [condition, epoch_id, channel_cols...] or signal pointer to per-condition folder
Output: parquet with [channel, condition, beta, tvalue, pvalue, se]
Note: non-numeric non-meta columns (e.g. 'region') become grouping dims → compound channel names."""
import polars as pl, numpy as np, sys, os, glob, itertools
import statsmodels.api as sm

_META = {'condition', 'epoch_id', 'time', 'sfreq'}

def _resolve(df: pl.DataFrame) -> pl.DataFrame | None:
    if 'folder_path' not in df.columns: return df
    files = sorted(glob.glob(os.path.join(df['folder_path'][0], '*.parquet')))
    if not files: print(f"[ols] No data in {df['folder_path'][0]} — upstream empty, skipping"); return None
    return pl.concat([pl.read_parquet(f) for f in files])

def _empty(base: str, suffix: str) -> str:
    out = f"{base}_{suffix}.parquet"
    pl.DataFrame(schema={'channel': pl.Utf8, 'condition': pl.Utf8, 'beta': pl.Float64,
                         'tvalue': pl.Float64, 'pvalue': pl.Float64, 'se': pl.Float64}
                 ).write_parquet(out, compression='snappy')
    pl.DataFrame({'x_data': [[]], 'y_data': [[]], 'y_var': [[]], 'plot_type': ['grid'],
                  'labels': [[]], 'x_label': ['Channel'], 'y_label': ['Beta Coefficient']}
                 ).write_parquet(out.replace('.parquet', '_vis.parquet'), compression='snappy')
    print(f"[ols] Empty output (no upstream data): {out}"); return out

def ols_process(ip: str, output_suffix: str = 'ols') -> str:
    if not os.path.exists(ip): print(f"[ols] File not found: {ip}"); sys.exit(1)
    print(f"[ols] OLS regression: {ip}")
    df = _resolve(pl.read_parquet(ip))
    base = os.path.splitext(os.path.basename(ip))[0]
    if df is None: return _empty(base, output_suffix)

    # Numeric cols = channels; non-numeric non-meta cols = grouping dims (e.g. 'region')
    num_cols = [c for c in df.columns if c not in _META and df[c].dtype.is_numeric()]
    grp_dims = [c for c in df.columns if c not in _META and not df[c].dtype.is_numeric()]
    if not num_cols: print("[ols] No numeric channel columns found"); sys.exit(1)

    conditions = sorted(df['condition'].unique().to_list())
    print(f"[ols] {len(num_cols)} channel(s), {len(conditions)} conditions" +
          (f", grouped by {grp_dims}" if grp_dims else ""))

    grp_combos = (list(itertools.product(*[df[d].unique().to_list() for d in grp_dims]))
                  if grp_dims else [()])
    results = []
    for combo in grp_combos:
        sub = (df.filter(pl.reduce(lambda a, b: a & b, [pl.col(d) == v for d, v in zip(grp_dims, combo)]))
               if combo else df)
        prefix = '_'.join(str(v) for v in combo) + '_' if combo else ''
        eids = sorted(sub['epoch_id'].unique().to_list())
        cond_list = [str(sub.filter(pl.col('epoch_id') == e)['condition'][0]) for e in eids]
        # Design: N dummies without intercept → betas are direct condition means
        X = np.column_stack([[1.0 if c == cond else 0.0 for c in cond_list] for cond in conditions])
        for ch in num_cols:
            y = np.array([float(sub.filter(pl.col('epoch_id') == e)[ch].mean()) for e in eids])
            model = sm.OLS(y, X).fit()
            for i, cond in enumerate(conditions):
                results.append({'channel': f"{prefix}{ch}", 'condition': cond,
                                'beta': float(model.params[i]), 'tvalue': float(model.tvalues[i]),
                                'pvalue': float(model.pvalues[i]), 'se': float(model.bse[i])})

    result_df = pl.DataFrame(results)
    out_file = f"{base}_{output_suffix}.parquet"
    result_df.write_parquet(out_file, compression='snappy')

    channels = result_df.filter(pl.col('condition') == conditions[0])['channel'].to_list()
    pl.DataFrame({
        'x_data': [channels],
        'y_data': [[result_df.filter(pl.col('condition') == c)['beta'].to_list() for c in conditions]],
        'y_var': [[result_df.filter(pl.col('condition') == c)['se'].to_list() for c in conditions]],
        'plot_type': ['grid'], 'labels': [conditions], 'x_label': ['Channel'], 'y_label': ['Beta Coefficient']
    }).write_parquet(out_file.replace('.parquet', '_vis.parquet'), compression='snappy')

    print(f"[ols] Output: {out_file} ({len(results)} rows)")
    return out_file

if __name__ == '__main__': (lambda a: ols_process(a[1], a[2] if len(a) > 2 else 'ols') if len(a) >= 2 else (print('[ols] Fit OLS regression per channel on epoched data. Outputs condition betas.\nUsage: ols_processor.py <epochs.parquet> [suffix=ols]'), sys.exit(1)))(sys.argv)
