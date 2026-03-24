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

def copy_and_output(matches, signal_file, pattern, append_suffix):
    if not matches:
        # No files found — fail so Nextflow's errorStrategy='ignore' kills this branch.
        # Downstream processes won't receive input and won't fire.
        print(f"[file_finder] ERROR: No files matching '{pattern}' — halting branch.", file=sys.stderr)
        return 1
    for f in matches:
        original_name = os.path.basename(f)
        # Append the chosen suffix to the original filename
        # e.g. EV_002_psd1.parquet + suffix=eda -> EV_002_psd1_eda.parquet
        stem = os.path.splitext(original_name)[0]
        ext = os.path.splitext(original_name)[1]
        out_name = f"{stem}_{append_suffix}{ext}"
        print(f"[file_finder] Appended suffix: {original_name} -> {out_name}", file=sys.stderr)
        shutil.copy2(f, out_name)
        print(os.path.abspath(out_name))
    return 0

def derive_suffix(pattern):
    """Derive append_suffix from the glob pattern when not explicitly provided.
    E.g. '*sam1.parquet' -> 'sam1', '*extr1.parquet' -> 'extr1', 'type:EEG.fif' -> 'found'."""
    import re as _re
    stem = os.path.splitext(os.path.basename(pattern))[0]  # strip extension
    stem = stem.lstrip('*')  # strip leading glob chars
    stem = _re.sub(r'^(type|name):', '', stem)  # strip type:/name: prefix
    return stem if stem else 'found'

if __name__ == "__main__":
    if len(sys.argv) == 3:
        # Could be "<pattern> <suffix>" or just "<pattern>"
        parts = sys.argv[2].strip().split(None, 1)
        if len(parts) == 2:
            pattern, append_suffix = parts
        else:
            pattern = parts[0]
            append_suffix = derive_suffix(pattern)
            print(f"[file_finder] No suffix provided, derived '{append_suffix}' from pattern '{pattern}'", file=sys.stderr)
    elif len(sys.argv) == 4:
        # IOInterface splits into separate args: <pattern> <suffix>
        pattern, append_suffix = sys.argv[2].strip(), sys.argv[3].strip()
    else:
        print("Usage: python file_finder.py <signal_file> <pattern> [<append_suffix>]", file=sys.stderr)
        sys.exit(1)
    matches = find_in_subdir(sys.argv[1], pattern)
    sys.exit(copy_and_output(matches, sys.argv[1], pattern, append_suffix))


