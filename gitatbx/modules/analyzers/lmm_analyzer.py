#!/usr/bin/env python3
"""
Linear & Cumulative Link Mixed Models (LMM / CLMM) Analyzer Module.
Strict fail-fast implementation with generic schema auto-detection.
"""
import sys
import os
import re
from typing import Any, Dict, List
import numpy as np
import polars as pl
import statsmodels.formula.api as smf


def log_info(msg: str) -> None:
    print(f"[lmm] INFO: {msg}")


def log_error(msg: str) -> None:
    print(f"[lmm] ERROR: {msg}")


def ensure_flat_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    list_cols = [c for c, dt in df.schema.items() if isinstance(dt, pl.List)]
    return df.explode(list_cols) if list_cols else df


def is_ordinal(series: Any) -> bool:
    """Check if DV represents discrete ordinal ratings (<= 9 integer steps)."""
    clean_series = series.dropna()
    if len(clean_series) == 0:
        return False
    unique_vals = np.unique(clean_series)
    is_integer_like = bool(np.all(np.isclose(unique_vals, np.round(unique_vals))))
    return is_integer_like and len(unique_vals) <= 9


def run_mixed_model(files: List[str], dep_var: str, pred_var: str, group_col: str) -> str:
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

    df = pl.concat(dfs, how='diagonal_relaxed') if len(dfs) > 1 else dfs[0]
    pred_cols = list(dict.fromkeys(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', pred_var)))

    # --- STRICT FAIL-FAST SCHEMA VALIDATION ---
    if not pred_cols:
        log_error("CRITICAL: No valid predictor variables provided.")
        sys.exit(1)

    if group_col not in df.columns or dep_var not in df.columns:
        log_error(f"CRITICAL: Missing grouping '{group_col}' or DV '{dep_var}' in schema.")
        sys.exit(1)

    missing_preds = [pv for pv in pred_cols if pv not in df.columns]
    if missing_preds:
        log_error(f"CRITICAL: Predictor(s) {missing_preds} missing from columns.")
        sys.exit(1)

    model_vars = list(dict.fromkeys([dep_var] + pred_cols + [group_col]))
    for v in model_vars:
        if df[v].null_count() > 0:
            log_error(f"CRITICAL: Model variable '{v}' contains null values.")
            sys.exit(1)

    pdf = df.select(model_vars).to_pandas()
    if len(pdf) < 5 or pdf[group_col].nunique() < 2:
        log_error(f"CRITICAL: Insufficient rows ({len(pdf)}) or groups ({pdf[group_col].nunique()}).")
        sys.exit(1)

    rhs_formula = pred_var.replace(',', ' + ')
    formula = f"{dep_var} ~ {rhs_formula}"
    use_ordinal = is_ordinal(pdf[dep_var])

    records: List[Dict[str, Any]] = []

    if use_ordinal:
        log_info(f"Detected ordinal DV '{dep_var}'. Running Cumulative Link Mixed Model (CLMM)...")
        try:
            import bambi as bmb  # type: ignore
            import arviz as az   # type: ignore

            pdf[dep_var] = pdf[dep_var].astype(int)
            bmb_formula = f"{dep_var} ~ {rhs_formula} + (1 + {rhs_formula} | {group_col})"
            model = bmb.Model(bmb_formula, pdf, family="ordinal")
            results = model.fit(draws=1000, tune=500, progressbar=False)

            preds = model.predict(results, inplace=False)
            mean_probs = preds.posterior[f"{dep_var}_probs"].mean(dim=["chain", "draw"]).values
            categories = np.sort(pdf[dep_var].unique())
            exp_values = np.sum(mean_probs * categories, axis=1)

            pdf['lmm_fit_fixed'] = exp_values
            pdf['lmm_fit_group'] = exp_values
            pdf['lmm_residual']  = pdf[dep_var] - exp_values

            summary_df = az.summary(results)
            records = [{
                'term': str(idx),
                'coef': round(float(row['mean']), 4),
                'std_err': round(float(row['sd']), 4),
                'z_stat': round(float(row['mean'] / (row['sd'] + 1e-8)), 4),
                'p_val': 0.001 if (row['hdi_2.5%'] > 0 or row['hdi_97.5%'] < 0) else 0.1,
                'sig': '***' if (row['hdi_2.5%'] > 0 or row['hdi_97.5%'] < 0) else 'ns'
            } for idx, row in summary_df.iterrows()]

        except Exception as e:
            log_error(f"CRITICAL: CLMM fitting failed: {e}")
            sys.exit(1)

    else:
        log_info(f"Detected continuous DV '{dep_var}'. Running Gaussian LMM...")
        model_fit = None

        # Step 1: Attempt Random Slope Model
        try:
            log_info(f"Attempting random slope model for '{formula}'...")
            slope_model = smf.mixedlm(formula, pdf, groups=pdf[group_col], re_formula=f"~ {rhs_formula}")
            res = slope_model.fit(maxiter=200, method="lbfgs")
            if res.converged:
                model_fit = res
            else:
                log_info("Random slope (lbfgs) did not converge. Retrying with Powell optimizer...")
                res = slope_model.fit(maxiter=200, method="powell")
                if res.converged:
                    model_fit = res
        except Exception as e:
            log_info(f"Random slope fit raised exception: {e}")

        # Step 2: Fall back to Random Intercept Model if Random Slope failed to converge
        if model_fit is None or not model_fit.converged:
            log_info("Random slope fit failed to converge. Falling back to Random Intercept model...")
            try:
                intercept_model = smf.mixedlm(formula, pdf, groups=pdf[group_col])
                res = intercept_model.fit(maxiter=200, method="lbfgs")
                if res.converged:
                    model_fit = res
                else:
                    log_info("Random intercept (lbfgs) did not converge. Retrying with Powell optimizer...")
                    res = intercept_model.fit(maxiter=200, method="powell")
                    if res.converged:
                        model_fit = res
            except Exception as e:
                log_error(f"CRITICAL: Random intercept execution failure: {e}")
                sys.exit(1)

        if model_fit is None or not model_fit.converged:
            log_error(f"CRITICAL: MixedLM fit failed to converge for formula '{formula}' across all models and optimizers.")
            sys.exit(1)

        records = [{
            'term': str(p),
            'coef': round(float(model_fit.params[p]), 4),
            'std_err': round(float(model_fit.bse[p]), 4),
            'z_stat': round(float(model_fit.tvalues[p]) if hasattr(model_fit, 'tvalues') else float(model_fit.zvalues[p]), 4),
            'p_val': round(float(model_fit.pvalues[p]), 4),
            'sig': '***' if model_fit.pvalues[p] < 0.001 else '**' if model_fit.pvalues[p] < 0.01 else '*' if model_fit.pvalues[p] < 0.05 else 'ns'
        } for p in model_fit.params.index]

        pdf['lmm_fit_fixed'] = model_fit.predict(pdf)
        pdf['lmm_fit_group'] = model_fit.fittedvalues
        pdf['lmm_residual']  = model_fit.resid

    base = os.path.splitext(os.path.basename(files[0]))[0]
    preds_slug = "_".join(pred_cols)

    summary_path = os.path.join(os.getcwd(), f"{base}_lmm_{dep_var}_{preds_slug}_summary.parquet")
    pl.DataFrame(records).write_parquet(summary_path, compression='gzip')

    out_path = os.path.join(os.getcwd(), f"{base}_lmm_{dep_var}_{preds_slug}.parquet")
    pl.from_pandas(pdf).write_parquet(out_path, compression='gzip')

    log_info(f"Saved row-level predictions to: {out_path}")
    print(out_path)
    return out_path


if __name__ == '__main__':
    args = sys.argv[1:]
    files = [a for a in args if os.path.exists(a) and a.endswith('.parquet')]
    terms = [a.strip(" '\"\\") for a in args if not os.path.exists(a) and not a.startswith('--')]

    if not files or len(terms) != 3:
        log_error("CRITICAL: Exact parameters required: <input_files...> <dep_var> <pred_var(s)> <group_col>")
        sys.exit(1)

    run_mixed_model(files, dep_var=terms[0], pred_var=terms[1], group_col=terms[2])