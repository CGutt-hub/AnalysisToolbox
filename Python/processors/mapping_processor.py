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
    mapped.write_parquet(out, compression='snappy')
    print(f"[mapping] Output: {out} ({mapped.shape})")
    
    # Generate inline visualization if numeric columns exist
    numeric_cols: list[str] = [c for c in mapped.columns if mapped[c].dtype in [pl.Float64, pl.Float32, pl.Int64, pl.Int32] and c not in [key_a, key_b]]
    if numeric_cols and len(numeric_cols) > 0:
        x_data: list[int] = list(range(len(mapped)))
        y_data: list[list[float]] = [mapped[col].to_list() for col in numeric_cols[:10]]  # Limit to first 10 cols
        if len(x_data) > 10000:
            step: int = len(x_data) // 10000
            x_data = x_data[::step]
            y_data = [yd[::step] for yd in y_data]
        vis_df = pl.DataFrame({
            'x_data': [[[x_data] for _ in range(len(y_data))]],
            'y_data': [y_data],
            'plot_type': ['line'],
            'labels': [numeric_cols[:10]],
            'x_label': ['Row Index'],
            'y_label': ['Value']
        })
        vis_df.write_parquet(out.replace('.parquet', '_vis.parquet'), compression='snappy')
    
    return out

if __name__ == '__main__': (lambda a: map_join(a[1], a[2], a[3], a[4]) if len(a) >= 5 else (print('[mapping] Join two dataframes on key columns.\nUsage: mapping_processor.py <a.parquet> <b.parquet> <key_a> <key_b>'), sys.exit(1)))(sys.argv)