#!/usr/bin/env python3
"""Append text to a .log.parquet file. Creates the file if it does not exist.

The parquet schema is {'content': [str]} — one row, the full accumulated log text.
This matches the schema expected by interactive_plotter.py / the HTML viewer.

Uses a .lock sidecar file for cross-process serialization so that concurrent
IOInterface tasks for the same participant do not overwrite each other's entries.

Usage:
    log_to_parquet.py <path/to/ID.log.parquet> <text_file>
    log_to_parquet.py <path/to/ID.log.parquet> --text "literal text to append"
"""
import polars as pl, sys, os, time

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # Windows — fall back to sidecar-existence lock


def _lock_acquire(lock_path):
    """Acquire a cross-process lock. Returns a lock handle to pass to _lock_release."""
    if _HAS_FCNTL:
        fd = open(lock_path, 'w')
        for _ in range(60):  # up to ~30 s
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except (OSError, IOError):
                time.sleep(0.5)
        return fd  # proceed without lock rather than dropping data
    else:
        # Windows: spin on creating a lockdir (atomic mkdir)
        for _ in range(60):
            try:
                os.mkdir(lock_path)
                return lock_path
            except FileExistsError:
                time.sleep(0.5)
        return None  # proceed without lock


def _lock_release(handle, lock_path):
    """Release a lock previously acquired via _lock_acquire."""
    if _HAS_FCNTL:
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()
    else:
        if handle is not None:
            try:
                os.rmdir(lock_path)
            except Exception:
                pass


def append_log(log_path: str, new_text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    lock_path = log_path + '.lock'
    handle = _lock_acquire(lock_path)
    try:
        existing = ""
        if os.path.exists(log_path):
            try:
                df = pl.read_parquet(log_path)
                existing = df['content'][0] if len(df) > 0 and 'content' in df.columns else ""
            except Exception:
                pass
        pl.DataFrame({'content': [existing + new_text]}).write_parquet(log_path, compression='gzip')
    finally:
        _lock_release(handle, lock_path)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: log_to_parquet.py <log.parquet> <text_file>  OR  log_to_parquet.py <log.parquet> --text "text"')
        sys.exit(1)
    path = sys.argv[1]
    if sys.argv[2] == '--text':
        text = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        try:
            with open(sys.argv[2], 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
        except Exception as e:
            print(f"[log_to_parquet] Error reading text file: {e}")
            sys.exit(1)
    append_log(path, text)
