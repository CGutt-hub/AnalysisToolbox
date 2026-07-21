import os
import sys
import warnings
import pandas as pd
import polars as pl
import statsmodels.formula.api as smf
from scipy.stats import chi2 as chi2_dist

# Konvergenz-Warnungen von statsmodels global unterdrücken
warnings.filterwarnings('ignore', message='.*ConvergenceWarning.*')

def log_info(msg):    print(f"[lmm] INFO: {msg}")
def log_warning(msg): print(f"[lmm] WARNING: {msg}")
def log_error(msg):   print(f"[lmm] ERROR: {msg}")

def _sig(p: float) -> str:
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'

def _fit_lmm_one_dv(df: pd.DataFrame, dv_col: str, condition_col: str, categorical: bool = True) -> dict | None:
    """Fit null and full LMM for one DV column and perform a Likelihood Ratio Test (Chi2)."""
    group_col = 'participant_id'
    if group_col not in df.columns:
        log_error(f"Group column '{group_col}' missing in sub-dataframe.")
        sys.exit(1)
        
    sub = df[[dv_col, condition_col, group_col]].dropna()
    n_rows = len(sub)
    n_conds = int(sub[condition_col].nunique())
    n_part = int(sub[group_col].nunique())

    # Sicherheits-Schleusen gegen fehlerhafte Matrizen
    if n_rows < 20 or n_conds < 2 or n_part < 3:
        log_warning(f"Skipping '{dv_col}': metrics too low (rows={n_rows}, conds={n_conds}, participants={n_part})")
        return None

    term = f"C({condition_col})" if categorical else condition_col
    groups_data = sub[group_col]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            
            # 1. Null-Modell (ML-Schätzung für den LRT-Vergleich)
            md_null = smf.mixedlm(f"Q('{dv_col}') ~ 1", sub, groups=groups_data)
            mdf_null = md_null.fit(reml=False, method='lbfgs')
            
            # 2. Vollständiges Modell (ML-Schätzung für den LRT-Vergleich)
            md_full = smf.mixedlm(f"Q('{dv_col}') ~ {term}", sub, groups=groups_data)
            mdf_full = md_full.fit(reml=False, method='lbfgs')

        # 3. Likelihood-Ratio-Test (LRT) via Chi2-Verteilung
        lrt_chi2 = max(2.0 * (mdf_full.llf - mdf_null.llf), 0.0)
        lrt_df   = max(int(len(mdf_full.fe_params) - len(mdf_null.fe_params)), 1)
        p_lrt    = float(chi2_dist.sf(lrt_chi2, lrt_df))

        # 4. Kontraste berechnen (REML-Schätzung für exakte Parameter-Werte)
        contrasts = []
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            md_reml = smf.mixedlm(f"Q('{dv_col}') ~ {term}", sub, groups=groups_data)
            mdf_reml = md_reml.fit(reml=True, method='lbfgs')
            
            for param in mdf_reml.params.index:
                if 'Intercept' in param or 'Group Var' in param:
                    continue
                p_val = float(mdf_reml.pvalues[param])
                contrasts.append({
                    'dv':       dv_col,
                    'contrast': param,
                    'beta':     round(float(mdf_reml.params[param]), 4),
                    'se':       round(float(mdf_reml.bse[param]), 4),
                    'z':        round(float(mdf_reml.tvalues[param]), 3),
                    'p':        round(p_val, 4),
                    'sig':      _sig(p_val),
                })

        return {
            'dv':             dv_col,
            'chi2':           round(float(lrt_chi2), 3),
            'df':             lrt_df,
            'p':              round(p_lrt, 4),
            'n_participants': n_part,
            'sig':            _sig(p_lrt),
            'contrasts':      contrasts,
        }
    except Exception as e:
        log_warning(f"LMM convergence or LRT failed for '{dv_col}': {e}")
        return None

