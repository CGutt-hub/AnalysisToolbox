"""LMM Analyzer — Linear Mixed Model for group-level (L2) analysis.

Replaces the pseudoreplicating one-way ANOVA at the group level with a proper
Linear Mixed Model (LMM):

    DV ~ C(condition) + (1 | participant_id)

Fixed effect  : condition bin (valence_high / valence_low / arousal_high /
                               arousal_low [/ valence_neutral])
Random effect : participant random intercept — accounts for between-participant
                baseline differences and eliminates the pseudoreplication
                present in the previous pooled ANOVA approach.

Significance is assessed via a Likelihood Ratio Test (LRT) comparing the full
model (condition fixed effect + random intercept) against a null model
(intercept + random intercept only), using ML estimation (not REML) so that
fixed-effect contributions can be compared.

Effect sizes follow Nakagawa & Schielzeth (2013), J. R. Soc. Interface:
    R²_marginal   — variance explained by fixed effects (condition) only
    R²_conditional— variance explained by fixed + random effects combined
    ICC           — intraclass correlation = between-participant variance share

Usage:
    lmm_analyzer.py <file1.parquet> [file2.parquet ...] <dv|auto> <group_col>
                    [group_log] [table] [terminal]

    lmm_analyzer.py --consolidate-l2 <out_base> <file1.parquet> ...
                    [--modality-map <json>]
"""

import os, sys, json, warnings, numpy as np, pandas as pd, polars as pl, statsmodels.formula.api as smf
from scipy.stats import chi2 as chi2_dist


def log_info(msg):    print(f"[lmm] INFO: {msg}")
def log_warning(msg): print(f"[lmm] WARNING: {msg}")
def log_error(msg):   print(f"[lmm] ERROR: {msg}")


def _sig(p: float) -> str:
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'


def _participant_id_from_filename(path: str) -> str:
    """Extract participant prefix (e.g. DEAP_01) from filename like DEAP_01_eda_binned.parquet."""
    base = os.path.splitext(os.path.basename(path))[0]
    parts = base.split('_')
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return base


def _modality_from_path(path: str, modality_map: dict) -> str:
    name = os.path.basename(path).lower()
    if modality_map:
        for modality, patterns in modality_map.items():
            if any(pat in name for pat in patterns):
                return modality
    if 'eeg_frontal' in name: return 'eeg_frontal'
    if 'eeg_parietal' in name: return 'eeg_parietal'
    if 'eda' in name: return 'eda'
    if 'hrv' in name: return 'hrv'
    if 'fai' in name: return 'fai'
    return 'unknown'


def _nakagawa_r2(mdf) -> tuple[float, float, float]:
    """Marginal R², conditional R², and ICC following Nakagawa & Schielzeth (2013)."""
    try:
        fixed_pred = mdf.fittedvalues - mdf.resid
        sigma2_f = float(np.var(fixed_pred, ddof=0))
        sigma2_r = float(mdf.cov_re.values[0][0])
        sigma2_e = float(mdf.scale)
        total = sigma2_f + sigma2_r + sigma2_e
        r2_m = sigma2_f / total if total > 0 else 0.0
        r2_c = (sigma2_f + sigma2_r) / total if total > 0 else 0.0
        icc  = sigma2_r / (sigma2_r + sigma2_e) if (sigma2_r + sigma2_e) > 0 else 0.0
        return round(r2_m, 4), round(r2_c, 4), round(icc, 4)
    except Exception as e:
        log_warning(f"R²/ICC computation failed: {e}")
        return float('nan'), float('nan'), float('nan')


