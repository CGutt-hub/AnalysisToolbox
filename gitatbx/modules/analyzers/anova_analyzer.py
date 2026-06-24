"""ANOVA Analyzer - One-way ANOVA and L2 consolidation utilities."""
import polars as pl, sys, os, json, numpy as np
from scipy.stats import f_oneway
from statsmodels.stats.multitest import fdrcorrection

def log_info(msg): print(f"[anova] INFO: {msg}")
def log_warning(msg): print(f"[anova] WARNING: {msg}")
def log_error(msg): print(f"[anova] ERROR: {msg}")

def _sig(p: float) -> str:
    if p < 0.001: return '***'
    if p < 0.01:  return '**'
    if p < 0.05:  return '*'
    return 'ns'

def _normalize_stat_column(name: str) -> str:
    raw = str(name).strip()
    mapping = {
        'DV': 'dv',
        'dv': 'dv',
        'F': 'F',
        'df1': 'df1',
        'df2': 'df2',
        'p': 'p',
        'p (FDR-corrected)': 'p',
        'eta_sq': 'eta_sq',
        'eta2': 'eta_sq',
        'sig': 'sig',
    }
    if raw == 'η²':
        return 'eta_sq'
    return mapping.get(raw, raw.lower().replace(' ', '_'))

def _eta_sq_label(value) -> str:
    if value is None:
        return 'unknown'
    if value < 0.01:
        return 'negligible'
    if value < 0.06:
        return 'small'
    if value < 0.14:
        return 'medium'
    return 'large'

def _modality_from_path(path: str, modality_map: dict[str, list[str]]) -> str:
    name = os.path.basename(path).lower()
    if modality_map:
        for modality, patterns in modality_map.items():
            if any(pat in name for pat in patterns):
                return modality
    if 'eeg_frontal' in name:
        return 'eeg_frontal'
    if 'eeg_parietal' in name:
        return 'eeg_parietal'
    if 'eda' in name:
        return 'eda'
    if 'hrv' in name:
        return 'hrv'
    if 'fai' in name:
        return 'fai'
    return 'unknown'

def consolidate_l2_anova(files: list[str], out_base: str, modality_map_json: str | None = None) -> str:
    modality_map = {}
    if modality_map_json:
        try:
            raw = json.loads(modality_map_json)
            modality_map = {
                str(k): [str(x).lower() for x in (v if isinstance(v, list) else [v])]
                for k, v in raw.items()
            }
        except Exception as e:
            log_warning(f'Invalid --modality-map JSON, using filename heuristics: {e}')

    frames = []
    for path in files:
        df = pl.read_parquet(path)
        if df.height == 0:
            log_warning(f'Skipping empty file: {path}')
            continue

        row = df.to_dicts()[0]
        x_data = row.get('x_data', [])
        y_data = row.get('y_data', [])
        if not isinstance(x_data, list) or not isinstance(y_data, list) or len(x_data) == 0:
            log_warning(f'Skipping unexpected format: {path}')
            continue

        columns = {}
        for idx, col_name in enumerate(x_data):
            key = _normalize_stat_column(col_name)
            if idx < len(y_data) and isinstance(y_data[idx], list):
                columns[key] = y_data[idx]

        if 'dv' not in columns:
            log_warning(f'Skipping table without DV column: {path}')
            continue

        table = pl.DataFrame(columns)
        keep = [c for c in ['dv', 'F', 'df1', 'df2', 'p', 'eta_sq', 'sig'] if c in table.columns]
        table = table.select(keep)
        for col, dtype in [('F', pl.Float64), ('p', pl.Float64), ('eta_sq', pl.Float64), ('df1', pl.Int64), ('df2', pl.Int64)]:
            if col in table.columns:
                table = table.with_columns(pl.col(col).cast(dtype, strict=False))

        modality = _modality_from_path(path, modality_map)
        table = table.with_columns([
            pl.lit(modality).alias('modality'),
            pl.lit(os.path.basename(path)).alias('source_file')
        ])

        if 'eta_sq' in table.columns:
            table = table.with_columns(
                pl.col('eta_sq').map_elements(_eta_sq_label, return_dtype=pl.String).alias('eta_sq_label')
            )
        if 'p' in table.columns:
            table = table.with_columns((pl.col('p') < 0.05).alias('reject_h0_p_lt_0_05'))

        frames.append(table.select([
            c for c in ['modality', 'dv', 'F', 'df1', 'df2', 'p', 'sig', 'eta_sq', 'eta_sq_label', 'reject_h0_p_lt_0_05', 'source_file']
            if c in table.columns
        ]))

    if not frames:
        log_error('No valid ANOVA tables could be parsed for L2 consolidation')
        sys.exit(1)

    result = pl.concat(frames, how='diagonal')
    if 'modality' in result.columns and 'dv' in result.columns:
        result = result.sort(['modality', 'dv'])

    out_file = os.path.join(os.getcwd(), f'{out_base}.parquet')
    result.write_parquet(out_file, compression='gzip')
    print(f'[anova] L2 consolidation output: {out_file}')
    print(out_file)
    return out_file

