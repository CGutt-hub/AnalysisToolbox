import os
import sys
import ast
import warnings
from typing import Any, Dict, List, Optional, Tuple
import polars as pl
import numpy as np
import pandas as pd

# Unterdrücke numpy-spezifische Laufzeit-Warnungen
warnings.filterwarnings('ignore', category=RuntimeWarning)

def log_info(msg: str) -> None:    print(f"[asymmetry] INFO: {msg}")
def log_warning(msg: str) -> None: print(f"[asymmetry] WARNING: {msg}")
def log_error(msg: str) -> None:   print(f"[asymmetry] ERROR: {msg}")

def compute_asymmetry(ip: str, pairs: list, mode: str = 'log', 
                      band: str | None = None, y_lim: float | None = None, 
                      y_label: str | None = None, epoch_output: bool = False) -> str:
    suffix = 'fai'
    print(f"[asymmetry] Asymmetry analysis: {ip}, pairs={pairs}, mode={mode}")
    
    df = pl.read_parquet(ip)
    # Garantiert einen reinen String für Dateinamen (kein Tupel)
    base = os.path.splitext(os.path.basename(ip))[0]

    # 1. Nextflow Signal Pointer auflösen
    if ('signal' in df.columns and 'folder_path' in df.columns
            and 'region' not in df.columns and 'channel' not in df.columns
            and 'x_data' not in df.columns and 'epoch_id' not in df.columns):
        folder = str(df['folder_path'].item(0))
        if os.path.isdir(folder):
            parquets = sorted([os.path.join(folder, fn) for fn in os.listdir(folder) if fn.endswith('.parquet')])
            if parquets:
                df = pl.concat([pl.read_parquet(p) for p in parquets], how='diagonal')
            else:
                sys.exit(1)

    # 2. Formatprüfung & Intelligente Spaltenerkennung
    if 'channel' in df.columns or 'region' in df.columns:
        region_col = 'channel' if 'channel' in df.columns else 'region'
        
        # KORREKTUR: Erkennt dynamisch, ob das Band (z.B. 'alpha') direkt die Spalte ist!
        value_col = None
        if 'value' in df.columns:
            value_col = 'value'
        elif 'power' in df.columns:
            value_col = 'power'
        elif band and band in df.columns:
            value_col = band
        
        if not value_col:
            raise ValueError(f"No value column found. Expected 'value', 'power' or '{band}', found columns: {df.columns}")
        
        sem_col = 'sem' if 'sem' in df.columns else 'power_std' if 'power_std' in df.columns else None
        has_epoch_data = 'epoch_id' in df.columns
        
        # Nur filtern, wenn die Spalte 'band' existiert (ist bei gepivoteten Daten nicht der Fall)
        if band and 'band' in df.columns:
            df = df.filter(pl.col('band') == band)
        
        # Mathematischer, vollkommen agnostischer Rechenkern
        def compute_asym(data_df: pl.DataFrame) -> Tuple[List[float], List[float]]:
            if len(data_df) > data_df[region_col].n_unique():
                if sem_col and sem_col in data_df.columns:
                    region_data = data_df.group_by(region_col).agg([
                        pl.col(value_col).mean().alias('value'), pl.col(sem_col).mean().alias('sem')
                    ])
                elif has_epoch_data:
                    region_data = data_df.group_by(region_col).agg([
                        pl.col(value_col).mean().alias('value'),
                        (pl.col(value_col).std() / pl.col(value_col).count().cast(pl.Float64).sqrt()).alias('sem')
                    ])
                else:
                    region_data = data_df.group_by(region_col).agg([
                        pl.col(value_col).mean().alias('value'), pl.lit(0.0).alias('sem')
                    ])
            else:
                region_data = data_df.rename({value_col: 'value'})
                if sem_col and sem_col in data_df.columns:
                    region_data = region_data.rename({sem_col: 'sem'})
                elif 'sem' not in region_data.columns:
                    region_data = region_data.with_columns(pl.lit(0.0).alias('sem'))
            
            region_dict = {row[region_col]: row['value'] for row in region_data.to_dicts()}
            region_sem = {row[region_col]: row.get('sem', 0.0) for row in region_data.to_dicts()}
            
            asym_vals, asym_sems = [], []
            for left, right in pairs:
                left_raw = region_dict.get(left)
                right_raw = region_dict.get(right)
                sem_L = region_sem.get(left, 0.0)
                sem_R = region_sem.get(right, 0.0)
                
                if left_raw is None or right_raw is None: 
                    continue
                
                left_val: float = 0.0
                right_val: float = 0.0
                
                # GENERISCHER VEKTOR-SCHUTZ: Verarbeitet Arrays (Musik, EEG, Finanz-Verläufe)
                if isinstance(left_raw, (list, np.ndarray)) and isinstance(right_raw, (list, np.ndarray)):
                    l_arr = np.array(left_raw, dtype=np.float64)
                    r_arr = np.array(right_raw, dtype=np.float64)
                    
                    if band and ',' in str(band):
                        try:
                            b_start, b_end = map(float, str(band).split(','))
                            x_axis = np.array(data_df['x_data'].item(0)) if 'x_data' in data_df.columns else np.arange(len(l_arr))
                            mask = (x_axis >= b_start) & (x_axis <= b_end)
                            left_val = float(np.mean(l_arr[mask])) if mask.any() else 0.0
                            right_val = float(np.mean(r_arr[mask])) if mask.any() else 0.0
                        except Exception:
                            left_val, right_val = float(np.mean(l_arr)), float(np.mean(r_arr))
                    else:
                        left_val, right_val = float(np.mean(l_arr)), float(np.mean(r_arr))
                else:
                    # KORREKTUR: import typing ist nicht mal nötig, wir überschreiben die Typvermutung des Linters
                    # durch eine explizite String/Skalar-Vorkonvertierung, die Pyright mathematisch beruhigt!
                    left_val = float(str(left_raw)) if isinstance(left_raw, (str, int, float)) else 0.0
                    right_val = float(str(right_raw)) if isinstance(right_raw, (str, int, float)) else 0.0
                
                # Absicherung gegen Log-Fehler
                if left_val <= 0.0 or right_val <= 0.0:
                    left_val, right_val = 1e-6, 1e-6

                if mode == 'log':
                    asym = np.log(right_val) - np.log(left_val)
                    sem = np.sqrt((sem_L/left_val)**2 + (sem_R/right_val)**2) if left_val > 0 else 0.0
                else:
                    asym = left_val - right_val
                    sem = np.sqrt(sem_L**2 + sem_R**2)
                
                asym_vals.append(float(asym))
                asym_sems.append(float(sem))
            return asym_vals, asym_sems

        # 3. Epoch-Level flat-table Export
        if epoch_output and 'epoch_id' in df.columns:
            epoch_ids = df['epoch_id'].unique().sort().to_list()
            records = []
            for eid in epoch_ids:
                epoch_df = df.filter(pl.col('epoch_id') == eid)
                cond_val = epoch_df['condition'].item(0) if 'condition' in epoch_df.columns else base
                
                vals, _ = compute_asym(epoch_df)
                row_dict = {'condition': str(cond_val), 'epoch_id': eid}
                for pair_idx, (left, right) in enumerate(pairs):
                    if pair_idx < len(vals):
                        row_dict[f"fai_{left}_{right}"] = vals[pair_idx]
                records.append(row_dict)
            
            out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, f"{base}_{suffix}.parquet")
            pl.DataFrame(records).write_parquet(out_path, compression='gzip')
            
            log_path = os.path.join(out_folder, f"{base}_{suffix}.log.parquet")
            pl.DataFrame({
                'timestamp': [pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")],
                'level': ['INFO'], 'module': ['asymmetry_analyzer'], 'message': ['Asymmetry computation successful']
            }).write_parquet(log_path, compression='gzip')

            signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
            pl.DataFrame({
                'signal': [signal_path], 'source': [os.path.basename(ip)], 'conditions': [len(epoch_ids)], 'folder_path': [os.path.abspath(out_folder)]
            }).write_parquet(signal_path, compression='gzip')
            return signal_path

        # 4. Standard Aggregiertes Plot-ready-Output
        asym_vals, asym_sems = compute_asym(df)
        pair_labels = [f"{left}-{right}" for left, right in pairs]
        cond = df['condition'].item(0) if 'condition' in df.columns else base
        
        out_folder = os.path.join(os.getcwd(), f"{base}_{suffix}")
        os.makedirs(out_folder, exist_ok=True)
        out_path = os.path.join(out_folder, f"{base}_{suffix}.parquet")
        pl.DataFrame({
            'condition': [str(cond)], 'x_data': [pair_labels], 'y_data': [asym_vals], 'y_var': [asym_sems],
            'plot_type': ['bar'], 'x_label': ['Pair'], 'y_label': [y_label or 'Asymmetry Index'], 'y_ticks': [y_lim] if y_lim is not None else [None]
        }).write_parquet(out_path, compression='gzip')
        
        log_path = os.path.join(out_folder, f"{base}_{suffix}.log.parquet")
        pl.DataFrame({
            'timestamp': [pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")],
            'level': ['INFO'], 'module': ['asymmetry_analyzer'], 'message': ['Asymmetry computation successful']
        }).write_parquet(log_path, compression='gzip')

        signal_path = os.path.join(os.getcwd(), f"{base}_{suffix}.parquet")
        n_unique_conds = df['condition'].n_unique() if 'condition' in df.columns else 1
        pl.DataFrame({
            'signal': [signal_path], 'source': [os.path.basename(ip)], 'conditions': [int(n_unique_conds)], 'folder_path': [os.path.abspath(out_folder)]
        }).write_parquet(signal_path, compression='gzip')
        return signal_path

    else:
        log_error("Unsupported parquet layout.")
        sys.exit(1)
        return ""

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("[asymmetry] Usage: python asymmetry_analyzer.py <input.parquet> <pairs_list> [mode] [band] [y_lim] [y_label] [epoch_output]")
        sys.exit(1)
        
    input_file = sys.argv[1]
    pairs_raw = sys.argv[2].strip("'\"").replace(' ', '')
    
    if "," in pairs_raw and not pairs_raw.startswith("["):
        parts = pairs_raw.split(",")
        pairs_list = [(parts[0], parts[1])]
    else:
        if not pairs_raw.endswith("]"): pairs_raw += "]"
        try: pairs_list = ast.literal_eval(pairs_raw)
        except Exception: pairs_list = [('F3', 'F4')]

    mode_arg = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] not in ['None', 'null'] else 'log'
    band_arg = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] not in ['None', 'null'] else None
    
    ylim_arg = None
    if len(sys.argv) > 5 and sys.argv[5] not in ['None', 'null', 'true', 'false']:
        try: ylim_arg = float(sys.argv[5])
        except ValueError: ylim_arg = None
            
    ylabel_arg = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] not in ['None', 'null', 'true', 'false'] else None
    
    epoch_output_arg = False
    for arg in sys.argv[3:]:
        if 'true' in arg.lower():
            epoch_output_arg = True
            break

    compute_asymmetry(input_file, pairs_list, mode_arg, band_arg, ylim_arg, ylabel_arg, epoch_output_arg)
