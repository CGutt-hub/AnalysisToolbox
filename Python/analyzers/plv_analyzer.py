import polars as pl, numpy as np, sys, os, ast
from scipy.signal import hilbert, butter, filtfilt
from numpy.typing import NDArray
from typing import Any

def compute_plv(stream_paths: list[str], stream_configs: list[dict[str, Any]], output_name: str, y_lim: float | None = None) -> str:
    """
    Compute PLV between arbitrary number of streams.
    
    Args:
        stream_paths: List of paths to epoched stream files
        stream_configs: List of dicts with 'type', 'channels'/'column', 'freq_band', 'sfreq' for each stream
                       type: 'continuous' (EEG, EDA) or 'event' (HRV R-peaks)
        output_name: Base name for output files
        y_lim: Optional Y-axis maximum limit for consistent scaling across participants
    """
    print(f"[plv] Loading {len(stream_paths)} streams")
    for i, (path, cfg) in enumerate(zip(stream_paths, stream_configs)):
        print(f"[plv]   Stream {i+1}: {os.path.basename(path)} ({cfg['type']})")
    
    # Load all streams
    streams = [pl.read_parquet(path) for path in stream_paths]
    
    workspace = os.getcwd()
    out_folder = os.path.join(workspace, f"{output_name}_plv")
    os.makedirs(out_folder, exist_ok=True)
    
    conditions = sorted(streams[0]['condition'].unique().to_list())
    print(f"[plv] Processing {len(conditions)} conditions: {conditions}")
    
    # Prepare filters for continuous streams
    filters = []
    for cfg in stream_configs:
        if cfg['type'] == 'continuous':
            butter_result = butter(4, cfg['freq_band'], btype='band', fs=cfg['sfreq'])
            b: NDArray[np.float64] = butter_result[0]  # type: ignore[assignment]
            a: NDArray[np.float64] = butter_result[1]  # type: ignore[assignment]
            filters.append((b, a))
        else:
            filters.append(None)
    
    # Process each condition
    for idx, cond in enumerate(conditions):
        cond_data = [df.filter(pl.col('condition') == cond) for df in streams]
        epoch_ids = sorted(cond_data[0]['epoch_id'].unique().to_list())
        
        # Determine output labels (channels or stream pairs)
        continuous_streams = [(i, cfg) for i, cfg in enumerate(stream_configs) if cfg['type'] == 'continuous']
        event_streams = [(i, cfg) for i, cfg in enumerate(stream_configs) if cfg['type'] == 'event']
        
        # Build all pairwise PLVs between streams
        plv_results = []
        
        # Continuous vs Event (e.g., EEG-HRV, EDA-HRV)
        if len(continuous_streams) > 0 and len(event_streams) > 0:
            for cont_idx, cont_cfg in continuous_streams:
                for ch in cont_cfg['channels']:
                    ch_plvs = []
                    
                    for eid in epoch_ids:
                        # Get continuous signal phase
                        signal: NDArray[np.float64] = cond_data[cont_idx].filter(pl.col('epoch_id') == eid)[ch].to_numpy()
                        b, a = filters[cont_idx]
                        filtered: NDArray[np.float64] = filtfilt(b, a, signal)  # type: ignore[assignment]
                        analytic: NDArray[np.complex128] = hilbert(filtered)  # type: ignore[assignment]
                        cont_phase: NDArray[np.floating[Any]] = np.angle(analytic)
                        
                        # Get event phase for each event stream
                        for evt_idx, evt_cfg in event_streams:
                            event_epoch = cond_data[evt_idx].filter(pl.col('epoch_id') == eid)
                            event_times: NDArray[np.float64] = event_epoch[evt_cfg['column']].to_numpy()
                            
                            # Build event phase signal
                            time_axis: NDArray[np.float64] = cond_data[cont_idx].filter(pl.col('epoch_id') == eid)['time'].to_numpy()
                            evt_phase: NDArray[np.float64] = np.zeros_like(time_axis)
                            
                            for i, t in enumerate(time_axis):
                                n_events: int = int(np.sum(event_times <= t))
                                if n_events > 0 and n_events < len(event_times):
                                    prev: np.float64 = event_times[n_events-1]
                                    nxt: np.float64 = event_times[n_events]
                                    frac: np.float64 = (t - prev) / (nxt - prev)
                                    evt_phase[i] = 2 * np.pi * (n_events + frac)
                            
                            # Calculate PLV
                            phase_diff: NDArray[np.floating[Any]] = cont_phase - evt_phase
                            plv: float = float(np.abs(np.mean(np.exp(1j * phase_diff))))
                            ch_plvs.append(plv)
                    
                    if ch_plvs:
                        label = f"{ch}-{os.path.splitext(os.path.basename(stream_paths[event_streams[0][0]]))[0]}"
                        plv_results.append({
                            'pair': label,
                            'plv_mean': float(np.mean(ch_plvs)),
                            'plv_sem': float(np.std(ch_plvs, ddof=1) / np.sqrt(len(ch_plvs)))
                        })
        
        # Continuous vs Continuous (e.g., EEG-EDA)
        if len(continuous_streams) >= 2:
            for i, (idx1, cfg1) in enumerate(continuous_streams[:-1]):
                for idx2, cfg2 in continuous_streams[i+1:]:
                    for ch1 in cfg1['channels']:
                        for ch2 in cfg2['channels']:
                            pair_plvs = []
                            
                            for eid in epoch_ids:
                                # Signal 1
                                sig1: NDArray[np.float64] = cond_data[idx1].filter(pl.col('epoch_id') == eid)[ch1].to_numpy()
                                b1, a1 = filters[idx1]
                                filt1: NDArray[np.float64] = filtfilt(b1, a1, sig1)  # type: ignore[assignment]
                                anal1: NDArray[np.complex128] = hilbert(filt1)  # type: ignore[assignment]
                                phase1: NDArray[np.floating[Any]] = np.angle(anal1)
                                
                                # Signal 2
                                sig2: NDArray[np.float64] = cond_data[idx2].filter(pl.col('epoch_id') == eid)[ch2].to_numpy()
                                b2, a2 = filters[idx2]
                                filt2: NDArray[np.float64] = filtfilt(b2, a2, sig2)  # type: ignore[assignment]
                                anal2: NDArray[np.complex128] = hilbert(filt2)  # type: ignore[assignment]
                                phase2: NDArray[np.floating[Any]] = np.angle(anal2)
                                
                                # Interpolate if different lengths due to different sampling rates
                                if len(phase1) != len(phase2):
                                    from scipy.interpolate import interp1d
                                    if len(phase2) < len(phase1):
                                        x_old = np.linspace(0, 1, len(phase2))
                                        x_new = np.linspace(0, 1, len(phase1))
                                        phase2 = interp1d(x_old, phase2, kind='linear')(x_new)
                                    else:
                                        x_old = np.linspace(0, 1, len(phase1))
                                        x_new = np.linspace(0, 1, len(phase2))
                                        phase1 = interp1d(x_old, phase1, kind='linear')(x_new)
                                
                                # PLV
                                pdiff: NDArray[np.floating[Any]] = phase1 - phase2
                                plv_val: float = float(np.abs(np.mean(np.exp(1j * pdiff))))
                                pair_plvs.append(plv_val)
                            
                            if pair_plvs:
                                plv_results.append({
                                    'pair': f"{ch1}-{ch2}",
                                    'plv_mean': float(np.mean(pair_plvs)),
                                    'plv_sem': float(np.std(pair_plvs, ddof=1) / np.sqrt(len(pair_plvs)))
                                })
        
        # Output
        if plv_results:
            result_df = pl.DataFrame(plv_results)
            output = pl.DataFrame({
                'condition': [cond],
                'x_data': [result_df['pair'].to_list()],
                'y_data': [result_df['plv_mean'].to_list()],
                'y_var': [result_df['plv_sem'].to_list()],
                'plot_type': ['bar'],
                'x_label': ['Stream Pair'],
                'y_label': ['Phase-Locking Value (PLV)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            })
            
            out_file = os.path.join(out_folder, f"{output_name}_plv{idx+1}.parquet")
            output.write_parquet(out_file)
            print(f"[plv]   {cond}: {os.path.basename(out_file)} ({len(plv_results)} pairs)")
    
    # Create procedure visualization file (aggregate all conditions)
    plot_files = [os.path.join(out_folder, f"{output_name}_plv{idx+1}.parquet") for idx in range(len(conditions))]
    if all(os.path.exists(f) for f in plot_files):
        try:
            all_plots = [pl.read_parquet(f) for f in plot_files]
            combined = pl.concat(all_plots)
            vis_path = os.path.join(workspace, f"{output_name}_plv_vis.parquet")
            combined.write_parquet(vis_path)
            print(f"[plv] Created procedure visualization: {vis_path}")
        except Exception as e:
            print(f"[plv] WARNING: Could not create procedure visualization: {e}")
    
    # Signal file
    signal_path = os.path.join(workspace, f"{output_name}_plv.parquet")
    pl.DataFrame({
        'signal': [1], 
        'source': [','.join([os.path.basename(p) for p in stream_paths])], 
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path)
    
    print(f"[plv] Finished. Signal: {os.path.basename(signal_path)}")
    return signal_path

if __name__ == '__main__': (lambda a: compute_plv(ast.literal_eval(a[1])['streams'], ast.literal_eval(a[1])['configs'], ast.literal_eval(a[1])['output_name'], float(a[2]) if len(a) > 2 and a[2] else None) if len(a) >= 2 else (print('Compute Phase Locking Value between streams. Plot-ready output.\n[plv] Usage: plv_analyzer.py <config_dict> [y_lim]'), sys.exit(1)))(sys.argv)