def anova_analyze(ip: str, dv: str, between: str, apply_fdr: bool = False,
                  y_lim: float | None = None, group_by: str | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    df = pl.read_parquet(ip).to_pandas()

    meta_cols = {between, 'epoch_id', 'sub_epoch_id', 'participant_id', 'window_id',
                 'condition', 'region', 'source'}
    if group_by:
        meta_cols.add(group_by)

    def _run_anova(sub_df) -> tuple[list, list]:
        """Run ANOVA on sub_df; returns (rows, p_vals_raw)."""
        if dv.lower() == 'auto':
            dv_cols = [c for c in sub_df.select_dtypes(include='number').columns if c not in meta_cols]
        else:
            dv_cols = [dv] if dv not in meta_cols else []
        rows, p_vals_raw = [], []
        for col in dv_cols:
            try:
                group_vals = [sub_df.loc[sub_df[between] == cond, col].dropna().values
                              for cond in sub_df[between].dropna().unique()]
                if len(group_vals) < 2 or any(len(g) == 0 for g in group_vals):
                    log_warning(f"Skipping {col}: insufficient groups"); continue
                F, p = f_oneway(*group_vals)
                k  = len(group_vals)
                N  = sum(len(g) for g in group_vals)
                grand_mean = np.concatenate(group_vals).mean()
                ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in group_vals)
                ss_total   = sum(((g - grand_mean) ** 2).sum() for g in group_vals)
                eta_sq = float(ss_between / ss_total) if ss_total > 0 else 0.0
                rows.append({'dv': col, 'F': float(F), 'df1': k - 1, 'df2': N - k,
                             'p': float(p), 'eta_sq': eta_sq})
                p_vals_raw.append(float(p))
            except Exception as e:
                log_warning(f"ANOVA failed for {col}: {e}")
        return rows, p_vals_raw

    if group_by and group_by in df.columns:
        groups = sorted(df[group_by].dropna().unique().tolist())
        log_info(f"ANOVA: {ip}, DVs={dv}, between={between}, group_by={group_by}({groups}), fdr={apply_fdr}")
        all_rows = []
        all_p_raw = []
        for grp in groups:
            sub = df[df[group_by] == grp]
            rows, p_raw = _run_anova(sub)
            for r in rows:
                r[group_by] = grp
            all_rows.extend(rows)
            all_p_raw.extend(p_raw)
        if not all_rows:
            log_error("All ANOVA runs failed"); sys.exit(1)
        if apply_fdr and len(all_p_raw) > 1:
            _, p_corrected = fdrcorrection(all_p_raw)
            for i, row in enumerate(all_rows):
                row['p'] = float(p_corrected[i])
        for row in all_rows:
            row['sig'] = _sig(row['p'])
        col_names = [group_by, 'DV', 'F', 'df1', 'df2', 'p', 'η²', 'sig']
        col_data  = [
            [r[group_by]            for r in all_rows],
            [r['dv']                for r in all_rows],
            [round(r['F'],    3)    for r in all_rows],
            [r['df1']               for r in all_rows],
            [r['df2']               for r in all_rows],
            [round(r['p'],    4)    for r in all_rows],
            [round(r['eta_sq'], 3)  for r in all_rows],
            [r['sig']               for r in all_rows],
        ]
        rows = all_rows
    else:
        if group_by:
            log_warning(f"group_by='{group_by}' not found in columns {list(df.columns)} — ignoring")
        log_info(f"ANOVA: {ip}, DVs={dv}, between={between}, fdr={apply_fdr}")
        rows, p_vals_raw = _run_anova(df)
        if not rows:
            log_error("All ANOVA runs failed"); sys.exit(1)
        if apply_fdr and len(p_vals_raw) > 1:
            _, p_corrected = fdrcorrection(p_vals_raw)
            for i, row in enumerate(rows):
                row['p'] = float(p_corrected[i])
        for row in rows:
            row['sig'] = _sig(row['p'])
        col_names = ['DV', 'F', 'df1', 'df2', 'p', 'η²', 'sig']
        col_data  = [
            [r['dv']                for r in rows],
            [round(r['F'],    3)    for r in rows],
            [r['df1']               for r in rows],
            [r['df2']               for r in rows],
            [round(r['p'],    4)    for r in rows],
            [round(r['eta_sq'], 3)  for r in rows],
            [r['sig']               for r in rows],
        ]
    p_label = f"p (FDR-corrected)" if apply_fdr else "p"

    base = os.path.splitext(os.path.basename(ip))[0]
    out_file = os.path.join(os.getcwd(), f"{base}_anova.parquet")
    pl.DataFrame([{
        'x_data':    col_names,
        'y_data':    col_data,
        'y_var':     None,
        'plot_type': 'table',
        'x_label':   between,
        'y_label':   p_label,
        'y_ticks':   y_lim,
    }]).write_parquet(out_file, compression='gzip')
    print(f"[anova] Output: {out_file}")
    print(out_file)
    return out_file

