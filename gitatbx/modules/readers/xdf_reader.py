import pyxdf, polars as pl, sys, os, numpy as np, mne, warnings

# Suppress MNE naming convention warnings
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Logging helpers
def log_info(msg): print(f"[xdf_reader] INFO: {msg}")
def log_warning(msg): print(f"[xdf_reader] WARNING: {msg}")
def log_error(msg): print(f"[xdf_reader] ERROR: {msg}")

def get_ch_names(stream):
    info = stream.get('info', {})
    desc = info.get('desc')
    if not desc: return None
    desc = desc[0] if isinstance(desc, list) and desc else desc
    if not isinstance(desc, dict) or 'channels' not in desc: return None
    channels = desc['channels']
    if isinstance(channels, list) and channels: channels = channels[0]
    if not isinstance(channels, dict) or 'channel' not in channels: return None
    ch_list = channels['channel']
    if not ch_list: return None
    if isinstance(ch_list, list):
        return [ch.get('label', [f'col{i}'])[0] if isinstance(ch.get('label'), list) else ch.get('label', f'col{i}') for i, ch in enumerate(ch_list)]
    return [ch_list.get('label', ['col0'])[0] if isinstance(ch_list.get('label'), list) else ch_list.get('label', 'col0')]

def get_stream_type(stream):
    info = stream.get('info', {})
    return info.get('type', [None])[0] if isinstance(info.get('type'), list) else info.get('type', None)

def get_stream_name(stream):
    info = stream.get('info', {})
    name = info.get('name', [None])[0] if isinstance(info.get('name'), list) else info.get('name', None)
    # Sanitize name for filename use (remove special chars)
    if name:
        return ''.join(c if c.isalnum() or c in '-_' else '' for c in name)
    return None

make_df = lambda s: (lambda ts, data, names: pl.DataFrame({'time': ts, **{names[j]: data[:, j] for j in range(len(names))}}) if names and len(ts) > 0 else pl.DataFrame({'time': ts, **{f'column_{j}': data[:, j] for j in range(data.shape[1] if hasattr(data, 'shape') else (len(data[0]) if len(data) > 0 else 0))}}) if len(ts) > 0 else pl.DataFrame({'time': [], 'empty': []}))(s.get('time_stamps', []), np.array(s.get('time_series', [])), get_ch_names(s))

def save_as_mne(stream, out_path, stream_type):
    ts = stream.get('time_stamps', [])
    data = np.array(stream.get('time_series', []))
    ch_names = get_ch_names(stream)
    
    if len(ts) == 0 or data.shape[0] == 0 or not ch_names or len(ch_names) == 0:
        info = mne.create_info(['empty'], 1.0, ch_types='misc')
        raw = mne.io.RawArray(np.array([[0.0]]), info, verbose=False)
        raw.save(out_path, overwrite=True, verbose=False)
        log_warning(f"Stream has no data or channels (samples={len(ts)}, channels={len(ch_names) if ch_names else 0})")
        return False
    
    ch_type = 'fnirs_cw_amplitude' if stream_type in ['NIRS', 'fNIRS'] else ('eeg' if stream_type == 'EEG' else 'misc')
    sfreq = float(stream.get('info', {}).get('nominal_srate', [1.0])[0] if isinstance(stream.get('info', {}).get('nominal_srate'), list) else stream.get('info', {}).get('nominal_srate', 1.0))
    info = mne.create_info(ch_names, sfreq, ch_types=ch_type)
    raw = mne.io.RawArray(data.T, info, verbose=False)
    raw.save(out_path, overwrite=True, verbose=False)
    return True

def read_xdf(ip):
    print(f"[xdf_reader] Loading: {ip}")
    print(f"[xdf_reader] File size: {os.path.getsize(ip) / (1024*1024):.1f} MB - this may take a while...")
    import time
    t0 = time.time()
    streams = pyxdf.load_xdf(ip)[0]
    load_time = time.time() - t0
    print(f"[xdf_reader] Loaded {len(streams)} streams in {load_time:.1f}s")
    
    # Quality check: no streams or very long load time
    if len(streams) == 0:
        log_warning("No streams found in XDF file")
    elif load_time > 60:
        log_info(f"Long load time ({load_time:.1f}s), large file or many streams")
    base = os.path.splitext(os.path.basename(ip))[0]
    workspace_root = os.getcwd()
    out_folder = os.path.join(workspace_root, f"{base}_xdf")
    os.makedirs(out_folder, exist_ok=True)
    
    # Collect stream info for signal file
    stream_info = []
    
    for i, s in enumerate(streams):
        stream_type = get_stream_type(s) or 'Unknown'
        stream_name = get_stream_name(s) or 'stream'
        n_samples = len(s.get('time_stamps', []))
        n_channels = len(get_ch_names(s) or [])
        
        # Quality check: very short streams
        if n_samples > 0 and n_samples < 10:
            log_warning(f"Stream {i+1} ({stream_type}): Only {n_samples} samples, may be incomplete")
        
        # Keep numbered filenames for consistent module order
        fif_path = os.path.join(out_folder, f"{base}_xdf{i+1}.fif")
        parquet_path = os.path.join(out_folder, f"{base}_xdf{i+1}.parquet")
        # Save as MNE .fif (always attempt)
        success = save_as_mne(s, fif_path, stream_type)
        if success:
            ch_names = get_ch_names(s)
            n_samples = len(s.get('time_stamps', []))
            n_channels = len(ch_names) if ch_names else 0
            print(f"[xdf_reader] Stream {i+1}/{len(streams)} ({stream_type}): {n_samples} samples, {n_channels} channels -> .fif")
        else:
            print(f"[xdf_reader] Warning: Stream {i+1}/{len(streams)} ({stream_type}) empty or not suitable for .fif, skipped")
        # Save as parquet (always attempt)
        df = make_df(s)
        df.write_parquet(parquet_path, compression='snappy')
        print(f"[xdf_reader] Stream {i+1}/{len(streams)} ({stream_type}): {df.shape} -> .parquet")
        
        # Record stream info
        stream_info.append({
            'index': i+1,
            'type': stream_type,
            'name': stream_name,
            'samples': len(s.get('time_stamps', [])),
            'fif': os.path.basename(fif_path),
            'parquet': os.path.basename(parquet_path)
        })
    
    # Write signal file with stream mapping
    signal_path = os.path.join(workspace_root, f"{base}_xdf.parquet")
    signal_df = pl.DataFrame({
        'signal': [1], 
        'source': [os.path.basename(ip)], 
        'streams': [len(streams)], 
        'folder_path': [os.path.abspath(out_folder)],
        'stream_types': [','.join(s['type'] for s in stream_info)],
        'stream_names': [','.join(s['name'] for s in stream_info)]
    })
    signal_df.write_parquet(signal_path, compression='snappy')
    print(f"[xdf_reader] Output: {signal_path}")

if __name__ == '__main__': (lambda a: read_xdf(a[1]) if len(a) == 2 else (print("[xdf_reader] Usage: python xdf_reader.py <input.xdf>"), sys.exit(1)))(sys.argv)