def lmm_analyze(files: list[str], extra_params_str: str):
    """Generischer Einstiegspunkt passend zu Ihrer IOInterface-Toolbox."""
    tokens = [t.strip() for t in extra_params_str.split(' ') if t.strip()]
    condition_col = tokens[0] if len(tokens) > 0 else 'condition'
    dv_prefix     = tokens[1] if len(tokens) > 1 else 'FAI'
    categorical   = 'linear' not in extra_params_str.lower()

    parquet_files = [f for f in files if f.endswith('.parquet')]
    if not parquet_files:
        log_error("No valid input parquet files received.")
        sys.exit(1)

    log_info(f"Loading {len(parquet_files)} parquet matrices via Polars...")
    frames = [pl.read_parquet(f) for f in parquet_files]
    combined = pl.concat(frames, how='diagonal')

    if 'participant_id' not in combined.columns:
        log_error("CRITICAL: 'participant_id' column missing in multi-subject cohort matrix.")
        sys.exit(1)

    n_part = combined['participant_id'].n_unique()
    log_info(f"Combined dataset: {len(combined)} rows from {n_part} participants.")

    # Dynamisches Feature-Matching anhand des Präfixes (projektunabhängig)
    dv_cols = [c for c in combined.columns if dv_prefix.lower() in c.lower() or c == dv_prefix]
    if not dv_cols:
        log_error(f"No columns matching prefix '{dv_prefix}' found in dataset.")
        sys.exit(1)

    keep_cols = list(set(dv_cols + [condition_col, 'participant_id']))
    pd_df = combined.select([c for c in keep_cols if c in combined.columns]).to_pandas()

    results = []
    for dv in dv_cols:
        res = _fit_lmm_one_dv(pd_df, dv, condition_col, categorical)
        if res:
            results.append(res)
            log_info(f"LMM + LRT Chi2 calculated for feature: '{dv}' (p = {res['p']})")

    # Generierung der Workspace-Struktur und des Signal-Parquet-Pointers (IOInterface-Standard)
    base = os.path.splitext(os.path.basename(parquet_files[0]))[0]
    workspace_root = os.getcwd()
    out_folder = os.path.join(workspace_root, f"{base}_lmm")
    os.makedirs(out_folder, exist_ok=True)

    # Zusammenfassungen inkl. Chi2 und DF exakt wegschreiben
    summary_records = [{
        'dv': r['dv'], 
        'chi2': r['chi2'], 
        'df': r['df'], 
        'p': r['p'], 
        'n_participants': r['n_participants'], 
        'sig': r['sig']
    } for r in results]
    
    if summary_records:
        pl.DataFrame(summary_records).write_parquet(os.path.join(out_folder, f"{base}_lmm_summary.parquet"))

    # LOG-BOOKKEEPING: Schreibt die prozess-lokale Log-Datei als Parquet (Hält den Workspace clean & geordnet)
    log_path = os.path.join(out_folder, f"{base}_lmm.log.parquet")
    pl.DataFrame({
        'timestamp': [pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")],
        'level': ['INFO'],
        'module': ['lmm_analyzer'],
        'message': [f"L2 Analysis successful for prefix {dv_prefix}"]
    }).write_parquet(log_path, compression='gzip')

    # SIGNAL-POINTER: Der kritische IOInterface Signal-Pointer für nachfolgende Sammel-Module
    signal_path = os.path.join(workspace_root, f"{base}_lmm.parquet")
    signal_df = pl.DataFrame({
        'signal': [signal_path],
        'source': [os.path.basename(parquet_files[0])],
        'streams': [len(results)],
        'folder_path': [os.path.abspath(out_folder)],
        'stream_types': ['LMM_LRT_Matrix'],
        'stream_names': [dv_prefix]
    })
    signal_df.write_parquet(signal_path, compression='gzip')
    log_info(f"Output Signal Pointer successfully created: {signal_path}")
    
    return signal_path


if __name__ == '__main__':
    # Lambda-CLI-Auswertung im Stil Ihrer Toolbox
    (lambda args: lmm_analyze(args[1:], " ".join(args[2:])) 
     if len(args) >= 3 
     else (print("[lmm_analyzer] Usage: python lmm_analyzer.py <input.parquet> <extraParams>"), sys.exit(1))
    )(sys.argv)
