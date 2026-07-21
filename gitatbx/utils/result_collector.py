"""Result Collector — copy a plot-ready parquet with a short, human-readable name.

Usage:
    result_collector.py <input.parquet> <clean_name>

The script reads the input parquet and writes it to the current directory
as ``{PID}_{clean_name}.parquet`` where PID is extracted from the input
filename (first two underscore-separated parts, e.g. ``EV_002``).
"""
import sys
import os
import polars as pl


def main():
    # Strip IOInterface tokens
    _TOKENS = {'terminal', 'table', 'result', 'group_log'}
    clean_args = [arg for arg in sys.argv[1:] if arg not in _TOKENS]
    
    if len(clean_args) < 2:
        print("Usage: result_collector.py <input.parquet> <clean_name>")
        sys.exit(1)

    input_path = clean_args[0]
    clean_name = clean_args[1]

    df = pl.read_parquet(input_path)

    # Extract participant ID from input filename (pattern: EV_NNN_...)
    basename = os.path.basename(input_path).replace('.parquet', '')
    parts = basename.split('_')
    pid = '_'.join(parts[:2]) if len(parts) >= 2 else 'result'

    output_name = f"{pid}_{clean_name}_result.parquet"
    df.write_parquet(output_name, compression='gzip')
    print(f"[result_collector] {basename} -> {output_name}")


if __name__ == '__main__':
    main()