def _fit_lmm_one_dv(df: pd.DataFrame, dv_col: str, condition_col: str) -> dict | None:
    """Fit null and full LMM for one DV column; returns statistics dict or None on failure."""
    sub = df[[dv_col, condition_col, 'participant_id']].dropna()
    n_conds = sub[condition_col].nunique()
    n_part  = sub['participant_id'].nunique()

    if len(sub) < 20:
        log_warning(f"Too few rows ({len(sub)}) for '{dv_col}' — skipping")
        return None
    if n_conds < 2:
        log_warning(f"Only {n_conds} condition(s) for '{dv_col}' — skipping")
        return None
    if n_part < 3:
        log_warning(f"Only {n_part} participant(s) for '{dv_col}' — random effects not estimable, skipping")
        return None

    # Suppress convergence warnings from statsmodels during fitting
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')

        # ── Null model (intercept + random intercept) ──────────────────────────
    mdf_null = None
    for method in ('lbfgs', 'bfgs', 'nm'):
        try:
            md_null  = smf.mixedlm(f"Q('{dv_col}') ~ 1", sub, groups=sub['participant_id'])
            mdf_null = md_null.fit(reml=False, method=method)
            break
        except Exception:
            continue
    if mdf_null is None:
        log_warning(f"Null model failed to converge for '{dv_col}' — skipping")
        return None

    # ── Full model (condition fixed effect + random intercept) ─────────────
    mdf_full = None
    for method in ('lbfgs', 'bfgs', 'nm'):
        try:
            md_full  = smf.mixedlm(f"Q('{dv_col}') ~ C({condition_col})", sub,
                                   groups=sub['participant_id'])
            mdf_full = md_full.fit(reml=False, method=method)
            break
        except Exception:
            continue
    if mdf_full is None:
            return None

    # ── LRT ───────────────────────────────────────────────────────────────────
    lrt_chi2 = max(2.0 * (mdf_full.llf - mdf_null.llf), 0.0)
    lrt_df   = max(int(len(mdf_full.fe_params) - len(mdf_null.fe_params)), 1)
    p_lrt    = float(chi2_dist.sf(lrt_chi2, lrt_df))

    # ── Effect sizes (Nakagawa & Schielzeth 2013) ─────────────────────────────
    r2_m, r2_c, icc = _nakagawa_r2(mdf_full)

    # ── Contrasts (refit with REML for better parameter estimates) ────────────
    contrasts = []
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for method in ('lbfgs', 'bfgs', 'nm'):
            try:
                md_reml  = smf.mixedlm(f"Q('{dv_col}') ~ C({condition_col})", sub,
                                       groups=sub['participant_id'])
                mdf_reml = md_reml.fit(reml=True, method=method)
                for param in mdf_reml.params.index:
                    if 'Intercept' in param or 'Group Var' in param:
                        continue
                    contrasts.append({
                        'dv':       dv_col,
                        'contrast': param,
                        'beta':     round(float(mdf_reml.params[param]), 4),
                        'se':       round(float(mdf_reml.bse[param]), 4),
                        'z':        round(float(mdf_reml.tvalues[param]), 3),
                        'p':        round(float(mdf_reml.pvalues[param]), 4),
                        'sig':      _sig(float(mdf_reml.pvalues[param])),
                    })
                break
            except Exception:
                continue

    return {
        'dv':             dv_col,
        'chi2':           round(float(lrt_chi2), 3),
        'df':             lrt_df,
        'p':              round(p_lrt, 4),
        'marginal_r2':    r2_m,
        'conditional_r2': r2_c,
        'icc':            icc,
        'n':              int(len(sub)),
        'n_part':         int(n_part),
        'sig':            _sig(p_lrt),
        'contrasts':      contrasts,
    }


