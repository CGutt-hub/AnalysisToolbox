"""Rejection Processor - Reject samples based on amplitude, gradient, or flatline criteria."""
import polars as pl, numpy as np, sys, os, ast
import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Logging helpers
def log_info(msg): print(f"[rejection] INFO: {msg}")
def log_warning(msg): print(f"[rejection] WARNING: {msg}")
def log_error(msg): print(f"[rejection] ERROR: {msg}")

def reject_samples(ip: str, columns: list | None = None, criterion: str = 'amplitude', threshold: float = 100e-6) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    print(f"[rejection] Rejection: {ip}, criterion={criterion}, threshold={threshold}")
    df = pl.read_parquet(ip)
    columns = columns or [c for c in df.columns if c not in ['time', 'sfreq']]
    mask = np.ones(len(df), dtype=bool)
    for col in columns:
        sig = df[col].to_numpy()
        if criterion == 'amplitude': mask &= np.abs(sig) < threshold
        elif criterion == 'gradient': mask &= np.abs(np.gradient(sig)) < threshold
        elif criterion == 'flatline': mask &= np.std(sig) > threshold
        elif criterion == 'zscore': z = np.abs((sig - np.mean(sig)) / (np.std(sig) + 1e-10)); mask &= z < threshold
        else: log_error(f"Unknown criterion: {criterion}"); sys.exit(1)
    
    retained = int(np.sum(mask))
    total = len(df)
    rejection_pct = ((total - retained) / total) * 100 if total > 0 else 0
    
    print(f"[rejection] Retaining {retained} of {total} samples ({100-rejection_pct:.1f}%)")
    
    # Quality check: excessive rejection
    if rejection_pct > 50:
        log_warning(f"Rejected {rejection_pct:.1f}% of samples (>50%), check threshold or signal quality")
    elif rejection_pct > 80:
        log_error(f"Rejected {rejection_pct:.1f}% of samples (>80%), threshold may be too strict")
    elif retained < 100:
        log_warning(f"Only {retained} samples retained, may be insufficient for analysis")
    
    # Output cleaned data
    out_file = ip.replace('.parquet', '_rej.parquet')
    df_clean = df.filter(mask)
    df_clean.write_parquet(out_file, compression='snappy')
    print(f"[rejection] Output: {out_file}")
    return out_file

if __name__ == '__main__': (lambda a: reject_samples(a[1], ast.literal_eval(a[2]) if len(a) > 2 and a[2] not in ('', 'None') else None, a[3] if len(a) > 3 else 'amplitude', float(a[4]) if len(a) > 4 else 100e-6) if len(a) >= 2 else (print('[rejection] Reject samples by amplitude, gradient, or flatline threshold.\nUsage: rejection_processor.py <input.parquet> [columns] [criterion=amplitude] [threshold=100e-6]'), sys.exit(1)))(sys.argv)
