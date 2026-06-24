import polars as pl, sys, os

def map_join(ip_a: str, ip_b: str, key_a: str, key_b: str) -> str:
    if not os.path.exists(ip_a): print(f"[mapping] Error: File not found: {ip_a}"); sys.exit(1)
    if not os.path.exists(ip_b): print(f"[mapping] Error: File not found: {ip_b}"); sys.exit(1)
    print(f"[mapping] Mapping: {ip_a} + {ip_b}")
    df_a, df_b = pl.read_parquet(ip_a), pl.read_parquet(ip_b)
    if key_a not in df_a.columns: print(f"[mapping] Error: Key '{key_a}' not in {ip_a}"); sys.exit(1)
    if key_b not in df_b.columns: print(f"[mapping] Error: Key '{key_b}' not in {ip_b}"); sys.exit(1)
    mapped = df_a.join(df_b, left_on=key_a, right_on=key_b, how='inner')
    out = f"{os.path.splitext(os.path.basename(ip_a))[0]}_mapping.parquet"
    mapped.write_parquet(out, compression='gzip')
    print(f"[mapping] Output: {out} ({mapped.shape})")
    return out

if __name__ == '__main__': (lambda a: map_join(a[1], a[2], a[3], a[4]) if len(a) >= 5 else (print('[mapping] Join two dataframes on key columns.\nUsage: mapping_processor.py <a.parquet> <b.parquet> <key_a> <key_b>'), sys.exit(1)))(sys.argv)