def lmm_analyze(files: list[str], dv: str = 'auto', group_col: str = 'condition') -> str:
    """Fit LMM for each DV; output summary table parquet + contrasts parquet.

    Participant IDs are derived from the first two underscore-delimited parts of
    each filename (e.g. 'DEAP_01' from 'DEAP_01_eda_binned.parquet').
    """
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}"); sys.exit(1)

    # Load all files and tag with participant_id from filename
    frames = []
    for f in files:
        df  = pl.read_parquet(f)
        pid = _participant_id_from_filename(f)
        df  = df.with_columns(pl.lit(pid).alias('participant_id'))
        frames.append(df)

    combined = pl.concat(frames, how='diagonal')
    n_part   = combined['participant_id'].n_unique()
    log_info(f"Combined: {len(combined)} rows from {n_part} participants")

    _META = {group_col, 'epoch_id', 'participant_id', 'condition', 'region', 'source',
             'sub_epoch_id', 'window_id', 'trial_id'}
    _NUMERIC = (pl.Float32, pl.Float64, pl.Int32, pl.Int64, pl.Int16, pl.Int8,
                pl.UInt32, pl.UInt64, pl.UInt16, pl.UInt8)

    if dv.lower() == 'auto':
        dv_cols = [c for c in combined.columns if c not in _META and combined[c].dtype in _NUMERIC]
    else:
        dv_cols = [dv] if dv in combined.columns else []

    if not dv_cols:
        log_error(f"No DV columns found (dv='{dv}', columns={combined.columns})"); sys.exit(1)

    log_info(f"DVs: {dv_cols}  |  Group: {group_col}  |  Participants: {n_part}")

    pd_df = combined.to_pandas()

    rows         = []
    all_contrasts = []
    for col in dv_cols:
        result = _fit_lmm_one_dv(pd_df, col, group_col)
        if result is None:
            continue
        all_contrasts.extend(result.pop('contrasts'))
        rows.append(result)
        log_info(f"  {col}: χ²({result['df']}) = {result['chi2']}, "
                 f"p = {result['p']}, R²_m = {result['marginal_r2']}, ICC = {result['icc']}")

    if not rows:
        log_error("All LMM fits failed"); sys.exit(1)

    # ── Derive output filename stem ─────────────────────────────────────────
    bases = [os.path.splitext(os.path.basename(f))[0] for f in files]
    parts = [b.split('_', 2)[-1] if b.count('_') >= 2 else b for b in bases]
    stem  = parts[0].replace('_binned', '') if len(set(parts)) == 1 else 'l2_group'

    # ── Summary table (flat rows, one per DV) ─────────────────────────────
    out_file = os.path.join(os.getcwd(), f"{stem}_lmm.parquet")
    flat_rows = [{k: v for k, v in r.items() if k != 'contrasts'} for r in rows]
    pl.DataFrame(flat_rows).write_parquet(out_file, compression='snappy')
    log_info(f"Summary output: {out_file}")
    print(out_file)

    # ── Contrasts table (flat, not plot-spec — raw data) ──────────────────
    if all_contrasts:
        contrasts_file = os.path.join(os.getcwd(), f"{stem}_lmm_contrasts.parquet")
        pl.DataFrame(all_contrasts).write_parquet(contrasts_file, compression='snappy')
        log_info(f"Contrasts output: {contrasts_file}")

    return out_file


def consolidate_l2_lmm(files: list[str], out_base: str,
                        modality_map_json: str | None = None) -> str:
    """Aggregate per-modality LMM summary parquets into a single group-level table.

    Each input is a table plot-spec parquet produced by lmm_analyze().
    Adds a 'modality' column derived from the filename (or the modality_map).
    """
    modality_map: dict[str, list[str]] = {}
    if modality_map_json:
        try:
            raw = json.loads(modality_map_json)
            modality_map = {
                str(k): [str(x).lower() for x in (v if isinstance(v, list) else [v])]
                for k, v in raw.items()
            }
        except Exception as e:
            log_warning(f"Invalid --modality-map JSON, using filename heuristics: {e}")

    frames = []
    for path in files:
        df = pl.read_parquet(path)
        if df.height == 0:
            log_warning(f"Skipping empty: {path}"); continue

        # Normalise column names (handle legacy capitalisation)
        rename = {c: c.lower() for c in df.columns if c != c.lower()}
        if rename:
            df = df.rename(rename)

        if 'dv' not in df.columns:
            log_warning(f"Skipping file without 'dv' column: {path}"); continue

        keep  = [c for c in ['dv', 'chi2', 'df', 'p', 'marginal_r2',
                              'conditional_r2', 'icc', 'n_part', 'sig']
                 if c in df.columns]
        table = df.select(keep)
        for col, dtype in [('chi2', pl.Float64), ('p', pl.Float64),
                           ('marginal_r2', pl.Float64), ('conditional_r2', pl.Float64),
                           ('icc', pl.Float64), ('df', pl.Int64), ('n_part', pl.Int64)]:
            if col in table.columns:
                table = table.with_columns(pl.col(col).cast(dtype, strict=False))

        modality = _modality_from_path(path, modality_map)
        table    = table.with_columns([
            pl.lit(modality).alias('modality'),
            pl.lit(os.path.basename(path)).alias('source_file'),
        ])
        if 'p' in table.columns:
            table = table.with_columns((pl.col('p') < 0.05).alias('reject_h0_p_lt_0_05'))

        frames.append(table.select([
            c for c in ['modality', 'dv', 'chi2', 'df', 'p', 'sig',
                        'marginal_r2', 'conditional_r2', 'icc', 'n_part',
                        'reject_h0_p_lt_0_05', 'source_file']
            if c in table.columns
        ]))

    if not frames:
        log_error("No valid LMM tables found for L2 consolidation"); sys.exit(1)

    result = pl.concat(frames, how='diagonal')
    if 'modality' in result.columns and 'dv' in result.columns:
        result = result.sort(['modality', 'dv'])

    out_file = os.path.join(os.getcwd(), f'{out_base}.parquet')
    result.write_parquet(out_file, compression='snappy')
    log_info(f"L2 LMM consolidation output: {out_file}")
    print(out_file)
    return out_file


