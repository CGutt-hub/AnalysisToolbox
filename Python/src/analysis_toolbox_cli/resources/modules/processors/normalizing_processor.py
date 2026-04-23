import polars as pl, sys, os

def normalize(ip: str, norm_type: str, cols: str) -> str:
    if not os.path.exists(ip): print(f"[normalizing] Error: File not found: {ip}"); sys.exit(1)
    print(f"[normalizing] Normalizing ({norm_type}): {ip}")
    df = pl.read_parquet(ip)
    col_list = cols.split(',')
    missing = [c for c in col_list if c not in df.columns]
    if missing: print(f"[normalizing] Error: Columns not found: {missing}"); sys.exit(1)
    for col in col_list:
        df = df.with_columns((
            (pl.col(col) - pl.col(col).mean()) / pl.col(col).std() if norm_type == 'zscore' else
            (pl.col(col) - pl.col(col).min()) / (pl.col(col).max() - pl.col(col).min()) if norm_type == 'minmax' else
            (pl.col(col) - pl.col(col).median()) / (pl.col(col).quantile(0.75) - pl.col(col).quantile(0.25)) if norm_type == 'robust' else
            pl.col(col).log() if norm_type == 'log' else pl.col(col)
        ).alias(f"{col}_norm"))
    out = f"{os.path.splitext(os.path.basename(ip))[0]}_norm.parquet"
    df.write_parquet(out, compression='snappy')
    print(f"[normalizing] Output: {out}")
    return out

if __name__ == '__main__': (lambda a: normalize(a[1], a[2], a[3]) if len(a) >= 4 else (print('[normalizing] Normalize columns using zscore, minmax, robust, or log scaling.\nUsage: normalizing_processor.py <input.parquet> <zscore|minmax|robust|log> <col1,col2,...>'), sys.exit(1)))(sys.argv)