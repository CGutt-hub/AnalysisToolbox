import os, sys, fnmatch, shutil, polars as pl, re

def resolve_type_pattern(df, pattern, folder_path):
    """Resolve type: or name: prefixed patterns using signal file metadata.
    
    Pattern formats:
    - type:EEG*.fif  -> Find stream index where type=EEG, then match *xdf{index}*.fif
    - name:BrainAmp*.fif -> Find stream index where name contains BrainAmp
    - *xdf4.fif -> Regular glob pattern (unchanged behavior)
    """
    type_match = re.match(r'^type:(\w+)(.*)$', pattern)
    name_match = re.match(r'^name:(\w+)(.*)$', pattern)
    
    if not type_match and not name_match:
        return pattern  # Regular pattern, no resolution needed
    
    # Check if signal file has stream metadata
    if 'stream_types' not in df.columns or 'stream_names' not in df.columns:
        print(f"[file_finder] Warning: Signal file has no stream metadata, falling back to pattern: {pattern}", file=sys.stderr)
        return None
    
    types = df['stream_types'][0].split(',')
    names = df['stream_names'][0].split(',')
    
    if type_match:
        target_type, suffix = type_match.groups()
        # Find first stream matching the type (case-insensitive)
        for idx, t in enumerate(types):
            if t.upper() == target_type.upper():
                resolved = f"*xdf{idx+1}{suffix}"
                print(f"[file_finder] Resolved type:{target_type} -> stream {idx+1} ({t}), pattern: {resolved}", file=sys.stderr)
                return resolved
        print(f"[file_finder] Error: No stream with type '{target_type}' found. Available: {types}", file=sys.stderr)
        return None
    
    if name_match:
        target_name, suffix = name_match.groups()
        # Find first stream whose name contains the target (case-insensitive)
        for idx, n in enumerate(names):
            if target_name.upper() in n.upper():
                resolved = f"*xdf{idx+1}{suffix}"
                print(f"[file_finder] Resolved name:{target_name} -> stream {idx+1} ({n}), pattern: {resolved}", file=sys.stderr)
                return resolved
        print(f"[file_finder] Error: No stream with name containing '{target_name}' found. Available: {names}", file=sys.stderr)
        return None
    
    return pattern

def find_in_subdir(signal_file, pattern):
    # Read folder path from signal file
    try:
        df = pl.read_parquet(signal_file)
        if 'folder_path' in df.columns:
            folder_path = df['folder_path'][0]
            print(f"[file_finder] Using folder from signal: {folder_path}", file=sys.stderr)
        else:
            # Fallback: derive from signal file name
            real_path = os.path.realpath(signal_file)
            signal_dir = os.path.dirname(real_path)
            signal_name = os.path.splitext(os.path.basename(real_path))[0]
            folder_path = os.path.join(signal_dir, signal_name)
            print(f"[file_finder] No folder_path in signal, using fallback: {folder_path}", file=sys.stderr)
        
        # Resolve type: or name: prefixed patterns
        resolved_pattern = resolve_type_pattern(df, pattern, folder_path)
        if resolved_pattern is None:
            return []  # Type/name not found
        pattern = resolved_pattern
        
    except Exception as e:
        print(f"Error reading signal file: {e}", file=sys.stderr)
        return []
    
    if not os.path.isdir(folder_path):
        print(f"[file_finder] Warning: Folder not found (upstream passthrough?): {folder_path}", file=sys.stderr)
        return []
    
    matches = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if fnmatch.fnmatch(f, pattern)]
    if not matches:
        print(f"[file_finder] Warning: No files matching '{pattern}' in {folder_path}", file=sys.stderr)
    return matches

def copy_and_output(matches, signal_file, pattern):
    if not matches:
        # No files found — upstream produced an empty folder (graceful passthrough chain).
        # Emit a proper signal file with folder_path so downstream modules can still resolve it.
        base = os.path.splitext(os.path.basename(os.path.realpath(signal_file)))[0]
        suffix = re.sub(r'[*?]', '', pattern).lstrip('._')        # strip glob chars
        suffix = os.path.splitext(suffix)[0] or 'pass'
        out_folder = os.path.abspath(f"{base}_{suffix}")
        os.makedirs(out_folder, exist_ok=True)
        out_name = f"{base}_{suffix}.parquet"
        pl.DataFrame({'signal': [1], 'source': [base], 'conditions': [0],
                      'folder_path': [out_folder]}).write_parquet(out_name, compression='snappy')
        print(f"[file_finder] No matches for '{pattern}' — emitting passthrough signal: {out_name}", file=sys.stderr)
        print(os.path.abspath(out_name))
        return 0
    for f in matches: shutil.copy2(f, b := os.path.basename(f)); print(os.path.abspath(b))
    return 0

if __name__ == "__main__":
    if len(sys.argv) != 3: print("Usage: python file_finder.py <signal_file> <pattern>", file=sys.stderr); sys.exit(1)
    matches = find_in_subdir(sys.argv[1], sys.argv[2])
    sys.exit(copy_and_output(matches, sys.argv[1], sys.argv[2]))


