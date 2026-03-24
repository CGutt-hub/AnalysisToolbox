import polars as pl, numpy as np, sys, mne, re, os, warnings
from typing import cast

# Suppress MNE naming convention warnings
warnings.filterwarnings('ignore', message='.*does not conform to MNE naming conventions.*')

def apply_regression(ip: str, regr_type: str = 'short_channel', out: str | None = None) -> str:
    """
    Generic regression processor for physiological signal artifact removal.
    
    Supports multiple regression strategies for removing systemic/superficial contamination
    from physiological signals (fNIRS, EEG, etc.).
    
    Args:
        ip: Input file path (.fif for MNE format or .parquet for legacy)
        regr_type: Regression strategy:
            - 'short_channel': fNIRS short-separation channel regression (requires MNE-NIRS)
            - 'pca': PCA-based artifact regression (future)
            - 'glm': GLM with custom regressors (future)
            - 'none': Pass-through (no regression)
        out: Optional output path (auto-generated if None)
    
    Returns:
        Path to output file (.fif for MNE format, .parquet for legacy)
    """
    print(f"[regression] Applying {regr_type} regression: {ip}")
    
    # Handle .fif input (MNE format)
    if ip.endswith('.fif'):
        raw = mne.io.read_raw_fif(ip, preload=True, verbose=False)
        base = os.path.splitext(os.path.basename(ip))[0]
        
        # All regression types use 'regr' suffix
        suffix = 'regr'
        
        if regr_type == 'short_channel':
            # Short channel regression for fNIRS
            from mne_nirs.signal_enhancement import short_channel_regression
            short_channels = [c for c in raw.ch_names if re.search(r'(^s\d+\b)|short|_sd|_short', c, re.I)]
            
            if not short_channels:
                print(f"[regression] Warning: No short channels detected, skipping regression")
                out_file = out or f"{base}_{suffix}.fif"
                raw.save(out_file, overwrite=True, verbose=False)
                print(f"[regression] Output (MNE Raw): {out_file}")
                return out_file
            
            print(f"[regression] Applying short-channel regression ({len(short_channels)} short channels)")
            raw_corrected = short_channel_regression(raw)
            out_file = out or f"{base}_{suffix}.fif"
            raw_corrected.save(out_file, overwrite=True, verbose=False)
            print(f"[regression] Output (MNE Raw): {out_file}")
            return out_file
        
        elif regr_type == 'pca':
            # PCA-based regression (future implementation)
            print(f"[regression] PCA regression not yet implemented")
            out_file = out or f"{base}_{suffix}.fif"
            raw.save(out_file, overwrite=True, verbose=False)
            return out_file
        
        elif regr_type == 'none':
            # No regression, just pass through
            print(f"[regression] No regression applied")
            out_file = out or f"{base}_{suffix}.fif"
            raw.save(out_file, overwrite=True, verbose=False)
            print(f"[regression] Output (MNE Raw): {out_file}")
            return out_file
        
        else:
            print(f"[regression] Error: Unknown regression type '{regr_type}'")
            sys.exit(1)
    
    # Handle parquet input (legacy)
    df = pl.read_parquet(ip)
    data_cols = [c for c in df.columns if c not in ['time', 'sfreq']]
    if not data_cols: print(f"[regression] Error: No data channels found"); sys.exit(1)
    
    # All regression types use 'regr' suffix
    suffix = 'regr'
    
    if regr_type == 'short_channel':
        from mne_nirs.signal_enhancement import short_channel_regression
        short_channels = [c for c in data_cols if re.search(r'(^s\d+\b)|short|_sd|_short', c, re.I)]
        
        if not short_channels:
            print(f"[regression] Warning: No short channels detected, skipping regression")
            out_file = out or f"{ip.replace('.parquet', '')}_{suffix}.parquet"
            df.write_parquet(out_file, compression='snappy')
            return out_file
        
        data = np.array([df[col].to_numpy() for col in data_cols])
        sfreq = float(df['sfreq'][0]) if 'sfreq' in df.columns else 10.0
        print(f"[regression] Applying short-channel regression ({len(short_channels)} short channels)")
        
        info = mne.create_info(data_cols, sfreq, ch_types='fnirs_cw_amplitude')
        raw = mne.io.RawArray(data, info, verbose=False)
        raw_corrected = short_channel_regression(raw)
        
        out_file = out or f"{ip.replace('.parquet', '')}_{suffix}.fif"
        raw_corrected.save(out_file, overwrite=True, verbose=False)
        print(f"[regression] Output (MNE Raw): {out_file}")
        return out_file
    
    elif regr_type == 'none':
        print(f"[regression] No regression applied")
        out_file = out or f"{ip.replace('.parquet', '')}_{suffix}.parquet"
        df.write_parquet(out_file, compression='snappy')
        return out_file
    
    else:
        print(f"[regression] Error: Unknown regression type '{regr_type}'")
        sys.exit(1)

if __name__ == '__main__': (lambda a: apply_regression(a[1], a[2] if len(a) > 2 else 'short_channel', a[3] if len(a) > 3 else None) if len(a) >= 2 else (print("[regression] Generic regression processor for physiological signal artifact removal.\nUsage: regression_processor.py <input.fif|.parquet> [regr_type=short_channel|pca|glm|none] [output]\nExample: regression_processor.py fnirs_tddr.fif short_channel"), sys.exit(1)))(sys.argv)