if __name__ == '__main__':
    if '--consolidate-l2' in sys.argv:
        args = [a for a in sys.argv[1:] if a != '--consolidate-l2']
        modality_map_json = None
        if '--modality-map' in args:
            idx = args.index('--modality-map')
            if idx + 1 >= len(args):
                print('[anova] Missing value for --modality-map')
                sys.exit(1)
            modality_map_json = args[idx + 1]
            del args[idx:idx + 2]

        parquet_files = [x for x in args if x.endswith('.parquet') and os.path.exists(x)]
        other_args = [x for x in args if not (x.endswith('.parquet') and os.path.exists(x))]
        if not parquet_files or not other_args:
            print('[anova] L2 consolidate usage: anova_analyzer.py --consolidate-l2 <anova1.parquet> [more.parquet ...] [--modality-map <json>] <out_basename>')
            sys.exit(1)
        consolidate_l2_anova(parquet_files, other_args[-1], modality_map_json)
        sys.exit(0)

    # Split argv: leading .parquet paths are inputs, remainder are analysis params.
    # This lets the process receive multiple files via Nextflow collect() staging.
    parquet_files = [x for x in sys.argv[1:] if x.endswith('.parquet') and os.path.exists(x)]
    other_args    = [x for x in sys.argv[1:] if not (x.endswith('.parquet') and os.path.exists(x))]

    if not parquet_files or len(other_args) < 2:
        print('[anova] One-way ANOVA per DV. Usage: anova_analyzer.py <input.parquet> [more.parquet ...] <dv|auto> <between> [apply_fdr=false] [y_lim] [group_by_col]')
        sys.exit(1)

    if len(parquet_files) > 1:
        log_info(f"Multi-file input: concatenating {len(parquet_files)} parquets")
        combined = pl.concat([pl.read_parquet(f) for f in parquet_files], how='diagonal')
        # Derive output stem from common filename suffix (strip leading participant prefix)
        bases = [os.path.splitext(os.path.basename(f))[0] for f in parquet_files]
        parts = [b.split('_', 2)[-1] if b.count('_') >= 2 else b for b in bases]
        stem  = parts[0] if len(set(parts)) == 1 else 'l2_group'
        ip = os.path.join(os.getcwd(), f'{stem}.parquet')
        combined.write_parquet(ip)
    else:
        ip = parquet_files[0]

    anova_analyze(ip, other_args[0], other_args[1],
                  len(other_args) > 2 and other_args[2].lower() in ['1', 'true', 'yes'],
                  float(other_args[3]) if len(other_args) > 3 and other_args[3] and other_args[3].lower() not in ('none', 'terminal') else None,
                  other_args[4] if len(other_args) > 4 and other_args[4].lower() not in ('none', 'terminal') else None)