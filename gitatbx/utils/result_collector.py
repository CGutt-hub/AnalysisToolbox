"""Result Collector — copy a plot-ready parquet with a short, human-readable name.

Usage:
    result_collector.py <input.parquet> <clean_name>

The script reads the input parquet and writes it to the current directory
as ``{PID}_{clean_name}.parquet`` where PID is extracted from the input
filename (first two underscore-separated parts, e.g. ``EV_002``).

Sentinel files (from failed upstream processes) are passed through
unchanged so that downstream finalization is never blocked.
"""
import sys
import os
import polars as pl


def main():
    if len(sys.argv) < 3:
        print("Usage: result_collector.py <input.parquet> <clean_name>")
        sys.exit(1)

    input_path = sys.argv[1]
    clean_name = sys.argv[2]

    df = pl.read_parquet(input_path)

    # Pass through sentinel files from failed upstream processes.
    # Use pid-based name so finalize_participant's groupTuple maps it to the right participant.
    if '_sentinel' in df.columns:
        basename = os.path.basename(input_path).replace('.parquet', '')
        parts = basename.split('_')
        pid = '_'.join(parts[:2]) if len(parts) >= 2 else 'sentinel'
        df.write_parquet(f"{pid}_sentinel_{clean_name}.parquet", compression='snappy')
        return

    # Extract participant ID from input filename (pattern: EV_NNN_...)
    basename = os.path.basename(input_path).replace('.parquet', '')
    parts = basename.split('_')
    pid = '_'.join(parts[:2]) if len(parts) >= 2 else 'result'

    output_name = f"{pid}_{clean_name}_result.parquet"
    df.write_parquet(output_name, compression='snappy')
    print(f"[result_collector] {basename} -> {output_name}")


if __name__ == '__main__':
    main()
