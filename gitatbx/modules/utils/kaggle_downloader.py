"""Generic Kaggle Dataset Downloader

Downloads a Kaggle dataset via kagglehub and emits one trigger parquet per
data file matching a glob pattern. Designed as a pipeline entry point that
fans each participant file out into Nextflow per-participant processing.

Usage
-----
    kaggle_downloader.py <kaggle_config.parquet> [file_pattern=*.dat]
                         [participant_prefix=PART] [path_col=file_path]
                         [n_participants=0]

Arguments
---------
    kaggle_config.parquet
        Parquet with at least a 'kaggle_id' column (dataset identifier string).
    file_pattern
        Glob/regex-style pattern for data files under the downloaded dataset root.
        Supports simple shell globs (e.g. 's*.dat', '*.csv').  Default: '*.dat'.
    participant_prefix
        String prefix for participant IDs.  The numeric part is extracted from
        the matching filename (first contiguous digit run, zero-padded to 2).
        Default: 'PART'.  Example: prefix='DEAP' + file 's01.dat' → 'DEAP_01'.
    path_col
        Column name used for the file path in each trigger parquet.
        Default: 'file_path'.
    n_participants
        Expected participant count.  If > 0, files are enumerated 1..N using
        the pattern (insert {N:02d} placeholder if present; otherwise files are
        discovered by glob and the first N are taken).
        If 0 (default), all matching files are used.

Output
------
    One  <prefix>_NN_<path_col>.parquet  per discovered file, written to cwd.
    Each trigger parquet contains:
        participant_id  (str) e.g. 'DEAP_01'
        <path_col>      (str) absolute path to the data file
    All trigger paths are printed to stdout (one per line) for Nextflow capture.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

import polars as pl


def log_info(msg):    print(f"[kaggle_dl] INFO: {msg}", file=sys.stderr)
def log_warning(msg): print(f"[kaggle_dl] WARNING: {msg}", file=sys.stderr)
def log_error(msg):   print(f"[kaggle_dl] ERROR: {msg}", file=sys.stderr)


def _find_files(dataset_root: str, file_pattern: str) -> list[str]:
    """Recursively find all files matching file_pattern under dataset_root."""
    found = []
    for dirpath, _dirs, files in os.walk(dataset_root):
        for fname in files:
            if fnmatch.fnmatch(fname, file_pattern):
                found.append(os.path.join(dirpath, fname))
    return sorted(found)


def _participant_id(filepath: str, prefix: str) -> str:
    """Derive a participant ID from the filename.

    Extracts the first contiguous run of digits from the basename,
    zero-pads to 2 characters, and prepends prefix.
    e.g.  s01.dat + 'DEAP'  →  'DEAP_01'
          subject_003.csv + 'SUB'  →  'SUB_03'
    """
    basename = os.path.splitext(os.path.basename(filepath))[0]
    digits = re.findall(r'\d+', basename)
    if not digits:
        log_warning(f"No digits found in '{basename}', using full basename as ID")
        return f"{prefix}_{basename}"
    return f"{prefix}_{int(digits[0]):02d}"


def download(
    config_path: str,
    file_pattern: str = '*.dat',
    participant_prefix: str = 'PART',
    path_col: str = 'file_path',
    n_participants: int = 0,
) -> list[str]:
    """Download dataset and emit trigger parquets.

    Returns the list of written trigger parquet paths.
    """
    try:
        import kagglehub
    except ImportError:
        log_error("kagglehub not installed. Run: pip install kagglehub")
        sys.exit(1)

    cfg = pl.read_parquet(config_path)
    if 'kaggle_id' not in cfg.columns:
        log_error(f"Config parquet missing 'kaggle_id' column. Found: {cfg.columns}")
        sys.exit(1)
    kaggle_id = str(cfg['kaggle_id'][0])
    log_info(f"Dataset: {kaggle_id}")

    dataset_root = kagglehub.dataset_download(kaggle_id)
    log_info(f"Dataset root: {dataset_root}")

    files = _find_files(dataset_root, file_pattern)
    if not files:
        log_error(f"No files matching '{file_pattern}' found under {dataset_root}")
        sys.exit(1)

    if n_participants > 0:
        files = files[:n_participants]
        if len(files) < n_participants:
            log_warning(f"Expected {n_participants} files but only found {len(files)}")

    log_info(f"Found {len(files)} file(s) matching '{file_pattern}'")

    trigger_paths = []
    for fpath in files:
        pid = _participant_id(fpath, participant_prefix)
        trigger = os.path.join(os.getcwd(), f"{pid}_{path_col}.parquet")
        pl.DataFrame({
            'participant_id': [pid],
            path_col:         [os.path.abspath(fpath)],
        }).write_parquet(trigger, compression='gzip')
        print(trigger)
        trigger_paths.append(trigger)

    log_info(f"Emitted {len(trigger_paths)} trigger parquet(s)")
    return trigger_paths


if __name__ == '__main__':
    # Strip Nextflow terminal / log tokens
    args = [a for a in sys.argv[1:] if a not in ('terminal', 'group_log', 'result')]

    if not args:
        print(
            "[kaggle_dl] Usage: kaggle_downloader.py <kaggle_config.parquet> "
            "[file_pattern=*.dat] [participant_prefix=PART] [path_col=file_path] "
            "[n_participants=0]"
        )
        sys.exit(1)

    config_path_        = args[0]
    file_pattern_       = args[1] if len(args) > 1 else '*.dat'
    participant_prefix_ = args[2] if len(args) > 2 else 'PART'
    path_col_           = args[3] if len(args) > 3 else 'file_path'
    n_participants_     = int(args[4]) if len(args) > 4 else 0

    download(config_path_, file_pattern_, participant_prefix_, path_col_, n_participants_)
