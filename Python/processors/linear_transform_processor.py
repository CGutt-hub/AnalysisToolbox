"""Linear Transform Processor - Apply matrix transformations to channel data.
Generic: y = A @ x. Supports custom matrices, built-in presets (mbll), and channel selection."""
import numpy as np, sys, os, mne, ast, warnings, re, polars as pl
from numpy.typing import NDArray
from typing import cast
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

# Built-in matrix presets (configurable via params)
PRESETS = {
    'mbll': {  # Modified Beer-Lambert Law - pairwise 2x2 transform
        'description': 'Unmix wavelength pairs using extinction coefficients',
        'coeffs': {760: (1.4866, 3.8437), 850: (2.5264, 1.7986)},
    }
}

def build_preset_matrix(preset: str, **kwargs) -> NDArray[np.float64]:
    """Build matrix from preset name with optional parameters."""
    if preset == 'mbll':
        ppf = kwargs.get('ppf', 6.0)
        coeffs = PRESETS['mbll']['coeffs']
        wavelengths = kwargs.get('wavelengths', (760, 850))
        E = np.array([[coeffs[w][0], coeffs[w][1]] for w in wavelengths])
        # Multiply by 1000 to convert from mM to μM (standard fNIRS unit)
        return np.linalg.pinv(E) / ppf * 1000
    raise ValueError(f"Unknown preset: {preset}")

def select_channels(ch_names: list[str], selector: str) -> list[int]:
    """Select channel indices. Selector: regex pattern, index range '0:50', or comma-list '0,2,4'."""
    if not selector or selector in ['None', 'null', '*']: return list(range(len(ch_names)))
    if ':' in selector and not any(c in selector for c in '[]{}()'): # Index range like "0:50" or "1::2" (step)
        parts = selector.split(':')
        start = int(parts[0]) if parts[0] else 0
        stop = int(parts[1]) if len(parts) > 1 and parts[1] else len(ch_names)
        step = int(parts[2]) if len(parts) > 2 and parts[2] else 1
        return list(range(start, min(stop, len(ch_names)), step))
    if selector.replace(',', '').replace(' ', '').isdigit(): # Comma-separated indices
        return [int(i.strip()) for i in selector.split(',') if i.strip().isdigit()]
    # Regex pattern
    pattern = re.compile(selector)
    return [i for i, name in enumerate(ch_names) if pattern.search(name)]

def linear_transform(data: NDArray[np.float64], matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    """Apply linear transformation y = A @ x. Pure matrix multiplication."""
    return matrix @ data

def linear_transform_process(ip: str, matrix_spec: str = '[[1,0],[0,1]]', param: str = '', channels: str = '') -> str:
    """Apply linear transform. matrix_spec: preset name ('mbll') or literal matrix [[a,b],[c,d]].
    channels: optional channel selector (regex, index range '0:50', or comma-list)."""
    if not os.path.exists(ip): print(f"[linear_transform] File not found: {ip}"); sys.exit(1)
    if not ip.endswith('.fif'): print(f"[linear_transform] Error: Requires .fif format"); sys.exit(1)
    print(f"[linear_transform] Linear transform: {ip}, matrix={matrix_spec}")
    raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
    
    # Channel selection (before transform)
    ch_indices = select_channels(raw.ch_names, channels)
    if channels and channels not in ['None', 'null', '*']:
        print(f"[linear_transform] Channel selection '{channels}': {len(ch_indices)}/{len(raw.ch_names)} channels")
        if not ch_indices:
            print(f"[linear_transform] Warning: No channels match selector, using all channels")
        else:
            raw = raw.pick(ch_indices)
    
    data = cast(NDArray[np.float64], raw.get_data())
    n_ch = len(raw.ch_names)
    
    # Build matrix from preset or parse literal
    if matrix_spec in PRESETS:
        ppf = float(param) if param else 6.0
        A = build_preset_matrix(matrix_spec, ppf=ppf)
        print(f"[linear_transform] Using preset '{matrix_spec}' with param={ppf}")
        # For pairwise presets, apply 2x2 matrix to consecutive channel pairs
        if matrix_spec == 'mbll':
            if n_ch % 2 != 0: print(f"[linear_transform] Error: Pairwise transform requires even channels (got {n_ch})"); sys.exit(1)
            out_data = np.zeros_like(data)
            for i in range(n_ch // 2):
                out_data[2*i:2*i+2, :] = linear_transform(data[2*i:2*i+2, :], A)
        else:
            out_data = linear_transform(data, A)
    else:
        A = np.array(ast.literal_eval(matrix_spec))
        out_data = linear_transform(data, A)
        print(f"[linear_transform] Custom matrix {A.shape}")
    
    raw_out = mne.io.RawArray(out_data, raw.info, verbose=False)
    out_file = f"{os.path.splitext(os.path.basename(ip))[0]}_lin.fif"
    raw_out.save(out_file, overwrite=True, verbose=False)
    # Generate inline visualization
    time_data: list[float] = raw.times.tolist()
    y_data: list[list[float]] = [out_data[i].tolist() for i in range(len(raw.ch_names))]
    if len(time_data) > 10000:
        step: int = len(time_data) // 10000
        time_data = time_data[::step]
        y_data = [yd[::step] for yd in y_data]
    vis_df = pl.DataFrame({
        'x_data': [[[time_data] for _ in range(len(raw.ch_names))]],
        'y_data': [y_data],
        'plot_type': ['line'],
        'labels': [raw.ch_names],
        'x_label': ['Time (s)'],
        'y_label': ['Concentration (µM)']
    })
    vis_df.write_parquet(out_file.replace('.fif', '_vis.parquet'))
    print(f"[linear_transform] Output: {out_file} ({n_ch} channels)")
    return out_file

if __name__ == '__main__': (lambda a: linear_transform_process(a[1], a[2] if len(a) > 2 else '[[1,0],[0,1]]', a[3] if len(a) > 3 else '', a[4] if len(a) > 4 else '') if len(a) >= 2 else (print('[linear_transform] Apply matrix transformation y=Ax with optional channel selection.\nPresets: mbll (pairwise Beer-Lambert).\nUsage: linear_transform_processor.py <input.fif> [matrix|preset] [param] [channels]\nChannels: regex pattern, index range "0:50", step "0::2", or comma-list "0,2,4"'), sys.exit(1)))(sys.argv)
