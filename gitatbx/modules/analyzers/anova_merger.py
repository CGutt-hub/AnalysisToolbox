"""ANOVA Merger — combines multiple anova_analyzer outputs into one labelled table.

Input: N ANOVA parquets (each in column-major table spec from anova_analyzer.py)
Args:  <label1> <label2> ... <labelN>  [terminal]
       Labels are applied in the same order as the input files.
Output: one combined column-major table parquet with a leading 'Modality' column.
"""
import polars as pl
import sys
import os


def log_info(msg):  print(f"[anova_merger] INFO: {msg}")
def log_error(msg): print(f"[anova_merger] ERROR: {msg}"); sys.exit(1)


def merge_anovas(input_files: list, labels: list) -> str:
    if len(input_files) != len(labels):
        log_error(f"Number of input files ({len(input_files)}) must match number of labels ({len(labels)})")

    all_rows = []
    for fpath, label in zip(input_files, labels):
        if not os.path.exists(fpath):
            log_error(f"File not found: {fpath}")
        spec = pl.read_parquet(fpath)

        # Column-major layout: x_data = list of col headers, y_data = list of col value lists
        x_data = spec['x_data'][0]   # e.g. ['DV', 'F', 'df1', 'df2', 'p', 'η²', 'sig']
        y_cols = spec['y_data'][0]   # list of N_col lists, each of length N_row

        if not x_data or not y_cols:
            log_info(f"Skipping empty ANOVA result: {os.path.basename(fpath)}")
            continue

        n_rows = len(y_cols[0])
        for i in range(n_rows):
            row = {'Modality': label}
            for col_name, col_vals in zip(x_data, y_cols):
                row[col_name] = col_vals[i]
            all_rows.append(row)
        log_info(f"  {label}: {n_rows} rows from {os.path.basename(fpath)}")

    if not all_rows:
        log_error("No ANOVA rows found across all input files")

    # Rebuild as flat DataFrame, then re-encode column-major
    flat = pl.DataFrame(all_rows, infer_schema_length=len(all_rows))
    col_names = flat.columns  # ['Modality', 'DV', 'F', 'df1', 'df2', 'p', 'η²', 'sig']
    col_data  = [flat[c].to_list() for c in col_names]

    # Derive participant prefix from first input file
    base = '_'.join(os.path.splitext(os.path.basename(input_files[0]))[0].split('_')[:2])
    out_file = os.path.join(os.getcwd(), f"{base}_anova_combined.parquet")

    pl.DataFrame([{
        'x_data':    col_names,
        'y_data':    col_data,
        'y_var':     None,
        'plot_type': 'table',
        'x_label':   'Modality',
        'y_label':   'p (FDR-corrected)',
        'y_ticks':   None,
    }]).write_parquet(out_file, compression='snappy')

    log_info(f"Combined {len(all_rows)} rows from {len(input_files)} modalities → {out_file}")
    print(f"[anova_merger] Output: {out_file}")
    print(out_file)
    return out_file


if __name__ == '__main__':
    a = sys.argv[1:]
    # Input files are .parquet paths; labels are everything else (excluding 'terminal')
    files  = [x for x in a if x.endswith('.parquet')]
    labels = [x for x in a if not x.endswith('.parquet') and x.lower() != 'terminal']

    if not files or not labels:
        print('[anova_merger] Usage: anova_merger.py <f1.parquet> [f2 ...] <label1> [label2 ...] [terminal]')
        print('  Labels applied in the same order as files.')
        sys.exit(1)

    merge_anovas(files, labels)
