"""PSD Analyzer - Computes Welch's Power Spectral Density across frequency bands and regions."""
import os
import sys
import ast
import warnings
from typing import Any, Dict
import polars as pl
import numpy as np
from scipy import signal

warnings.filterwarnings('ignore', category=RuntimeWarning)

def log_info(msg: str) -> None:    print(f"[psd] INFO: {msg}")
def log_warning(msg: str) -> None: print(f"[psd] WARNING: {msg}")
def log_error(msg: str) -> None:   print(f"[psd] ERROR: {msg}")

def compute_psd(ip: str, bands: dict, channels: list | None = None, regions: dict | None = None, y_lim: float | None = None) -> str:
    print(f"[psd] Loading: {ip}")
    if not os.path.exists(ip):
        log_error(f"Input file not found: {ip}")
        sys.exit(1)

    df = pl.read_parquet(ip)
    
    if len(df) == 0:
        log_error("Empty input DataFrame — no epochs to compute PSD, halting branch.")
        sys.exit(1)
    
    if 'epoch_id' not in df.columns or 'condition' not in df.columns:
        log_error("Input DataFrame missing required 'epoch_id' or 'condition' columns.")
        sys.exit(1)

    ch_names = [c for c in df.columns if c not in ['condition', 'epoch_id', 'time']]
    if not ch_names:
        log_error("No signal channels found in input DataFrame.")
        sys.exit(1)
    
    region_mode = False
    regions_dict: Dict[str, Any] = {}
    if isinstance(regions, dict) and len(regions) > 0:
        region_mode = True
        regions_dict = regions

    if region_mode:
        print(f"[psd] Regional mode: {len(regions_dict)} regions") 
    else:
        if channels:
            ch_names = [c for c in ch_names if c in channels]

    epoch_ids_all = df['epoch_id'].unique().to_list()
    if not epoch_ids_all:
        log_error("No epochs found.")
        sys.exit(1)
        
    first_epoch_df = df.filter(pl.col('epoch_id') == epoch_ids_all[0])
    times = first_epoch_df['time'].to_numpy() if 'time' in first_epoch_df.columns else np.array([])
    
    if len(times) > 1:
        dt = float(times[1]) - float(times[0])
    else:
        dt = 1.0 / 128.0
    sfreq = 1.0 / dt if dt > 0 else 128.0
    
    epoch_ids = df['epoch_id'].unique().to_list()
    conditions = [str(df.filter(pl.col('epoch_id') == eid)['condition'].to_list()[0]) for eid in epoch_ids]
    
    print(f"[psd] Data: {len(epoch_ids)} epochs, {sfreq:.1f} Hz, Bands: {list(bands.keys())}")
    
    results = []
    nperseg = min(256, len(times) if len(times) > 0 else 256)
    if nperseg < 2:
        log_error("Epoch sample size too small for Welch PSD computation.")
        sys.exit(1)
    
    if region_mode:
        for ep_idx, eid in enumerate(epoch_ids):
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            cond = conditions[ep_idx]
            
            channel_psds = {}
            for ch in ch_names:
                if ch in epoch_df.columns:
                    f_welch, p_welch = signal.welch(epoch_df[ch].to_numpy(), fs=sfreq, nperseg=nperseg)
                    channel_psds[ch] = (f_welch, p_welch)
            
            for region_name, region_channels in regions_dict.items():
                available_chs = [ch for ch in region_channels if ch in channel_psds]
                if not available_chs: continue
                
                avg_psd = np.mean([channel_psds[ch][1] for ch in available_chs], axis=0)
                freqs = channel_psds[available_chs[0]][0]
                
                for band_name, (fmin, fmax) in bands.items():
                    mask = (freqs >= fmin) & (freqs <= fmax)
                    power = float(np.mean(avg_psd[mask])) if mask.any() else 0.0
                    results.append({
                        'condition': cond, 'epoch_id': eid, 'region': region_name, 'band': band_name, 'power': power
                    })
    else:
        for ep_idx, eid in enumerate(epoch_ids):
            epoch_df = df.filter(pl.col('epoch_id') == eid)
            cond = conditions[ep_idx]
            
            for ch in ch_names:
                freqs, p = signal.welch(epoch_df[ch].to_numpy(), fs=sfreq, nperseg=nperseg)
                for band_name, (fmin, fmax) in bands.items():
                    mask = (freqs >= fmin) & (freqs <= fmax)
                    power = float(np.mean(p[mask])) if mask.any() else 0.0
                    results.append({
                        'condition': cond, 'epoch_id': eid, 'channel': ch, 'band': band_name, 'power': power
                    })
    
    if not results:
        log_error("PSD extraction yielded zero results. Halting.")
        sys.exit(1)

    result_df = pl.DataFrame(results)
    conds = sorted(result_df['condition'].unique().to_list())
    base = os.path.splitext(os.path.basename(ip))[0]
    out_folder = os.path.join(os.getcwd(), f"{base}_psd")
    os.makedirs(out_folder, exist_ok=True)
    
    for idx, cond in enumerate(conds):
        cond_data = result_df.filter(pl.col('condition') == cond)
        
        if region_mode:
            epoch_agg = cond_data.group_by(['epoch_id', 'region', 'band']).agg([
                pl.col('power').mean().alias('value')
            ]).with_columns(pl.lit(cond).alias('condition'))
            epoch_pivot = epoch_agg.pivot(on='band', index=['condition', 'epoch_id', 'region'], values='value')
            epoch_pivot.write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}.parquet"), compression='gzip')
        else:
            raw_df = cond_data.select(['condition', 'epoch_id', 'channel', 'band', 'power'])
            raw_df.write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}.parquet"), compression='gzip')
            epoch_agg = cond_data.group_by(['epoch_id', 'band']).agg([
                pl.col('power').mean().alias('value')
            ]).with_columns(pl.lit(cond).alias('condition'))
            epoch_pivot = epoch_agg.pivot(on='band', index=['condition', 'epoch_id'], values='value')
        
        if region_mode:
            band_names = sorted([c for c in epoch_pivot.columns if c not in ['condition', 'epoch_id', 'region']])
            region_list = sorted(epoch_pivot['region'].unique().to_list())
            series_data, series_sems = [], []
            for band_name in band_names:
                b_means, b_sems = [], []
                for reg in region_list:
                    r_data = epoch_pivot.filter(pl.col('region') == reg)[band_name].to_numpy()
                    
                    m_val = np.nanmean(r_data) if len(r_data) > 0 else 0.0
                    s_val = np.nanstd(r_data) if len(r_data) > 0 else 0.0
                    
                    m = float(m_val)
                    s = float(s_val)
                    
                    b_means.append(m)
                    b_sems.append(s / (len(r_data) ** 0.5) if len(r_data) > 0 else 0.0)
                series_data.append(b_means)
                series_sems.append(b_sems)
            
            pl.DataFrame({
                'condition': [cond], 'x_data': [region_list], 'y_data': [series_data], 'y_var': [series_sems],
                'labels': [band_names], 'plot_type': ['line'], 'x_label': ['Region'], 'y_label': ['Power (μV²/Hz)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}_plot.parquet"), compression='gzip')
        else:
            band_names = [c for c in epoch_pivot.columns if c not in ['condition', 'epoch_id']]
            b_means, b_sems = [], []
            for b in band_names:
                r_data = epoch_pivot[b].to_numpy()
                
                m_val = np.nanmean(r_data) if len(r_data) > 0 else 0.0
                s_val = np.nanstd(r_data) if len(r_data) > 0 else 0.0
                
                m = float(m_val)
                s = float(s_val)
                
                b_means.append(m)
                b_sems.append(s / (len(r_data) ** 0.5) if len(r_data) > 0 else 0.0)
            
            pl.DataFrame({
                'condition': [cond], 'x_data': [band_names], 'y_data': [b_means], 'y_var': [b_sems],
                'plot_type': ['bar'], 'x_label': ['Frequency Band'], 'y_label': ['Power (μV²/Hz)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            }).write_parquet(os.path.join(out_folder, f"{base}_psd{idx+1}_plot.parquet"), compression='gzip')
            
    if region_mode:
        all_agg = result_df.group_by(['condition', 'epoch_id', 'region', 'band']).agg(pl.col('power').mean().alias('value'))
        all_pivot = all_agg.pivot(on='band', index=['condition', 'epoch_id', 'region'], values='value')
        all_pivot.write_parquet(os.path.join(out_folder, f"{base}_psd_all.parquet"), compression='gzip')

    signal_path = os.path.join(os.getcwd(), f"{base}_psd.parquet")
    pl.DataFrame({
        'signal_file': [os.path.basename(ip)], 
        'source': [os.path.basename(ip)], 
        'conditions': [len(conds)], 
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='gzip')
    
    print(f"[psd] Output: {signal_path}")
    return signal_path

if __name__ == '__main__':
    if len(sys.argv) < 3:
        log_error("Usage: python psd_analyzer.py <epochs.parquet> <bands_dict> [channels] [regions_dict] [y_lim]")
        sys.exit(1)
        
    input_file = sys.argv[1]
    
    bands_raw = sys.argv[2].strip("'\"").replace(' ', '')
    if not bands_raw.endswith("}"): 
        bands_raw += "}"  
    
    try:
        bands_dict = ast.literal_eval(bands_raw)
    except Exception as e:
        log_error(f"Failed to parse bands dictionary argument '{sys.argv[2]}': {e}")
        sys.exit(1)
        
    channels_arg = None
    regions_arg  = None
    ylim_arg     = None
    
    if len(sys.argv) > 3 and sys.argv[3] not in ['None', 'null', 'result']:
        try: channels_arg = ast.literal_eval(sys.argv[3])
        except Exception as e:
            log_error(f"Failed to parse channels argument: {e}")
            sys.exit(1)
        
    if len(sys.argv) > 4 and sys.argv[4] not in ['None', 'null', 'result']:
        try: regions_arg = ast.literal_eval(sys.argv[4])
        except Exception as e:
            log_error(f"Failed to parse regions argument: {e}")
            sys.exit(1)
        
    if len(sys.argv) > 5 and sys.argv[5] not in ['None', 'null', 'result']:
        try: ylim_arg = float(sys.argv[5])
        except Exception as e:
            log_error(f"Failed to parse y_lim argument: {e}")
            sys.exit(1)

    compute_psd(input_file, bands_dict, channels_arg, regions_arg, ylim_arg)