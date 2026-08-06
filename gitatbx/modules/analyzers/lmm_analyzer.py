"""Linear Mixed Models (LMM) Analyzer Module (Strict fail-fast implementation)."""
import sys, os, polars as pl
import statsmodels.formula.api as smf

def log_info(msg: str):  print(f"[lmm] INFO: {msg}")
def log_error(msg: str): print(f"[lmm] ERROR: {msg}")

def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df

def run_lmm(files: list[str], dep_var: str, pred_var: str, group_col: str) -> str:
    dfs = []
    for f in files:
        if os.path.exists(f) and os.path.getsize(f) > 12:
            try:
                pl_df = pl.read_parquet(f)
                pl_df = ensure_flat_dataframe(pl_df)
                dfs.append(pl_df)
            except Exception as e:
                log_error(f"Could not read parquet file {f}: {e}")
                sys.exit(1)
        else:
            log_error(f"Input file not found or empty: {f}")
            sys.exit(1)

    if not dfs:
        log_error("No valid input parquet files loaded.")
        sys.exit(1)

    df = pl.concat(dfs, how='diagonal_relaxed') if len(dfs) > 1 else dfs[0]

    if group_col not in df.columns:
        log_error(f"Required grouping column '{group_col}' missing from dataset columns: {list(df.columns)}")
        sys.exit(1)

    if dep_var not in df.columns:
        log_error(f"Declared dependent variable '{dep_var}' missing from columns: {list(df.columns)}")
        sys.exit(1)

    if pred_var not in df.columns:
        log_error(f"Declared predictor variable '{pred_var}' missing from columns: {list(df.columns)}")
        sys.exit(1)

    # STRICT FAIL FAST ON NULLS IN MODEL VARIABLES
    for v in [dep_var, pred_var, group_col]:
        if df[v].null_count() > 0:
            log_error(f"CRITICAL: Model variable '{v}' contains null values. Imputation disabled.")
            sys.exit(1)

    pdf = df.select([dep_var, pred_var, group_col]).to_pandas()
    if len(pdf) < 5 or pdf[group_col].nunique() < 2:
        log_error(f"Insufficient rows ({len(pdf)}) or groups ({pdf[group_col].nunique()}) to fit LMM model.")
        sys.exit(1)

    formula = f"{dep_var} ~ {pred_var}"
    try:
        model = smf.mixedlm(formula, pdf, groups=pdf[group_col]).fit()
        if not model.converged:
            log_error(f"MixedLM fit failed to converge for formula '{formula}'.")
            sys.exit(1)
    except Exception as e:
        log_error(f"MixedLM model execution raised exception: {e}")
        sys.exit(1)

    records = [{
        'term': str(p),
        'coef': round(float(model.params[p]), 4),
        'std_err': round(float(model.bse[p]), 4),
        'z_stat': round(float(model.tvalues[p]) if hasattr(model, 'tvalues') else float(model.zvalues[p]), 4),
        'p_val': round(float(model.pvalues[p]), 4),
        'sig': '***' if model.pvalues[p] < 0.001 else '**' if model.pvalues[p] < 0.01 else '*' if model.pvalues[p] < 0.05 else 'ns'
    } for p in model.params.index]

    base = os.path.splitext(os.path.basename(files[0]))[0]
    out_path = os.path.join(os.getcwd(), f"{base}_lmm.parquet")
    pl.DataFrame(records).write_parquet(out_path, compression='gzip')

    log_info(f"Output generated: {out_path}")
    print(out_path)
    return out_path

if __name__ == '__main__':
    args = sys.argv[1:]
    files = [a for a in args if os.path.exists(a) and a.endswith('.parquet')]
    terms = [a.strip(" '\"\\") for a in args if not os.path.exists(a) and not a.startswith('--')]
    
    if not files or len(terms) != 3:
        log_error("CRITICAL: Exact parameters required: <input_files...> <dep_var> <pred_var> <group_col>")
        sys.exit(1)

    run_lmm(files, dep_var=terms[0], pred_var=terms[1], group_col=terms[2])