"""Join Processor - Generic SQL-style join on shared key columns.

Performs SQL-style joins (inner, left, right, outer) on multiple data files using a shared key column.
Follows single responsibility principle: does ONLY joins, nothing else.

Usage:
    join_processor.py <file1> <file2> ... <on_key> [join_type=inner] [output_suffix]
    
    Where:
    - <file1> <file2> ...: Input parquet files to join
    - <on_key>: Column name to join on (must exist in all files)
    - [join_type]: 'inner' (default), 'left', 'right', 'outer'
    - [output_suffix]: Base name for output file (default: 'joined')
    
Examples:
    join_processor.py EV2_01_sam.parquet EV2_01_eda.parquet trial_id
    join_processor.py EV2_01_sam.parquet EV2_01_eda.parquet trial_id inner sam_eda
"""
import polars as pl, sys, os, hashlib, re
from typing import Literal, cast

# Logging helpers
def log_info(msg):    print(f"[join] INFO: {msg}")
def log_warning(msg): print(f"[join] WARNING: {msg}")
def log_error(msg):   print(f"[join] ERROR: {msg}")

def extract_pid(filepath: str) -> str:
    """Extract participant ID from filepath (e.g. EV2_002, DEAP_01)."""
    basename = os.path.basename(filepath)
    match = re.match(r'^([A-Za-z][A-Za-z0-9]*_\d+)', basename)
    return match.group(1) if match else ''

def _normalise_keys(df: pl.DataFrame, on_key: str, src: str) -> pl.DataFrame:
    """Rename known column aliases to match join key."""
    _KEY_ALIASES: dict[str, str] = {'trial_id': 'condition'}
    cols = set(df.columns)
    alias = _KEY_ALIASES.get(on_key)
    if on_key not in cols and alias and alias in cols:
        log_info(f"{src}: renaming column '{alias}' -> '{on_key}' to match join key")
        df = df.rename({alias: on_key})
    return df

def join_files(files: list[str], on_key: str, how: Literal['inner', 'left', 'right', 'full'] = 'inner', output_suffix: str = 'joined') -> str:
    """Join multiple files on a shared key column.
    
    Args:
        files: List of parquet file paths to join (at least 2)
        on_key: Column name to join on (must exist in all files after alias normalization)
        how: Join type ('inner', 'left', 'right', 'outer')
        output_suffix: Suffix for output filename
        
    Returns:
        Output file path
    """
    if len(files) < 2:
        log_error(f"Join requires at least 2 files, got {len(files)}")
        sys.exit(1)
    
    # Extract participant ID from first file for canonical naming
    pid = extract_pid(files[0])
    
    # Load and normalize all files
    dfs = []
    for f in files:
        if not os.path.exists(f):
            log_error(f"File not found: {f}")
            sys.exit(1)
        df = _normalise_keys(pl.read_parquet(f), on_key, f)
        if on_key not in df.columns:
            log_error(f"Key column '{on_key}' not found in {f}")
            sys.exit(1)
        dfs.append(df)
    
    log_info(f"Joining {len(files)} files on '{on_key}' using {how} join")
    
    # Perform sequential joins
    result: pl.DataFrame = dfs[0]
    for i, df in enumerate(dfs[1:], 1):
        try:
            # Map join type string to Polars JoinStrategy
            # Note: Polars uses 'full' instead of 'outer' for SQL outer joins
            join_map: dict[str, Literal['inner', 'left', 'right', 'full']] = {
                'inner': 'inner',
                'left': 'left', 
                'right': 'right',
                'outer': 'full',  # Polars uses 'full' for outer join
                'full': 'full'
            }
            polars_how: Literal['inner', 'left', 'right', 'full'] = join_map.get(how, 'inner')
            # Also accept 'outer' as alias for 'full' for SQL compatibility
            result = result.join(df, on=on_key, how=polars_how)
        except Exception as e:
            log_error(f"Join failed between {files[0]} and {files[i]}: {e}")
            sys.exit(1)
    
    # Create source identifier for output filename uniqueness
    source_parts = []
    for f in files:
        basename = os.path.splitext(os.path.basename(f))[0]
        parts = basename.split('_')
        # Remove participant ID prefix (first 2 parts) to get processing identifiers
        if len(parts) > 2:
            source_parts.append('_'.join(parts[2:]))
        else:
            source_parts.append(basename)
    
    # Generate unique source identifier
    if len(set(source_parts)) > 1:
        source_hash = hashlib.md5('_'.join(sorted(source_parts)).encode()).hexdigest()[:6]
        unique_source = source_hash
    else:
        unique_source = source_parts[0] if source_parts else 'joined'
    
    # Generate canonical ATBX output filename
    if pid:
        out_basename = f"{pid}_{output_suffix}_{unique_source}.parquet"
    else:
        out_basename = f"{output_suffix}_{unique_source}.parquet"
    
    out_path = os.path.join(os.getcwd(), out_basename)
    result.write_parquet(out_path, compression='gzip')
    
    log_info(f"Join result: {result.shape} -> {out_path}")
    print(out_path)
    return out_path


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 3:
        print(f"[join] Usage: join_processor.py <file1> <file2> ... <on_key> [join_type=inner] [output_suffix]")
        print(f"[join] Example: join_processor.py EV2_01_sam.parquet EV2_01_eda.parquet trial_id inner sam_eda")
        sys.exit(1)
    
    # Strip IOInterface tokens for parameter parsing
    _TOKENS = {'terminal', 'table', 'result', 'group_log'}
    clean_args = [arg for arg in args if arg not in _TOKENS]
    
    # Parse arguments: find the split between files and parameters
    known_join_types = {'inner', 'left', 'right', 'outer'}
    
    # Check if we have explicit join_type
    has_join_type = any(jt in clean_args for jt in known_join_types)
    if has_join_type:
        # Find the first join type in the args
        join_type_pos = next((i for i, arg in enumerate(clean_args) if arg in known_join_types), None)
        if join_type_pos and join_type_pos < len(clean_args) - 1:
            files = clean_args[:join_type_pos]
            on_key = clean_args[join_type_pos + 1]
            join_type = clean_args[join_type_pos]
            output_suffix = clean_args[join_type_pos + 2] if join_type_pos + 2 < len(clean_args) else 'joined'
        else:
            files = clean_args[:-2]
            on_key = clean_args[-2]
            join_type = 'inner'
            output_suffix = clean_args[-1]
    else:
        # Pattern: files... on_key output_suffix (default inner join)
        files = clean_args[:-2]
        on_key = clean_args[-2]
        join_type = 'inner'
        output_suffix = clean_args[-1]
    
    if len(files) < 2:
        log_error(f"Need at least 2 files to join, got {len(files)}")
        sys.exit(1)
    
    # Cast join_type to proper Literal type (safe since we validated it above)
    # Map 'outer' to 'full' for Polars compatibility
    if join_type == 'outer':
        how_typed: Literal['inner', 'left', 'right', 'full'] = 'full'
    elif join_type in known_join_types:
        how_typed = cast(Literal['inner', 'left', 'right', 'full'], join_type)
    else:
        how_typed = 'inner'
    join_files(files, on_key, how_typed, output_suffix)