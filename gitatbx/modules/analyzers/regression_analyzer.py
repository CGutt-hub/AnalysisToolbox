"""Regression Analyzer - Perform OLS regression and feature impact analysis."""
import sys, os, glob as _glob
import polars as pl, polars.selectors as cs
import numpy as np
import statsmodels.api as sm

def log_info(msg):    print(f"[regression] INFO: {msg}")
def log_warning(msg): print(f"[regression] WARNING: {msg}")
def log_error(msg):   print(f"[regression] ERROR: {msg}")

_EXCLUDE = {'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id', 'condition', 'region', 'source', 'trial_id'}
_DISPLAY_TOKENS = {'terminal', 'group_log', 'result', 'table', 'bar', 'line', 'scatter', 'heatmap', 'none', 'auto'}

def _flatten_df(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize flat, plot-spec, or signal-pointer DataFrames into tidy wide tables safely."""
    if 'signal' in df.columns and 'folder_path' in df.columns and df.height > 0:
        folder = str(df['folder_path'][0])
        data_files = sorted(_glob.glob(os.path.join(folder, "*.parquet")))
        if data_files:
            try:
                df = pl.concat([pl.read_parquet(f) for f in data_files], how='diagonal')
            except Exception as e:
                log_warning(f"Could not concatenate folder parquets: {e}")

    if 'x_data' in df.columns and 'y_data' in df.columns and df.height > 0:
        try:
            rec = df.to_dicts()[0]
            x_lst = rec.get('x_data', [])
            y_lst = rec.get('y_data', [])
            if isinstance(x_lst, list) and isinstance(y_lst, list) and len(x_lst) > 0:
                cols = {}
                for col_name, val_vec in zip(x_lst, y_lst):
                    if isinstance(val_vec, list):
                        cols[str(col_name)] = val_vec
                    else:
                        cols[str(col_name)] = [val_vec]
                max_len = max(len(v) for v in cols.values())
                for k, v in cols.items():
                    if len(v) < max_len:
                        cols[k] = list(v) + [None] * (max_len - len(v))
                df = pl.DataFrame(cols)
        except Exception as e:
            log_warning(f"Failed to unpack plot-spec structure safely: {e}")

    if 'condition' in df.columns and 'trial_id' not in df.columns:
        df = df.with_columns(pl.col('condition').cast(pl.String).alias('trial_id'))

    return df

def _load_data(files: list[str]) -> pl.DataFrame:
    valid_files = [f for f in files if os.path.exists(f)]
    if not valid_files:
        log_error("No valid input files found on disk.")
        sys.exit(1)

    dfs = []
    for f in valid_files:
        try:
            raw_df = pl.read_parquet(f)
            flat_df = _flatten_df(raw_df)
            if flat_df.height > 0:
                dfs.append(flat_df)
        except Exception as e:
            log_warning(f"Skipping malformed file {f}: {e}")

    if not dfs:
        log_error("All input files failed to load or were empty.")
        sys.exit(1)
    
    if len(dfs) == 1:
        return dfs[0]

    joined = dfs[0]
    for t in dfs[1:]:
        join_col = None
        if 'condition' in joined.columns and 'condition' in t.columns:
            join_col = 'condition'
        elif 'trial_id' in joined.columns and 'trial_id' in t.columns:
            join_col = 'trial_id'

        try:
            if join_col:
                joined = joined.join(t, on=join_col, how='inner')
                if joined.height == 0:
                    log_warning(f"Inner join on '{join_col}' resulted in 0 rows. Falling back to diagonal concat.")
                    joined = pl.concat([dfs[0], t], how='diagonal')
            elif joined.height == t.height:
                joined = pl.concat([joined, t], how='horizontal')
            else:
                log_warning("Dataframes do not share join keys and have mismatched heights. Attempting diagonal concat.")
                joined = pl.concat([joined, t], how='diagonal')
        except Exception as e:
            log_warning(f"Join/concat operation failed: {e}. Falling back to diagonal concat.")
            joined = pl.concat([joined, t], how='diagonal')

    return joined

def run_regression(files: list[str], target_y: str | None = None, predictors: list[str] | None = None) -> str:
    df = _load_data(files)
    num_cols = [c for c in df.select(cs.numeric()).columns if c not in _EXCLUDE]

    if len(num_cols) < 2:
        log_error(f"Need at least 2 numeric variables for regression, found {len(num_cols)} ({num_cols}). Halting.")
        sys.exit(1)

    dep_var = target_y if target_y and target_y in df.columns else num_cols[0]
    if target_y and target_y not in df.columns:
        log_error(f"Specified target variable '{target_y}' not found in columns: {df.columns}")
        sys.exit(1)
    
    if predictors:
        missing_preds = [p for p in predictors if p not in df.columns]
        if missing_preds:
            log_error(f"Specified predictors missing from table: {missing_preds}")
            sys.exit(1)
        pred_vars = list(dict.fromkeys([p for p in predictors if p != dep_var]))
    else:
        pred_vars = [c for c in num_cols if c != dep_var]

    if not pred_vars:
        log_error(f"No valid predictors remaining for target '{dep_var}'.")
        sys.exit(1)

    valid_df = df.select([dep_var] + pred_vars).drop_nulls()

    if valid_df.height < len(pred_vars) + 2:
        log_warning(f"Insufficient complete rows ({valid_df.height}) for {len(pred_vars)} predictors after strict drop_nulls. Attempting median imputation for missing values.")
        subset_df = df.select([dep_var] + pred_vars)
        for col in subset_df.columns:
            median_val = subset_df[col].median()
            if median_val is not None:
                subset_df = subset_df.with_columns(pl.col(col).fill_null(median_val))
        valid_df = subset_df.drop_nulls()
        
        if valid_df.height < len(pred_vars) + 2:
            log_error(f"Insufficient complete rows ({valid_df.height}) even after imputation for {len(pred_vars)} predictors. Halting regression.")
            sys.exit(1)

    Y = valid_df[dep_var].to_numpy()
    X = valid_df.select(pred_vars).to_numpy()

    non_zero_var_idx = [i for i in range(X.shape[1]) if np.std(X[:, i]) > 0]
    if not non_zero_var_idx:
        log_error("All selected predictors exhibit zero variance. Halting regression model.")
        sys.exit(1)

    pred_vars = [pred_vars[i] for i in non_zero_var_idx]
    X = X[:, non_zero_var_idx]
    X_with_const = sm.add_constant(X)

    try:
        model = sm.OLS(Y, X_with_const).fit()
    except Exception as e:
        log_error(f"OLS Fitting failed: {e}")
        sys.exit(1)

    records = []
    terms = ['Intercept'] + pred_vars
    for idx, term in enumerate(terms):
        p_val = float(model.pvalues[idx])
        sig_str = '***' if p_val < 0.001 else '**' if p_val < 0.01 else '*' if p_val < 0.05 else ''
        records.append({
            'term': term,
            'coef': round(float(model.params[idx]), 4),
            'std_err': round(float(model.bse[idx]), 4),
            't_stat': round(float(model.tvalues[idx]), 4),
            'p_val': round(p_val, 4),
            'sig': sig_str
        })

    result_df = pl.DataFrame(records)
    cols = ['term', 'coef', 'std_err', 't_stat', 'p_val', 'sig']

    base = os.path.splitext(os.path.basename(files[0]))[0]
    out_path = os.path.join(os.getcwd(), f"{base}_regression.parquet")

    pl.DataFrame({
        'condition': ['ols_regression'],
        'x_data':    [cols],
        'y_data':    [[[str(v) for v in row.values()] for row in result_df.to_dicts()]],
        'y_var':     [[[]]],
        'plot_type': ['table'],
        'x_label':   [f'Predictors of {dep_var}'],
        'y_label':   [f'R-squared: {model.rsquared:.3f} (N={valid_df.height})'],
        'y_ticks':   [''],
    }).write_parquet(out_path, compression='gzip')

    log_info(f"Regression output saved to: {out_path}")
    print(out_path)
    return out_path

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        log_error("Usage: regression_analyzer.py <file1.parquet> [--target Y] [--predictors X1,X2]")
        sys.exit(1)

    files = [a for a in args if os.path.exists(a)]
    non_files = [a for a in args if not os.path.exists(a) and a.lower() not in _DISPLAY_TOKENS]

    target = None
    preds = None

    if '--target' in non_files:
        idx = non_files.index('--target')
        if idx + 1 < len(non_files): target = non_files[idx + 1]

    if '--predictors' in non_files:
        idx = non_files.index('--predictors')
        if idx + 1 < len(non_files): preds = non_files[idx + 1].split(',')

    if not files:
        log_error("No valid parquet files found.")
        sys.exit(1)

    run_regression(files, target_y=target, predictors=preds)