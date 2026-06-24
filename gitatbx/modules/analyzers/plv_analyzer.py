import polars as pl, numpy as np, sys, os, ast
from scipy.signal import hilbert, butter, filtfilt
from numpy.typing import NDArray
from typing import Any

def compute_plv(
    stream_paths: list[str],
    stream_configs: list[dict[str, Any]],
    output_name: str,
    y_lim: float | None = None,
    output_format: str = 'signal_pointer',
) -> str:
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
    
    # Per-epoch PLV records used for both flat-table output and descriptive plots.
    # Columns: condition, epoch_id, trial_id, pair, value
    epoch_records: list[dict[str, Any]] = []

    # Process each condition
    for idx, cond in enumerate(conditions):
        cond_data = [df.filter(pl.col('condition') == cond) for df in streams]
        epoch_ids = sorted(cond_data[0]['epoch_id'].unique().to_list())
        
        # Determine output labels (channels or stream pairs)
        continuous_streams = [(i, cfg) for i, cfg in enumerate(stream_configs) if cfg['type'] == 'continuous']
        event_streams = [(i, cfg) for i, cfg in enumerate(stream_configs) if cfg['type'] == 'event']
        
        # Build all pairwise PLVs between streams
        
        # Continuous vs Event (e.g., EEG-HRV, EDA-HRV)
        if len(continuous_streams) > 0 and len(event_streams) > 0:
            for cont_idx, cont_cfg in continuous_streams:
                for ch in cont_cfg['channels']:
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
                            event_name = os.path.splitext(os.path.basename(stream_paths[evt_idx]))[0]
                            pair_label = f"{ch}-{event_name}"
                            epoch_records.append({
                                'condition': cond,
                                'epoch_id': str(eid),
                                'trial_id': str(cond),
                                'pair': pair_label,
                                'value': plv,
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
                                pair_label = f"{ch1}-{ch2}"
                                epoch_records.append({
                                    'condition': cond,
                                    'epoch_id': str(eid),
                                    'trial_id': str(cond),
                                    'pair': pair_label,
                                    'value': plv_val,
                                })

    if not epoch_records:
        print('[plv] WARNING: no PLV records generated')

    epoch_df = pl.DataFrame(epoch_records) if epoch_records else pl.DataFrame({
        'condition': [], 'epoch_id': [], 'trial_id': [], 'pair': [], 'value': []
    })

    # Flat-table mode: one row per trial_id/epoch_id, one column per stream pair.
    # This is join-ready for downstream correlation_analyzer.
    # ALWAYS output to subfolder, signal_pointer style (for file_finder compatibility)
    if output_format == 'flat_table':
        if len(epoch_df) > 0:
            wide_df = epoch_df.pivot(
                values='value',
                index=['condition', 'epoch_id', 'trial_id'],
                columns='pair',
                aggregate_function='mean',
            ).sort(['condition', 'epoch_id'])
        else:
            wide_df = pl.DataFrame({'condition': [], 'epoch_id': [], 'trial_id': []})

        # Write flat table INTO subfolder (file_finder will extract it)
        flat_table_path = os.path.join(out_folder, f"{output_name}_plv.parquet")
        wide_df.write_parquet(flat_table_path, compression='gzip')
        print(f"[plv] Output (flat_table): {os.path.basename(flat_table_path)} ({len(wide_df)} rows) in subfolder")
        
        # Emit signal pointer at root level pointing to subfolder
        signal_path = os.path.join(workspace, f"{output_name}_plv.parquet")
        pl.DataFrame({
            'signal': [1], 
            'source': [','.join([os.path.basename(p) for p in stream_paths])], 
            'format': ['flat_table'],
            'folder_path': [os.path.abspath(out_folder)]
        }).write_parquet(signal_path, compression='gzip')
        print(f"[plv] Emitted signal pointer: {os.path.basename(signal_path)} -> {out_folder}")
        return signal_path

    # Default (signal_pointer) mode: emit per-condition plot files + signal pointer.
    # This is the same as before for backward compatibility
    signal_path = os.path.join(workspace, f"{output_name}_plv.parquet")
    if len(epoch_df) > 0:
        cond_summary = epoch_df.group_by(['condition', 'pair']).agg([
            pl.col('value').mean().alias('plv_mean'),
            pl.col('value').std(ddof=1).alias('plv_std'),
            pl.len().alias('n')
        ]).with_columns([
            (pl.col('plv_std') / pl.col('n').sqrt()).fill_null(0.0).alias('plv_sem')
        ])

        for idx, cond in enumerate(conditions):
            cond_df = cond_summary.filter(pl.col('condition') == cond)
            if len(cond_df) == 0:
                continue
            output = pl.DataFrame({
                'condition': [cond],
                'x_data': [cond_df['pair'].to_list()],
                'y_data': [cond_df['plv_mean'].to_list()],
                'y_var': [cond_df['plv_sem'].to_list()],
                'plot_type': ['bar'],
                'x_label': ['Stream Pair'],
                'y_label': ['Phase-Locking Value (PLV)'],
                'y_ticks': [y_lim] if y_lim is not None else [None]
            })

            out_file = os.path.join(out_folder, f"{output_name}_plv{idx+1}.parquet")
            output.write_parquet(out_file, compression='gzip')
            print(f"[plv]   {cond}: {os.path.basename(out_file)} ({len(cond_df)} pairs)")
    
    pl.DataFrame({
        'signal': [1], 
        'source': [','.join([os.path.basename(p) for p in stream_paths])], 
        'conditions': [len(conditions)],
        'folder_path': [os.path.abspath(out_folder)]
    }).write_parquet(signal_path, compression='gzip')
    
    print(f"[plv] Finished. Signal: {os.path.basename(signal_path)}")
    return signal_path


def _plv_main(argv: list[str]) -> None:
    """Entry point supporting two CLI formats:

    Legacy (original):
        plv_analyzer.py <config_dict>  [y_lim] [output_format]
        config_dict = {'streams': [...], 'configs': [...], 'output_name': '...'}

    IOInterface-compatible (new):
        plv_analyzer.py <file1> [file2 ...] <json_configs_list> <output_name> [y_lim] [output_format]
        json_configs_list = JSON list of per-stream config dicts (one per file, no 'streams' key)
        output_name       = base name for output files (e.g. 'DEAP_01_eeg_hrv')
    """
    a = argv
    if len(a) < 2:
        print('Compute Phase Locking Value between streams. Plot-ready output.\n'
              '[plv] Usage: plv_analyzer.py <file1> [file2 ...] <json_configs_list> <output_name> [y_lim]')
        sys.exit(1)

    if a[1].startswith('{'):
        # Legacy format
        cfg = ast.literal_eval(a[1])
        y_lim = float(a[2]) if len(a) > 2 and a[2] and a[2] not in ('None', '') else None
        output_format = next((x for x in a[2:] if x in ('signal_pointer', 'flat_table')), 'signal_pointer')
        compute_plv(cfg['streams'], cfg['configs'], cfg['output_name'], y_lim, output_format)
        return

    # IOInterface format: files come first, then the JSON configs list, then output_name
    json_idx = next((i for i, x in enumerate(a[1:], 1) if x.startswith('[')), None)
    if json_idx is None:
        print(f'[plv] ERROR: no JSON configs list found in args: {a[1:]}')
        sys.exit(1)

    stream_paths = a[1:json_idx]
    configs      = ast.literal_eval(a[json_idx])

    tail = a[json_idx + 1:]
    output_format = next((x for x in tail if x in ('signal_pointer', 'flat_table')), 'signal_pointer')
    tail_wo_format = [x for x in tail if x not in ('signal_pointer', 'flat_table')]

    if tail_wo_format and tail_wo_format[0] not in ('None', ''):
        output_name = tail_wo_format[0]
    else:
        output_name = re.sub(r'^([A-Za-z]+_[0-9]+).*', r'\1_plv', os.path.basename(stream_paths[0]))

    y_lim = None
    if len(tail_wo_format) > 1 and tail_wo_format[1] not in ('None', ''):
        y_lim = float(tail_wo_format[1])

    compute_plv(stream_paths, configs, output_name, y_lim, output_format)


if __name__ == '__main__':
    import re
    _plv_main(sys.argv)