def contextual_lmm(files: list[str],
                   dv_modality: str = 'auto',
                   cov_modality: str = 'auto',
                   group_col: str = 'condition',
                   out_base: str = 'contextual_lmm') -> str:
    """Within-between decomposition LMM for cross-modal group-level questions (WP3/WP4).

    Model per DV×covariate pair:
        DV_ij ~ cov_within_ij + cov_between_i + C(condition_ij) + (1 | participant_i)

    where:
        cov_between_i  = participant mean of covariate (between-person trait effect)
        cov_within_ij  = cov_ij - cov_between_i          (within-person trial fluctuation)

    beta_between answers the WP3/WP4 question: "do people with higher covariate also
    show higher DV?" — cleanly separated from individual baseline variance.
    beta_within  answers the bonus question: trial-to-trial coupling.

    Files are split by modality (inferred from filename) into DV files and covariate
    files. Use dv_modality / cov_modality = 'auto' to let the function infer from names,
    or pass explicit modality strings (e.g. 'eda', 'hrv', 'fai').
    """
    _META = {'condition', 'epoch_id', 'participant_id', 'region', 'source',
             'sub_epoch_id', 'window_id', 'trial_id', 'plot_type', 'x_label',
             'y_label', 'x_data', 'y_ticks', 'y_var', 'y_data'}
    _NUMERIC = (pl.Float32, pl.Float64, pl.Int32, pl.Int64, pl.Int16, pl.Int8,
                pl.UInt32, pl.UInt64, pl.UInt16, pl.UInt8)

    def _load_tag(path: str) -> pl.DataFrame:
        df  = pl.read_parquet(path)
        pid = _participant_id_from_filename(path)
        if 'participant_id' not in df.columns:
            df = df.with_columns(pl.lit(pid).alias('participant_id'))
        return df

    def _infer_modality(path: str) -> str:
        name = os.path.basename(path).lower()
        for mod in ('eeg_frontal', 'eeg_parietal', 'fai', 'eda', 'hrv', 'eeg'):
            if mod in name:
                return mod
        return 'unknown'

    # ── Split files into DV and covariate sets ─────────────────────────────
    cov_mod = cov_modality  # covariate modality is always explicit
    if cov_mod == 'auto':
        modalities = sorted({_infer_modality(f) for f in files})
        if not modalities:
            log_error("Cannot auto-detect covariate modality"); sys.exit(1)
        cov_mod = modalities[-1]  # last alphabetically as fallback
        log_warning(f"cov_modality='auto': using '{cov_mod}'")

    cov_files = [f for f in files if cov_mod in os.path.basename(f).lower()]
    if dv_modality == 'auto':
        # All files that are NOT covariate files become DVs
        dv_files = [f for f in files if cov_mod not in os.path.basename(f).lower()]
        dv_mod   = 'auto'
    else:
        dv_mod   = dv_modality
        dv_files = [f for f in files if dv_mod in os.path.basename(f).lower()]

    if not dv_files:
        log_error(f"No DV files found (dv_modality='{dv_modality}')"); sys.exit(1)
    if not cov_files:
        log_error(f"No covariate files found for modality '{cov_mod}'"); sys.exit(1)

    dv_label = dv_modality if dv_modality != 'auto' else ','.join(sorted({_infer_modality(f) for f in dv_files}))
    log_info(f"DV modality='{dv_label}' ({len(dv_files)} files), "
             f"covariate modality='{cov_mod}' ({len(cov_files)} files)")

    # ── Load and merge on (participant_id, epoch_id/trial_id, condition) ───
    dv_df  = pl.concat([_load_tag(f) for f in dv_files],  how='diagonal')
    cov_df = pl.concat([_load_tag(f) for f in cov_files], how='diagonal')

    join_keys = [k for k in ['participant_id', 'epoch_id', 'condition']
                 if k in dv_df.columns and k in cov_df.columns]
    if not join_keys:
        log_error("No common join keys between DV and covariate files"); sys.exit(1)
    log_info(f"Join keys: {join_keys}")

    # Rename covariate value columns to avoid clashes
    cov_val_cols = [c for c in cov_df.columns if c not in _META and cov_df[c].dtype in _NUMERIC]
    cov_rename   = {c: f'{cov_mod}_{c}' for c in cov_val_cols if not c.startswith(cov_mod)}
    if cov_rename:
        cov_df = cov_df.rename(cov_rename)
    cov_val_cols = [cov_rename.get(c, c) for c in cov_val_cols]

    merged = dv_df.join(
        cov_df.select(join_keys + cov_val_cols),
        on=join_keys, how='inner'
    )
    log_info(f"Merged shape: {merged.shape}")

    if merged.height == 0:
        log_error("Merge produced no rows — check that join keys align between files")
        sys.exit(1)

    # ── Within-between decomposition for each covariate column ─────────────
    pd_df = merged.to_pandas()
    for cov_col in cov_val_cols:
        person_mean = pd_df.groupby('participant_id')[cov_col].transform('mean')
        pd_df[f'{cov_col}_between'] = person_mean
        pd_df[f'{cov_col}_within']  = pd_df[cov_col] - person_mean

    # ── DV columns ─────────────────────────────────────────────────────────
    dv_cols = [c for c in dv_df.columns
               if c not in _META and dv_df[c].dtype in _NUMERIC]
    if not dv_cols:
        log_error(f"No numeric DV columns found in '{dv_mod}' files"); sys.exit(1)

    # ── Fit one model per DV × covariate pair ──────────────────────────────
    rows       = []
    contrasts  = []
    col_names  = ['DV', 'covariate', 'beta_within', 'p_within', 'sig_within',
                  'beta_between', 'p_between', 'sig_between',
                  'marginal_r2', 'conditional_r2', 'icc', 'n_part', 'sig_lrt']

    for dv_col in dv_cols:
        for cov_col in cov_val_cols:
            within_col  = f'{cov_col}_within'
            between_col = f'{cov_col}_between'
            sub = pd_df[[dv_col, within_col, between_col, group_col,
                         'participant_id']].dropna()

            n_part = sub['participant_id'].nunique()
            if len(sub) < 20 or n_part < 3:
                log_warning(f"Skipping {dv_col} ~ {cov_col}: n={len(sub)}, n_part={n_part}")
                continue

            formula = (f"Q('{dv_col}') ~ Q('{within_col}') + Q('{between_col}')"
                       f" + C({group_col})")

            mdf_null, mdf_full = None, None
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                for method in ('lbfgs', 'bfgs', 'nm'):
                    try:
                        mdf_null = smf.mixedlm(
                            f"Q('{dv_col}') ~ 1", sub,
                            groups=sub['participant_id']
                        ).fit(reml=False, method=method)
                        break
                    except Exception:
                        continue
                for method in ('lbfgs', 'bfgs', 'nm'):
                    try:
                        mdf_full = smf.mixedlm(
                            formula, sub, groups=sub['participant_id']
                        ).fit(reml=False, method=method)
                        break
                    except Exception:
                        continue

            if mdf_null is None or mdf_full is None:
                log_warning(f"Model failed to converge for {dv_col} ~ {cov_col}"); continue

            lrt_chi2 = max(2.0 * (mdf_full.llf - mdf_null.llf), 0.0)
            lrt_df   = max(int(len(mdf_full.fe_params) - len(mdf_null.fe_params)), 1)
            p_lrt    = float(chi2_dist.sf(lrt_chi2, lrt_df))
            r2_m, r2_c, icc = _nakagawa_r2(mdf_full)

            def _coef(name_fragment):
                for k in mdf_full.params.index:
                    if name_fragment in k:
                        return float(mdf_full.params[k]), float(mdf_full.pvalues[k])
                return float('nan'), float('nan')

            beta_w, p_w = _coef(within_col)
            beta_b, p_b = _coef(between_col)

            log_info(f"  {dv_col} ~ {cov_col}: "
                     f"β_within={beta_w:.3f}(p={p_w:.3f}), "
                     f"β_between={beta_b:.3f}(p={p_b:.3f}), "
                     f"LRT p={p_lrt:.4f}, R²_m={r2_m}")

            rows.append({
                'DV':              dv_col,
                'covariate':       cov_col,
                'beta_within':     round(beta_w, 4),
                'p_within':        round(p_w, 4),
                'sig_within':      _sig(p_w),
                'beta_between':    round(beta_b, 4),
                'p_between':       round(p_b, 4),
                'sig_between':     _sig(p_b),
                'marginal_r2':     r2_m,
                'conditional_r2':  r2_c,
                'icc':             icc,
                'n_part':          n_part,
                'sig_lrt':         _sig(p_lrt),
            })

    if not rows:
        log_error("All contextual LMM fits failed"); sys.exit(1)

    # ── Output as flat table — one row per DV×covariate pair ───────────────
    out_file = os.path.join(os.getcwd(), f'{out_base}.parquet')
    pl.DataFrame(rows).write_parquet(out_file, compression='snappy')
    log_info(f"Output: {out_file} — {len(rows)} DV×covariate pair(s)")
    print(out_file)
    return out_file


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2:
        print('[lmm] LMM group-level analysis with random participant intercept.')
        print('[lmm] Usage: lmm_analyzer.py <file1.parquet> [file2.parquet ...] <dv|auto> <group_col>')
        print('[lmm]        lmm_analyzer.py --consolidate-l2 <out_base> <file1.parquet> ...'
              ' [--modality-map <json>]')
        print('[lmm]        lmm_analyzer.py --contextual <dv_modality> <covariate_modality>'
              ' <file1.parquet> ... [group_col] [out_base]')
        sys.exit(1)

    # Strip IOInterface tokens
    _TOKENS = {'terminal', 'group_log', 'result', 'table'}

    if '--consolidate-l2' in a:
        args = [x for x in a[1:] if x != '--consolidate-l2' and x not in _TOKENS]
        modality_map_json = None
        if '--modality-map' in args:
            idx = args.index('--modality-map')
            if idx + 1 < len(args):
                modality_map_json = args[idx + 1]
                del args[idx:idx + 2]
        parquet_files = [x for x in args if x.endswith('.parquet') and os.path.exists(x)]
        other_args    = [x for x in args if not (x.endswith('.parquet') and os.path.exists(x))]
        if not parquet_files or not other_args:
            print('[lmm] --consolidate-l2 usage: lmm_analyzer.py --consolidate-l2 <out_base>'
                  ' <file1.parquet> ... [--modality-map <json>]')
            sys.exit(1)
        consolidate_l2_lmm(parquet_files, other_args[-1], modality_map_json)
    elif '--contextual' in a:
        args = [x for x in a[1:] if x != '--contextual' and x not in _TOKENS]
        parquet_files = [x for x in args if x.endswith('.parquet') and os.path.exists(x)]
        other_args    = [x for x in args if not (x.endswith('.parquet') and os.path.exists(x))]
        if not parquet_files:
            log_error("--contextual requires parquet files"); sys.exit(1)
        dv_modality  = other_args[0] if len(other_args) > 0 else 'auto'
        cov_modality = other_args[1] if len(other_args) > 1 else 'auto'
        group_col    = other_args[2] if len(other_args) > 2 else 'condition'
        out_base     = other_args[3] if len(other_args) > 3 else 'contextual_lmm'
        contextual_lmm(parquet_files, dv_modality, cov_modality, group_col, out_base)
    else:
        # Standard mode: split argv into parquet files and parameter strings
        args          = [x for x in a[1:] if x not in _TOKENS]
        parquet_files = [x for x in args if x.endswith('.parquet') and os.path.exists(x)]
        other_args    = [x for x in args if not (x.endswith('.parquet') and os.path.exists(x))]

        if not parquet_files:
            log_error("No valid parquet files provided"); sys.exit(1)

        dv_arg    = other_args[0] if other_args else 'auto'
        group_arg = other_args[1] if len(other_args) > 1 else 'condition'

        lmm_analyze(parquet_files, dv_arg, group_arg)
