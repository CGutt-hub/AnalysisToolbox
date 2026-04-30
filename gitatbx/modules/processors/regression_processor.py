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
            # Match common short-channel naming conventions:
            #   - MNE-NIRS: starts with 's' followed by digits (e.g. 's1', 's2')
            #   - NIRx raw fif: 'S-D:idx-wl' where D is short detector (D8-D15)
            #     e.g. '1-8:3-0', '2-9:6-1', '3-10:9-0' — detector number 8..15
            #   - Generic: channel name contains 'short', '_sd', '_short'
            short_ch_names = [c for c in raw.ch_names if re.search(
                r'(^s\d+\b)|short|_sd|_short|-(?:8|9|1[0-5]):', c, re.I)]

            if not short_ch_names:
                print(f"[regression] Warning: No short channels detected, skipping regression")
                out_file = out or f"{base}_{suffix}.fif"
                raw.save(out_file, overwrite=True, verbose=False)
                print(f"[regression] Output (MNE Raw): {out_file}")
                return out_file

            long_ch_names = [c for c in raw.ch_names if c not in short_ch_names]
            print(f"[regression] Applying short-channel regression ({len(short_ch_names)} short, {len(long_ch_names)} long channels)")

            # Extract data arrays: channels × samples
            data = raw.get_data()  # shape (n_ch, n_times)
            ch_idx = {ch: i for i, ch in enumerate(raw.ch_names)}

            # For NIRx data, wavelength group is encoded in channel name as the last token
            # e.g. '1-8:3-0' → wl group '0'. Group short regressors by wavelength.
            def wl_group(name: str) -> str:
                m = re.search(r':(\d+)-(\d+)$', name)
                return m.group(2) if m else 'all'

            # Build per-wavelength short-channel mean regressors
            wl_short: dict[str, list[int]] = {}
            for c in short_ch_names:
                wl = wl_group(c)
                wl_short.setdefault(wl, []).append(ch_idx[c])

            # If no wavelength structure detected, use all shorts together
            if set(wl_short.keys()) == {'all'}:
                global_short_mean = data[list(wl_short['all']), :].mean(axis=0, keepdims=True)  # (1, T)

            corrected = data.copy()
            for c in long_ch_names:
                wl = wl_group(c)
                if wl in wl_short:
                    regressor = data[wl_short[wl], :].mean(axis=0)  # (T,)
                elif 'all' in wl_short:
                    regressor = data[wl_short['all'], :].mean(axis=0)
                else:
                    continue  # no matching short channels — skip

                # OLS: corrected = signal - (signal·reg / reg·reg) * reg
                idx = ch_idx[c]
                signal = data[idx]
                beta = float(np.dot(signal, regressor) / (np.dot(regressor, regressor) + 1e-12))
                corrected[idx] = signal - beta * regressor

            raw_corrected = mne.io.RawArray(corrected, raw.info, verbose=False)
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
