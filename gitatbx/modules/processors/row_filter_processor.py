"""Row Filter Processor — Filter rows where a column equals a given value.

Useful for splitting region-stratified epoch data (e.g. region=Frontal vs region=Parietal)
before feeding into per-region analysis chains.

Usage:
    row_filter_processor.py <input.parquet> <column> <value> [drop_col=false]

Arguments:
    input.parquet  Input parquet file.
    column         Column name to filter on.
    value          Value to keep (equality match, string comparison).
    drop_col       If 'true', drop the filter column from the output (default: false).
"""
import polars as pl, sys, os


def log_info(msg):  print(f"[row_filter] INFO: {msg}")
def log_error(msg): print(f"[row_filter] ERROR: {msg}")


def filter_rows(ip: str, col: str, val: str, drop_col: bool = False) -> str:
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)

    df = pl.read_parquet(ip)

    if col not in df.columns:
        log_error(f"Column '{col}' not found. Available: {df.columns}"); sys.exit(1)

    before = len(df)
    filtered = df.filter(pl.col(col) == val)
    log_info(f"{ip}: kept {len(filtered)}/{before} rows where {col}={val!r}")

    if len(filtered) == 0:
        log_error(f"No rows matched {col}={val!r} — halting branch."); sys.exit(1)

    if drop_col:
        filtered = filtered.drop(col)
        log_info(f"Dropped column '{col}' from output")

    base = os.path.splitext(os.path.basename(ip))[0]
    out = f"{base}_{val.lower().replace(' ', '_')}_filter.parquet"
    filtered.write_parquet(out, compression='gzip')
    log_info(f"Output: {out}")
    return out


if __name__ == '__main__':
    (lambda a: filter_rows(
        a[1], a[2], a[3],
        len(a) > 4 and a[4].lower() in ('1', 'true', 'yes'),
    ) if len(a) >= 4 else (
        print('[row_filter] Filter rows by column equality.'),
        print('[row_filter] Usage: row_filter_processor.py <input.parquet> <column> <value> [drop_col=false]'),
        sys.exit(1),
    ))(sys.argv)
