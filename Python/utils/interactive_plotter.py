"""Interactive Plotter - Generate unified HTML archive with file tree navigation.

This plotter creates a single HTML file per participant containing all procedure plots
with a collapsible file tree for navigation.
"""
import polars as pl
import sys
import os
import json
import time
from typing import Any, IO

# Import shared helpers from plotter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# File locking: fcntl on Linux/macOS, msvcrt on Windows
if os.name == 'nt':
    import msvcrt
    def acquire_file_lock(f: IO[Any], timeout: float = 120) -> None:
        start = time.time()
        while time.time() - start < timeout:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError("Could not acquire file lock")
    def release_file_lock(f: IO[Any]) -> None:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl as _fcntl
    def acquire_file_lock(f: IO[Any], timeout: float = 120) -> None:
        start = time.time()
        while time.time() - start < timeout:
            try:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return
            except IOError:
                time.sleep(0.1)
        raise TimeoutError("Could not acquire file lock")
    def release_file_lock(f: IO[Any]) -> None:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)

def parse_filename_to_tree_path(filename, participant_id):
    """Parse filename to tree path with participant folder.

    Examples:
        EV_002_xdf4_extr1_filt, EV_002 -> ['EV_002', 'xdf4_extr1_filt']
        EV_003_xdf3_log_tddr, EV_003   -> ['EV_003', 'xdf3_log_tddr']
    """
    # Remove participant ID, extension, and _vis suffix
    base = os.path.basename(filename).replace('.html', '').replace('_vis', '')
    parts = base.split('_')
    if len(parts) >= 2:
        # Skip participant ID (e.g., EV_002)
        base = '_'.join(parts[2:])

    # Return path with participant folder + filename
    return [participant_id, base]

def _meta_filename(archive_path):
    """Derive the meta.json filename from the archive HTML path.
    EV_procedure_results.html  →  EV_procedure_meta.json
    """
    base = os.path.basename(os.path.abspath(archive_path))
    return base.replace('_results.html', '_meta.json') if base.endswith('_results.html') else 'meta.json'

def _write_launcher_sidecar(archive_path):
    """Write a shell launcher and serve.py into a .bin/ subfolder next to the HTML.
    .bin/ is given the Windows hidden attribute so the root stays clean.
    Always overwrites so the launcher stays up to date.
    """
    html_name = os.path.basename(archive_path)
    archive_dir = os.path.dirname(os.path.abspath(archive_path))
    # If the HTML already lives inside .bin/, use that folder directly
    if os.path.basename(archive_dir) == '.bin':
        bin_dir = archive_dir
    else:
        bin_dir = os.path.join(archive_dir, '.bin')
    os.makedirs(bin_dir, exist_ok=True)
    # Hide the folder on Windows
    try:
        import subprocess
        subprocess.run(['attrib', '+h', bin_dir], check=False, capture_output=True)
    except Exception:
        pass

    base = html_name[:-len('_results.html')] if html_name.endswith('_results.html') else html_name.replace('.html', '')
    serve_name = base + '_results_serve.py'
    serve_path = os.path.join(bin_dir, serve_name)

    serve_py = f'''\
#!/usr/bin/env python3
# Auto-generated sidecar server for {html_name}.
# Run: python3 .bin/{serve_name}   (or double-click {base}_results.sh)
from __future__ import annotations
from collections.abc import Callable
from typing import Any
import os, json, http.server, socketserver

PAGE   = '.bin/{html_name}'
PORT   = 8080
FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # results root, not .bin

_parquet_to_json: Callable[[str], list[dict[str, Any]]] | None = None

try:
    import polars as pl
    def _polars_loader(path: str) -> list[dict[str, Any]]:
        return pl.read_parquet(path).to_dicts()
    _parquet_to_json = _polars_loader
except ImportError:
    try:
        import pyarrow.parquet as pq
        def _pyarrow_loader(path: str) -> list[dict[str, Any]]:
            return pq.read_table(path).to_pydict()
        _parquet_to_json = _pyarrow_loader
    except ImportError:
        pass

class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FOLDER, **kw)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.endswith('.parquet') and _parquet_to_json:
            fpath = os.path.join(FOLDER, path.lstrip('/').replace('/', os.sep))
            if os.path.isfile(fpath):
                try:
                    data = _parquet_to_json(fpath)
                    body = json.dumps(data).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except Exception as e:
                    print(f'[serve] parquet error {{fpath}}: {{e}}')
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

if __name__ == '__main__':
    import os; os.chdir(FOLDER)
    print(f'Serving  {{FOLDER}}')
    print(f'Open     http://localhost:{{PORT}}/{{PAGE}}')
    print('Stop     Ctrl+C')
    try:
        with socketserver.TCPServer(('', PORT), _Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print('\\nStopped.')
    except OSError as exc:
        if getattr(exc, 'errno', None) in (98, 10048) or 'address' in str(exc).lower():
            print(f'Port {{PORT}} busy -- a server is already running.')
            print(f'Open http://localhost:{{PORT}}/{{PAGE}} in your browser.')
        else:
            raise
'''

    sh = (
        '#!/bin/bash\n'
        'cd "$(dirname "$0")"\n'
        f'python3 ".bin/{serve_name}" > ".bin/{base}_results_serve.log" 2>&1 &\n'
        'SERVER_PID=$!\n'
        'sleep 1\n'
        f'URL="http://localhost:8080/.bin/{html_name}"\n'
        'if command -v powershell.exe &>/dev/null; then\n'
        '    powershell.exe -c "Start-Process \'$URL\'"\n'
        'elif command -v xdg-open &>/dev/null; then\n'
        '    xdg-open "$URL"\n'
        'elif command -v open &>/dev/null; then\n'
        '    open "$URL"\n'
        'fi\n'
        'disown $SERVER_PID\n'
        'exit 0\n'
    )

    # .sh launcher goes at the results ROOT (parent of .bin/), not inside .bin/
    root_dir = os.path.dirname(bin_dir)
    sh_path  = os.path.join(root_dir, f'{base}_results.sh')

    try:
        with open(serve_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(serve_py)
        with open(sh_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(sh)
        # Make .sh executable — try os.chmod first (works on Linux/NFS/WSL2+NTFS),
        # then fall back to git update-index so the file is committed as mode 100755
        # and restored as executable on the next git checkout even on plain NTFS.
        try:
            os.chmod(sh_path, 0o755)
        except Exception:
            pass
        try:
            import subprocess as _sp
            if not (os.stat(sh_path).st_mode & 0o111):
                # chmod didn't stick (plain NTFS without metadata mount); mark in git index
                _gr = _sp.run(['git', 'rev-parse', '--show-toplevel'],
                              capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.abspath(sh_path)))
                if _gr.returncode == 0:
                    _git_root = _gr.stdout.strip()
                    _rel = os.path.relpath(sh_path, _git_root).replace('\\', '/')
                    _sp.run(['git', 'update-index', '--chmod=+x', _rel],
                            capture_output=True, cwd=_git_root)
        except Exception:
            pass
        # Remove any stale files that old generator versions may have left
        for stale_name in [serve_name, f'{base}_results.bat', f'{base}_results.desktop']:
            for stale_dir in [bin_dir, root_dir]:
                stale = os.path.join(stale_dir, stale_name)
                if os.path.isfile(stale):
                    try:
                        os.remove(stale)
                    except Exception:
                        pass
    except Exception:
        pass  # non-fatal

def load_or_create_archive(archive_path):
    """Load existing archive metadata from {project}_meta.json at the archive root."""
    archive_dir = os.path.dirname(os.path.abspath(archive_path))
    meta_path = os.path.join(archive_dir, _meta_filename(archive_path))
    if os.path.exists(meta_path):
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def create_archive_html(project_name='procedure'):
    """Create a static HTML archive shell. plotMeta is fetched from {project_name}_meta.json
    at startup and polled every 10 s — never baked into the HTML.
    Sidecars are .parquet fetched on demand by the browser.
    """
    return f"""<!DOCTYPE html>
<html data-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>{project_name} - Analysis Archive</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
    <style>
        :root {{
            --bg-primary:    #0f0f0f;
            --bg-secondary:  #161616;
            --bg-tertiary:   #1c1c1c;
            --bg-elevated:   #242424;
            --text-primary:  #e8e8e8;
            --text-secondary:#a0a0a0;
            --text-muted:    #6a6a6a;
            --accent-primary:#c9a227;
            --accent-hover:  #ddb52f;
            --accent-subtle: rgba(201,162,39,0.12);
            --border-primary:#2a2a2a;
            --border-subtle: #1f1f1f;
            --font-mono: 'JetBrains Mono','Fira Code','SF Mono','Cascadia Code','Consolas',monospace;
            --transition-fast: 150ms ease;
            --transition-normal: 250ms ease;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.4);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.5);
            --code-bg:   #0a0a0a;
            --code-text: #e6b450;
            /* log colours */
            --log-error:   #f48771;
            --log-warning: #dcdcaa;
            --log-info:    #4fc1ff;
            /* proc-node colours */
            --node-ok-bg:     rgba(39,174,96,0.15);
            --node-ok-fg:     #2ecc71;
            --node-ok-border: rgba(39,174,96,0.35);
            --node-grp-bg:    rgba(201,162,39,0.12);
            --node-grp-fg:    var(--accent-primary);
            --node-grp-border:rgba(201,162,39,0.3);
            --node-miss-bg:   rgba(255,255,255,0.04);
            --node-miss-fg:   var(--text-muted);
            --node-miss-border:var(--border-primary);
        }}
        html[data-theme="light"] {{
            --bg-primary:    #ffffff;
            --bg-secondary:  #f8f8f8;
            --bg-tertiary:   #f0f0f0;
            --bg-elevated:   #e8e8e8;
            --text-primary:  #1a1a1a;
            --text-secondary:#5a5a5a;
            --text-muted:    #9a9a9a;
            --accent-primary:#9a7d1c;
            --accent-hover:  #b89322;
            --accent-subtle: rgba(154,125,28,0.1);
            --border-primary:#e0e0e0;
            --border-subtle: #eeeeee;
            --code-bg:   #f5f5f5;
            --code-text: #9a7d1c;
            --log-error:   #c0392b;
            --log-warning: #a04000;
            --log-info:    #1a5276;
            --node-ok-bg:     rgba(39,174,96,0.1);
            --node-ok-fg:     #1e8449;
            --node-ok-border: rgba(39,174,96,0.3);
            --node-grp-bg:    rgba(154,125,28,0.08);
            --node-grp-fg:    var(--accent-primary);
            --node-grp-border:rgba(154,125,28,0.25);
            --node-miss-bg:   rgba(0,0,0,0.04);
            --node-miss-fg:   var(--text-muted);
            --node-miss-border:var(--border-primary);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{
            font-family: var(--font-mono);
            background: var(--bg-primary);
            color: var(--text-primary);
            display: flex;
            height: 100vh;
            overflow: hidden;
            transition: background var(--transition-normal), color var(--transition-normal);
        }}
        /* ── Header ──────────────────────────────────────────────────────────── */
        #header {{
            position: fixed; top: 0; left: 0; right: 0; height: 50px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-primary);
            display: flex; align-items: center; padding: 0 20px; gap: 12px;
            z-index: 1000;
            box-shadow: var(--shadow-sm);
        }}
        #header h1 {{
            font-size: 15px; font-weight: 600; letter-spacing: 0.03em;
            color: var(--text-primary); flex: 1;
        }}
        #header h1 span {{ color: var(--accent-primary); }}
        #theme-toggle {{
            background: none; border: 1px solid var(--border-primary);
            border-radius: 6px; color: var(--text-secondary); cursor: pointer;
            padding: 5px 10px; font-size: 13px; font-family: var(--font-mono);
            transition: border-color var(--transition-fast), color var(--transition-fast);
        }}
        #theme-toggle:hover {{ border-color: var(--accent-primary); color: var(--accent-primary); }}
        /* ── File:// banner ──────────────────────────────────────────────────── */
        #file-protocol-banner {{
            display: none; position: fixed; top: 50px; left: 0; right: 0;
            z-index: 1500; background: var(--bg-elevated);
            border-bottom: 1px solid var(--accent-primary);
            color: var(--text-primary); align-items: center;
            padding: 0 16px; gap: 12px; font-size: 12px; height: 40px;
        }}
        #file-protocol-banner .fpb-badge {{
            background: var(--accent-subtle); color: var(--accent-primary);
            border: 1px solid var(--accent-primary); border-radius: 4px;
            padding: 2px 8px; font-size: 11px; font-weight: 600; white-space: nowrap;
        }}
        #file-protocol-banner code {{
            background: var(--code-bg); color: var(--code-text);
            padding: 2px 8px; border-radius: 4px; font-size: 11px;
            font-family: var(--font-mono); border: 1px solid var(--border-primary);
        }}
        #file-protocol-banner .fpb-copy {{
            padding: 3px 10px; border: 1px solid var(--accent-primary);
            border-radius: 4px; background: var(--accent-subtle);
            color: var(--accent-primary); font-weight: 600; cursor: pointer;
            font-size: 11px; font-family: var(--font-mono);
            transition: background var(--transition-fast);
        }}
        #file-protocol-banner .fpb-copy:hover {{ background: var(--accent-primary); color: var(--bg-primary); }}
        #file-protocol-banner .fpb-note {{ margin-left: auto; font-size: 11px; color: var(--text-muted); }}
        /* ── Search box ──────────────────────────────────────────────────────── */
        #search-box {{
            position: fixed; top: 50px; left: 0; width: 280px;
            padding: 10px 12px;
            background: var(--bg-secondary); border-bottom: 1px solid var(--border-primary);
            border-right: 1px solid var(--border-primary); z-index: 999;
        }}
        #search-input {{
            width: 100%; padding: 7px 10px;
            background: var(--bg-tertiary); border: 1px solid var(--border-primary);
            border-radius: 6px; font-size: 12px; font-family: var(--font-mono);
            color: var(--text-primary); outline: none;
            transition: border-color var(--transition-fast);
        }}
        #search-input::placeholder {{ color: var(--text-muted); }}
        #search-input:focus {{ border-color: var(--accent-primary); }}
        /* ── Sidebar ─────────────────────────────────────────────────────────── */
        #sidebar {{
            position: fixed; left: 0; top: 97px; bottom: 0; width: 280px;
            background: var(--bg-secondary); border-right: 1px solid var(--border-primary);
            overflow-y: auto; padding: 12px 10px;
        }}
        #sidebar::-webkit-scrollbar {{ width: 4px; }}
        #sidebar::-webkit-scrollbar-track {{ background: transparent; }}
        #sidebar::-webkit-scrollbar-thumb {{ background: var(--border-primary); border-radius: 2px; }}
        /* ── Sidebar tabs ────────────────────────────────────────────────────── */
        .sidebar-tabs {{
            display: flex; border-bottom: 1px solid var(--border-primary); margin-bottom: 10px;
        }}
        .sidebar-tab {{
            flex: 1; padding: 6px 4px; text-align: center; cursor: pointer;
            font-size: 11px; font-weight: 600; color: var(--text-muted);
            background: none; border: none; border-bottom: 2px solid transparent;
            transition: all var(--transition-fast); font-family: var(--font-mono);
        }}
        .sidebar-tab.active {{ color: var(--accent-primary); border-bottom-color: var(--accent-primary); }}
        /* ── Flat list tree ──────────────────────────────────────────────────── */
        .tree-folder {{
            padding: 6px 10px; cursor: pointer; user-select: none;
            color: var(--text-secondary); font-weight: 600; font-size: 12px;
            border-radius: 4px; margin: 2px 0; display: flex; align-items: center; gap: 6px;
            transition: background var(--transition-fast), color var(--transition-fast);
        }}
        .tree-folder:hover {{ background: var(--bg-tertiary); color: var(--text-primary); }}
        .tree-folder-content {{ margin-left: 10px; display: none; }}
        .tree-folder-content.expanded {{ display: block; }}
        .tree-folder-icon {{ display: inline-block; width: 14px; font-size: 10px; transition: transform 0.2s; }}
        .tree-folder-icon.expanded {{ transform: rotate(90deg); }}
        .tree-item {{
            padding: 5px 10px; cursor: pointer; user-select: none;
            color: var(--text-secondary); border-radius: 4px; margin: 1px 0;
            font-size: 12px; transition: background var(--transition-fast), color var(--transition-fast);
        }}
        .tree-item:hover {{ background: var(--bg-tertiary); color: var(--text-primary); }}
        .tree-item.active {{
            background: var(--accent-subtle); color: var(--accent-primary);
            border-left: 2px solid var(--accent-primary); font-weight: 600;
        }}
        /* ── Proc tree ───────────────────────────────────────────────────────── */
        .proc-group {{ margin-bottom: 6px; }}
        .proc-group-hdr {{
            padding: 6px 10px; font-size: 11px; font-weight: 700;
            color: var(--text-secondary); background: var(--bg-tertiary);
            border: 1px solid var(--border-primary); border-radius: 4px;
            cursor: pointer; display: flex; align-items: center; gap: 8px;
            user-select: none; letter-spacing: 0.05em; text-transform: uppercase;
            transition: background var(--transition-fast), color var(--transition-fast);
        }}
        .proc-group-hdr:hover {{ background: var(--bg-elevated); color: var(--text-primary); }}
        .proc-group-body {{ padding: 4px 0 2px 8px; }}
        .proc-pid-row {{ margin-bottom: 5px; }}
        .proc-pid-label {{
            font-size: 9px; font-weight: 700; text-transform: uppercase;
            color: var(--text-muted); letter-spacing: 0.06em; margin-bottom: 2px;
        }}
        .proc-chain {{ display: flex; flex-wrap: wrap; align-items: center; gap: 3px; }}
        .proc-node {{
            display: inline-flex; align-items: center; padding: 3px 8px;
            border-radius: 10px; font-size: 10px; border: 1px solid transparent;
            transition: all var(--transition-fast); white-space: nowrap; font-weight: 500;
        }}
        .proc-node.ok {{
            background: var(--node-ok-bg); color: var(--node-ok-fg);
            border-color: var(--node-ok-border); cursor: pointer;
        }}
        .proc-node.ok:hover {{ filter: brightness(1.2); }}
        .proc-node.ok.active {{
            background: var(--accent-primary); color: var(--bg-primary);
            border-color: var(--accent-primary); font-weight: 700;
        }}
        .proc-node.missing {{
            background: var(--node-miss-bg); color: var(--node-miss-fg);
            border-color: var(--node-miss-border); font-style: italic;
        }}
        .proc-node.group-ok {{
            background: var(--node-grp-bg); color: var(--node-grp-fg);
            border-color: var(--node-grp-border); cursor: pointer;
        }}
        .proc-node.group-ok:hover {{ filter: brightness(1.2); }}
        .proc-node.group-ok.active {{
            background: var(--accent-primary); color: var(--bg-primary);
            border-color: var(--accent-primary); font-weight: 700;
        }}
        .proc-node.group-missing {{
            background: var(--node-miss-bg); color: var(--node-miss-fg);
            border-color: var(--node-miss-border); font-style: italic;
        }}
        .proc-arrow {{ color: var(--text-muted); font-size: 12px; line-height: 1; }}
        .proc-group-divider {{ border: none; border-top: 1px solid var(--border-primary); margin: 5px 0; }}
        /* ── Main content ────────────────────────────────────────────────────── */
        #content {{
            position: fixed; left: 280px; top: 50px; right: 0; bottom: 0;
            padding: 28px 32px; overflow-y: auto; background: var(--bg-primary);
        }}
        #content::-webkit-scrollbar {{ width: 6px; }}
        #content::-webkit-scrollbar-track {{ background: transparent; }}
        #content::-webkit-scrollbar-thumb {{ background: var(--border-primary); border-radius: 3px; }}
        .plot-title {{
            font-size: 20px; font-weight: 600; color: var(--text-primary);
            margin-bottom: 14px; letter-spacing: 0.02em; border-left: 3px solid var(--accent-primary);
            padding-left: 12px;
        }}
        .empty-state {{
            text-align: center; padding: 120px 20px; color: var(--text-muted);
            animation: fadeIn 0.4s ease;
        }}
        .empty-state h2 {{ font-size: 18px; margin-bottom: 8px; color: var(--text-secondary); font-weight: 500; }}
        .empty-state p {{ font-size: 13px; }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(8px); }}
            to   {{ opacity: 1; transform: translateY(0); }}
        }}
        /* ── Export bar ──────────────────────────────────────────────────────── */
        .export-bar {{
            display: flex; gap: 8px; margin-bottom: 14px;
            align-items: center; flex-wrap: wrap;
        }}
        .export-btn {{
            padding: 5px 14px; border: 1px solid var(--border-primary);
            border-radius: 5px; cursor: pointer; font-size: 11px; font-weight: 600;
            font-family: var(--font-mono); background: var(--bg-secondary);
            color: var(--text-secondary); transition: all var(--transition-fast);
        }}
        .export-btn:hover {{ border-color: var(--accent-primary); color: var(--accent-primary); }}
        .export-btn.png:hover {{ border-color: #2ecc71; color: #2ecc71; }}
        .export-btn.svg:hover {{ border-color: #3498db; color: #3498db; }}
        .export-btn.pdf:hover {{ border-color: #9b59b6; color: #9b59b6; }}
        .export-size {{
            font-size: 11px; color: var(--text-muted);
            display: flex; gap: 6px; align-items: center;
        }}
        .export-size select {{
            font-size: 11px; padding: 4px 6px; border-radius: 5px;
            border: 1px solid var(--border-primary); background: var(--bg-secondary);
            color: var(--text-primary); font-family: var(--font-mono); outline: none;
        }}
        /* ── Plot container ──────────────────────────────────────────────────── */
        .plot-container {{
            width: 100%; height: calc(100vh - 200px);
            background: var(--bg-secondary); border: 1px solid var(--border-primary);
            border-radius: 8px; overflow: hidden; animation: fadeIn 0.3s ease;
        }}
        /* ── Log viewer ──────────────────────────────────────────────────────── */
        .log-stats {{
            display: flex; flex-wrap: wrap; gap: 16px;
            margin-bottom: 14px; padding: 10px 14px;
            background: var(--bg-secondary); border: 1px solid var(--border-primary);
            border-radius: 6px; font-size: 12px; color: var(--text-secondary);
        }}
        .log-stats .error-count   {{ color: var(--log-error);   font-weight: 600; }}
        .log-stats .warning-count {{ color: var(--log-warning); font-weight: 600; }}
        .log-stats .info-count    {{ color: var(--log-info);    font-weight: 600; }}
        .log-toggle {{
            margin-bottom: 12px; padding: 7px 16px;
            background: var(--accent-subtle); color: var(--accent-primary);
            border: 1px solid var(--accent-primary); border-radius: 5px;
            cursor: pointer; font-size: 12px; font-weight: 600;
            font-family: var(--font-mono); transition: background var(--transition-fast);
        }}
        .log-toggle:hover {{ background: var(--accent-primary); color: var(--bg-primary); }}
        .log-container {{
            width: 100%; height: calc(100vh - 220px);
            font-family: var(--font-mono); font-size: 11.5px;
            background: var(--code-bg); color: var(--text-secondary);
            padding: 16px; overflow-y: auto; border-radius: 6px;
            border: 1px solid var(--border-primary); animation: fadeIn 0.3s ease;
        }}
        .log-container::-webkit-scrollbar {{ width: 5px; }}
        .log-container::-webkit-scrollbar-thumb {{ background: var(--border-primary); border-radius: 2px; }}
        .log-line   {{ padding: 1px 0; line-height: 1.55; }}
        .log-error  {{ color: var(--log-error);   font-weight: 600; }}
        .log-warning{{ color: var(--log-warning); }}
        .log-info   {{ color: var(--log-info); }}
        .search-highlight {{
            background: var(--accent-primary); color: var(--bg-primary);
            font-weight: 700; padding: 0 2px; border-radius: 2px;
        }}
        .hidden {{ display: none !important; }}
        /* ── Discovery indicator ─────────────────────────────────────────────── */
        #discovery-indicator {{
            position: fixed; bottom: 14px; right: 14px;
            background: var(--bg-elevated); border: 1px solid var(--accent-primary);
            color: var(--accent-primary); padding: 5px 12px; border-radius: 5px;
            font-size: 11px; font-family: var(--font-mono); z-index: 9999;
            transition: opacity var(--transition-normal); opacity: 0;
        }}
    </style>
</head>
<body>
    <div id="file-protocol-banner">
        <span class="fpb-badge">! file://</span>
        <span style="color:var(--text-secondary)"><code>fetch()</code> is blocked &mdash; plots will not load.</span>
        <code id="fpb-cmd">{project_name}_results.sh</code>
        <button class="fpb-copy" onclick="navigator.clipboard.writeText(document.getElementById('fpb-cmd').textContent).then(()=>{{this.textContent='Copied';setTimeout(()=>this.textContent='Copy',1500)}})">Copy</button>
        <span class="fpb-note">Run the .sh script in the results folder, then re-open the URL shown.</span>
    </div>
    <div id="header">
        <h1><span>{project_name}</span> &mdash; Analysis Archive</h1>
        <button id="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme">Theme</button>
    </div>
    <div id="search-box">
        <input type="text" id="search-input" placeholder="Search plots and logs..." />
    </div>
    <div id="sidebar">
        <div class="sidebar-tabs">
            <button class="sidebar-tab active" id="tab-tree" onclick="switchTab('tree')">Tree</button>
            <button class="sidebar-tab" id="tab-list" onclick="switchTab('list')">List</button>
        </div>
        <div id="proc-tree-view">
            <div id="proc-tree"></div>
        </div>
        <div id="list-view" style="display:none">
            <div id="tree"></div>
        </div>
    </div>
    <div id="content">
        <div class="empty-state">
            <h2>Select a plot from the sidebar</h2>
            <p>Click any item to view its interactive plot.</p>
        </div>
    </div>
    <div id="discovery-indicator"></div>
    
    <script>
        // ── Theme toggle ─────────────────────────────────────────────────────────
        (function() {{
            const saved = localStorage.getItem('toolbox-theme') || 'dark';
            document.documentElement.setAttribute('data-theme', saved);
            document.getElementById('theme-toggle').textContent = saved === 'dark' ? 'Light' : 'Dark';
        }})();
        function toggleTheme() {{
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('toolbox-theme', next);
            document.getElementById('theme-toggle').textContent = next === 'dark' ? 'Light' : 'Dark';
        }}

        // ── file:// protocol detection ───────────────────────────────────────────
        (function() {{
            if (window.location.protocol !== 'file:') return;
            var b = document.getElementById('file-protocol-banner');
            if (b) b.style.display = 'flex';
            var ss = document.getElementById('search-box');
            var sb = document.getElementById('sidebar');
            var ct = document.getElementById('content');
            if (ss) ss.style.top = '90px';
            if (sb) sb.style.top = '137px';
            if (ct) ct.style.top = '90px';
        }})();

        let plotMeta = {{}};  // populated by directory discovery below
        let searchTerm = '';

        // ── Parquet sidecar loader (hyparquet + compressors for ZSTD/Brotli) ──────
        const _plot_cache = {{}};
        let _hyparquetPromise = null;
        function getHyparquet() {{
            if (!_hyparquetPromise) _hyparquetPromise = Promise.all([
                import('https://esm.sh/hyparquet@1'),
                import('https://esm.sh/hyparquet-compressors@1').catch(() => ({{}}))
            ]).then(([hq, hqc]) => ({{ ...hq, compressors: hqc.compressors }}));
            return _hyparquetPromise;
        }}

        async function loadSidecar(id) {{
            if (_plot_cache[id] !== undefined) return _plot_cache[id];
            const meta = plotMeta[id];
            if (!meta) throw new Error('Unknown plot id: ' + id);
            const pid = (meta.path && meta.path[0]) || 'unknown';

            if (meta.type === 'log') {{
                // Path stored in meta.file (relative to archive root) — no URL construction needed
                const url = meta.file.startsWith('/') || meta.file.startsWith('http') ? meta.file : '/' + meta.file;
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('HTTP ' + resp.status + ' fetching ' + url + ' — open via HTTP server (serve_html.ps1 or GitHub Pages)');
                let rows;
                if ((resp.headers.get('Content-Type') || '').includes('application/json')) {{
                    rows = await resp.json();  // server already decoded parquet
                }} else {{
                    const buf = await resp.arrayBuffer();
                    const {{ parquetRead, compressors }} = await getHyparquet();
                    rows = [];
                    await parquetRead({{ file: buf, compressors, onComplete: data => rows.push(...data) }});
                }}
                const content = rows.length > 0 ? (rows[0].content || '') : '';
                const result = {{ content }};
                _plot_cache[id] = result;
                return result;
            }}

            // Plot: fetch .parquet, decode, build Plotly figure
            const rawUrl = meta.file || (pid + '/plots/' + id + '.parquet');
            const url = rawUrl.startsWith('/') || rawUrl.startsWith('http') ? rawUrl : '/' + rawUrl;
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('HTTP ' + resp.status + ' fetching ' + url + ' — open via HTTP server (serve_html.ps1 or GitHub Pages)');
            let rows;
            if ((resp.headers.get('Content-Type') || '').includes('application/json')) {{
                rows = await resp.json();  // server already decoded parquet
            }} else {{
                const buf = await resp.arrayBuffer();
                const {{ parquetRead, compressors }} = await getHyparquet();
                rows = [];
                await parquetRead({{ file: buf, compressors, onComplete: data => rows.push(...data) }});
            }}
            const result = buildFigureFromTable(rows, id);
            _plot_cache[id] = result;
            return result;
        }}

        // ── Helpers ──────────────────────────────────────────────────────────────
        // Recursively convert TypedArrays (Float64Array etc.) to plain JS arrays.
        // hyparquet returns typed arrays for numeric list columns.
        function toPlain(v) {{
            if (v === null || v === undefined) return v;
            if (ArrayBuffer.isView(v)) return Array.from(v);
            if (Array.isArray(v)) return v.map(toPlain);
            return v;
        }}
        function toLst(v) {{
            v = toPlain(v);
            if (v === null || v === undefined) return [];
            return Array.isArray(v) ? v : [v];
        }}
        function stdDev(arr) {{
            if (!arr.length) return 0;
            const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
            return Math.sqrt(arr.reduce((s, v) => s + (v - mean) ** 2, 0) / arr.length);
        }}
        function grayN(i, n) {{ return 'hsl(0,0%,' + Math.round(20 + 55 * i / Math.max(n - 1, 1)) + '%)'; }}
        function buildErrorY(arr) {{
            if (!arr || !arr.length || arr.every(v => v == null)) return undefined;
            return {{ type: 'data', array: arr, visible: true }};
        }}
        function buildGridLayout(subLabels, n, titleText, xLabel, yLabel) {{
            const layout = {{ title: titleText, template: 'plotly_white', height: 600,
                margin: {{ l: 60, r: 40, t: 80, b: 60 }},
                grid: {{ rows: 1, columns: n, pattern: 'independent' }} }};
            subLabels.forEach((lbl, i) => {{
                const ax = i === 0 ? '' : String(i + 1);
                layout['xaxis' + ax] = {{ title: i === Math.floor(n / 2) ? xLabel : '' }};
                layout['yaxis' + ax] = {{ title: i === 0 ? yLabel : '' }};
            }});
            layout.annotations = subLabels.map((lbl, i) => ({{
                text: String(lbl), showarrow: false,
                x: (i + 0.5) / n, y: 1.07, xref: 'paper', yref: 'paper', font: {{ size: 12 }}
            }}));
            return layout;
        }}

        // ── Figure builder ────────────────────────────────────────────────────────
        function buildFigureFromTable(rows, fallbackId) {{
            if (!rows || !rows.length) return {{ data: [], layout: {{ title: 'No data' }}, title: 'No data' }};
            const first = rows[0];
            const plotType = String(first.plot_type || 'line');
            const title   = String(first.title || fallbackId || '');
            const xLabel  = String(first.x_label || '');
            const yLabel  = String(first.y_label || '');
            const labels  = toLst(first.labels).map(String);
            const base = {{ title, template: 'plotly_white', hovermode: 'closest', height: 600,
                margin: {{ l: 60, r: 40, t: 60, b: 60 }},
                xaxis: {{ title: xLabel }}, yaxis: {{ title: yLabel }} }};

            // Multi-row: each row is a separate condition
            if (rows.length > 1) {{
                const subL = rows.map(r => String(r.condition || r.title || ''));
                const data = rows.map((row, i) => {{
                    const rx = toLst(row.x_data), ry = toLst(row.y_data), rv = toLst(row.y_var);
                    const nested = ry.length > 0 && Array.isArray(ry[0]);
                    return {{
                        type: 'bar',
                        x: nested ? toLst(rx[0] !== undefined ? rx[0] : rx) : rx,
                        y: nested ? toLst(ry[0]) : ry,
                        error_y: buildErrorY(rv.length ? (Array.isArray(rv[0]) ? toLst(rv[0]) : rv) : null),
                        marker: {{ color: 'dimgray' }}, showlegend: false,
                        xaxis: 'x' + (i > 0 ? String(i + 1) : ''),
                        yaxis: 'y' + (i > 0 ? String(i + 1) : ''),
                    }};
                }});
                return {{ data, layout: buildGridLayout(subL, rows.length, title, xLabel, yLabel), title }};
            }}

            const xR = toLst(first.x_data), yR = toLst(first.y_data), yVR = toLst(first.y_var);
            const cy = yR.length > 0 && Array.isArray(yR[0]);
            const cx = xR.length > 0 && Array.isArray(xR[0]);
            const n  = cy ? yR.length : (cx ? xR.length : 0);

            // ── LINE / LINE_GRID ──────────────────────────────────────────────────
            if (plotType === 'line' || plotType === 'line_grid') {{
                if (n > 0) {{
                    const seriesL = labels.length ? labels : Array.from({{ length: n }}, (_, i) => String(i + 1));
                    const ch = Array.from({{ length: n }}, (_, i) => {{
                        const yd = toLst(cy ? yR[i] : yR);
                        const xd = cx ? toLst(xR[i]) : (xR.length ? xR : Array.from({{ length: yd.length }}, (_, j) => j));
                        return {{ xd, yd, lbl: seriesL[i] || String(i) }};
                    }}).filter(c => c.yd.length > 0);
                    if (ch.length === 1) {{
                        return {{ data: [{{ type: 'scatter', mode: 'lines', x: ch[0].xd, y: ch[0].yd,
                            name: ch[0].lbl, line: {{ color: 'dimgray', width: 1.5 }} }}], layout: base, title }};
                    }}
                    const allY = ch.flatMap(c => c.yd);
                    const yRange = allY.length ? Math.max(...allY) - Math.min(...allY) : 1;
                    const off = Math.max(3 * stdDev(allY), yRange / ch.length * 1.5) || 1;
                    const data = ch.map(({{ xd, yd, lbl }}, i) => ({{
                        type: 'scatter', mode: 'lines', x: xd, y: yd.map(v => v + (ch.length - 1 - i) * off),
                        name: lbl, line: {{ color: grayN(i, ch.length), width: 1 }}, opacity: 0.9, customdata: yd,
                        hovertemplate: '<b>' + lbl + '</b><br>x: %{{x}}<br>y: %{{customdata}}<extra></extra>'
                    }}));
                    return {{ data, layout: {{ ...base, yaxis: {{ title: yLabel || 'Channels (staggered)', showgrid: false, zeroline: false }} }}, title }};
                }}
                const yd = yR, xd = xR.length ? xR : Array.from({{ length: yd.length }}, (_, i) => i);
                return {{ data: [{{ type: 'scatter', mode: 'lines', x: xd, y: yd, line: {{ color: 'dimgray', width: 1.5 }} }}], layout: base, title }};
            }}

            // ── GRID ──────────────────────────────────────────────────────────────
            if (plotType === 'grid') {{
                if (n > 0) {{
                    const subL = labels.length ? labels : Array.from({{ length: n }}, (_, i) => 'Cond ' + (i + 1));
                    const data = Array.from({{ length: n }}, (_, i) => ({{
                        type: 'bar',
                        x: cx ? toLst(xR[i]) : xR,
                        y: cy ? toLst(yR[i]) : yR,
                        error_y: buildErrorY(yVR.length > i ? toLst(yVR[i]) : (yVR.length === 1 ? yVR : null)),
                        marker: {{ color: 'dimgray' }}, showlegend: false,
                        xaxis: 'x' + (i > 0 ? String(i + 1) : ''),
                        yaxis: 'y' + (i > 0 ? String(i + 1) : ''),
                    }}));
                    return {{ data, layout: buildGridLayout(subL, n, title, xLabel, yLabel), title }};
                }}
                // Flat grid (txt_tree-style) — simple bar
                return {{ data: [{{ type: 'bar', x: xR, y: yR, error_y: buildErrorY(yVR), marker: {{ color: 'dimgray' }} }}], layout: base, title }};
            }}

            // ── BAR ───────────────────────────────────────────────────────────────
            if (plotType === 'bar') {{
                if (cy) {{
                    // labels = series names, x_data = shared x-axis categories
                    const seriesL = labels.length ? labels : Array.from({{ length: n }}, (_, i) => String(i + 1));
                    const data = Array.from({{ length: n }}, (_, i) => ({{
                        type: 'bar', name: seriesL[i],
                        x: cx ? toLst(xR[i]) : xR,
                        y: toLst(yR[i]),
                        error_y: buildErrorY(yVR.length > i ? toLst(yVR[i]) : null),
                        marker: {{ color: n === 1 ? 'dimgray' : grayN(i, n) }}
                    }}));
                    const layout = {{ ...base }};
                    if (n > 1) layout.barmode = 'group';
                    return {{ data, layout, title }};
                }}
                return {{ data: [{{ type: 'bar', x: xR, y: yR, error_y: buildErrorY(yVR), marker: {{ color: 'dimgray' }} }}], layout: base, title }};
            }}

            // ── SCATTER ───────────────────────────────────────────────────────────
            if (plotType === 'scatter') {{
                const xd = cx ? toLst(xR[0]) : xR, yd = cy ? toLst(yR[0]) : yR;
                return {{ data: [{{ type: 'scatter', mode: 'markers', x: xd, y: yd, marker: {{ color: 'dimgray', size: 4, opacity: 0.6 }} }}], layout: base, title }};
            }}

            return {{ data: [], layout: {{ ...base, title: 'Unsupported type: ' + plotType }}, title }};
        }}
        
        // Search functionality
        document.getElementById('search-input').addEventListener('input', (e) => {{
            searchTerm = e.target.value.toLowerCase();
            // Preserve current active plot while filtering
            const _cur = document.querySelector('.tree-item.active, .proc-node.active')?.dataset?.plotId;
            renderFlatList(plotMeta, _cur);
            // Also filter the tree view without full re-render
            applyTreeSearch();
            // If viewing log, re-render with search highlight
            if (window.currentLog && document.getElementById('log-display')) {{
                renderLog(window.showFullLog);
            }}
        }});
        
        function switchTab(tab) {{
            const isTree = tab === 'tree';
            document.getElementById('proc-tree-view').style.display = isTree ? '' : 'none';
            document.getElementById('list-view').style.display = isTree ? 'none' : '';
            // #search-box is always visible — it works for both tree and list views
            document.getElementById('tab-tree').classList.toggle('active', isTree);
            document.getElementById('tab-list').classList.toggle('active', !isTree);
            // Re-apply tree search filter when switching to tree tab
            if (isTree) applyTreeSearch();
        }}

        const PIPELINE_SCHEMA = [];  // unused — kept for API compat

        // ── Auto-discovery: build pipeline schema from actual step names ─────────
        function longestCommonPrefix(strs) {{
            if (!strs.length) return '';
            let prefix = strs[0];
            for (let i = 1; i < strs.length; i++) {{
                while (!strs[i].startsWith(prefix)) {{
                    prefix = prefix.slice(0, -1);
                    if (!prefix) return '';
                }}
            }}
            // Only strip at underscore boundary
            const lastUs = prefix.lastIndexOf('_');
            return lastUs >= 0 ? prefix.slice(0, lastUs + 1) : '';
        }}

        function buildAutoSchema(data) {{
            const perPidSteps = new Set();
            const sharedSteps = new Set();
            const stepIds = {{}};
            for (const [id, info] of Object.entries(data)) {{
                if (info.type === 'log') continue;
                const step = info.path[1];
                if (!step) continue;
                if (/_concat$/.test(step)) {{
                    sharedSteps.add(step);
                }} else {{
                    perPidSteps.add(step);
                }}
                stepIds[step] = id;
            }}
            // Group per-participant steps by first underscore token
            const chainMap = {{}};
            for (const step of perPidSteps) {{
                const key = step.split('_')[0];
                if (!chainMap[key]) chainMap[key] = [];
                if (!chainMap[key].includes(step)) chainMap[key].push(step);
            }}
            // Sort within each chain: shorter name = earlier stage
            for (const key of Object.keys(chainMap)) {{
                chainMap[key].sort((a, b) => a.length - b.length || a.localeCompare(b));
            }}
            // Associate shared outputs with the chain whose steps contain the shared step's base
            const chainShared = {{}};
            const orphanShared = [];
            for (const step of sharedSteps) {{
                const base = step.replace(/_fai_concat$/, '').replace(/_concat$/, '');
                let matched = false;
                for (const key of Object.keys(chainMap)) {{
                    if (chainMap[key].some(s => s.includes(base)) || base.includes(key)) {{
                        if (!chainShared[key]) chainShared[key] = [];
                        chainShared[key].push(step);
                        matched = true;
                        break;
                    }}
                }}
                if (!matched) orphanShared.push(step);
            }}
            return {{ chainMap, chainShared, orphanShared, stepIds }};
        }}

        function makeGroupSection(label, startOpen) {{
            const grp = document.createElement('div');
            grp.className = 'proc-group';
            const hdr = document.createElement('div');
            hdr.className = 'proc-group-hdr';
            hdr.innerHTML = `<span class="proc-toggle">${{startOpen ? '\u25bc' : '\u25b6'}}</span> ${{label}}`;
            const body = document.createElement('div');
            body.className = 'proc-group-body';
            if (!startOpen) body.style.display = 'none';
            hdr.onclick = () => {{
                const open = body.style.display !== 'none';
                body.style.display = open ? 'none' : '';
                hdr.querySelector('.proc-toggle').textContent = open ? '\u25b6' : '\u25bc';
            }};
            return {{ grp, hdr, body }};
        }}

        function makeChainNode(label, title, plotId, isGroup) {{
            const node = document.createElement('span');
            const has = !!plotId;
            node.className = 'proc-node ' + (has ? (isGroup ? 'group-ok' : 'ok') : (isGroup ? 'group-missing' : 'missing'));
            node.textContent = label;
            node.title = title;
            if (has) {{
                node.dataset.plotId = plotId;
                node.onclick = () => showPlot(plotId);
            }}
            return node;
        }}

        // Filter proc-tree DOM by searchTerm
        function applyTreeSearch() {{
            const el = document.getElementById('proc-tree');
            if (!el) return;
            if (!searchTerm) {{
                el.querySelectorAll('.proc-pid-row, .proc-group').forEach(n => n.style.display = '');
                return;
            }}
            el.querySelectorAll('.proc-pid-row').forEach(row => {{
                row.style.display = row.textContent.toLowerCase().includes(searchTerm) ? '' : 'none';
            }});
            el.querySelectorAll('.proc-group').forEach(grp => {{
                const body = grp.querySelector('.proc-group-body');
                if (!body) {{ grp.style.display = ''; return; }}
                const hasVisible = Array.from(body.querySelectorAll('.proc-pid-row'))
                    .some(r => r.style.display !== 'none');
                grp.style.display = hasVisible ? '' : 'none';
            }});
        }}

        function renderProcedureTree(data) {{
            const byPid = {{}};
            for (const [id, info] of Object.entries(data)) {{
                if (info.type === 'log') continue;
                const pid = info.path[0];
                const step = info.path[1];
                if (!step) continue;
                if (!byPid[pid]) byPid[pid] = {{}};
                byPid[pid][step] = id;
            }}
            const sortedPids = Object.keys(byPid).sort();
            const el = document.getElementById('proc-tree');
            if (!el) return;
            el.innerHTML = '';

            const {{ chainMap, chainShared, orphanShared, stepIds }} = buildAutoSchema(data);

            for (const chainKey of Object.keys(chainMap).sort()) {{
                const chainSteps = chainMap[chainKey];
                const sharedForChain = chainShared[chainKey] || [];
                const commonPfx = longestCommonPrefix(chainSteps);
                const stepLabel = s => (commonPfx && s.startsWith(commonPfx) ? s.slice(commonPfx.length) : s) || s;

                const {{ grp, hdr, body }} = makeGroupSection(chainKey.replace(/_/g, ' '), true);
                let anyRendered = false;

                for (const pid of sortedPids) {{
                    const pidSteps = byPid[pid] || {{}};
                    const chain = document.createElement('div');
                    chain.className = 'proc-chain';
                    let anyInSection = false;
                    chainSteps.forEach((step, i) => {{
                        const plotId = pidSteps[step] || null;
                        if (plotId) anyInSection = true;
                        if (i > 0) {{
                            const arr = document.createElement('span');
                            arr.className = 'proc-arrow';
                            arr.textContent = '\u2192';
                            chain.appendChild(arr);
                        }}
                        chain.appendChild(makeChainNode(stepLabel(step), step, plotId, false));
                    }});
                    if (anyInSection) {{
                        anyRendered = true;
                        const row = document.createElement('div');
                        row.className = 'proc-pid-row';
                        const lbl = document.createElement('div');
                        lbl.className = 'proc-pid-label';
                        lbl.textContent = pid;
                        row.appendChild(lbl);
                        row.appendChild(chain);
                        body.appendChild(row);
                    }}
                }}

                if (sharedForChain.length > 0) {{
                    const sharedChain = document.createElement('div');
                    sharedChain.className = 'proc-chain';
                    let anyShared = false;
                    sharedForChain.forEach((step, i) => {{
                        const plotId = stepIds[step] || null;
                        if (plotId) anyShared = true;
                        if (i > 0) {{
                            const dot = document.createElement('span');
                            dot.style.cssText = 'color:#aaa;font-size:11px;padding:0 2px;';
                            dot.textContent = '\u00b7';
                            sharedChain.appendChild(dot);
                        }}
                        sharedChain.appendChild(makeChainNode(step, step, plotId, true));
                    }});
                    if (anyShared) {{
                        body.appendChild(Object.assign(document.createElement('hr'), {{className: 'proc-group-divider'}}));
                        const sharedRow = document.createElement('div');
                        sharedRow.className = 'proc-pid-row';
                        const lbl = document.createElement('div');
                        lbl.className = 'proc-pid-label';
                        lbl.textContent = 'group';
                        sharedRow.appendChild(lbl);
                        sharedRow.appendChild(sharedChain);
                        body.appendChild(sharedRow);
                    }}
                }}

                if (anyRendered) {{
                    grp.appendChild(hdr);
                    grp.appendChild(body);
                    el.appendChild(grp);
                }}
            }}

            // Orphan shared outputs (no chain matched)
            if (orphanShared.length > 0) {{
                const {{ grp, hdr, body }} = makeGroupSection('Group outputs', true);
                const chain = document.createElement('div');
                chain.className = 'proc-chain';
                orphanShared.forEach((step, i) => {{
                    const plotId = stepIds[step] || null;
                    if (i > 0) {{
                        const dot = document.createElement('span');
                        dot.style.cssText = 'color:#aaa;font-size:11px;padding:0 2px;';
                        dot.textContent = '\u00b7';
                        chain.appendChild(dot);
                    }}
                    chain.appendChild(makeChainNode(step, step, plotId, true));
                }});
                const row = document.createElement('div');
                row.className = 'proc-pid-row';
                row.appendChild(chain);
                body.appendChild(row);
                grp.appendChild(hdr);
                grp.appendChild(body);
                el.appendChild(grp);
            }}

            // Logs section
            const logEntries = Object.entries(data).filter(([, m]) => m.type === 'log');
            if (logEntries.length > 0) {{
                const {{ grp, hdr, body }} = makeGroupSection('Logs', false);
                const byPidLog = {{}};
                for (const [id, info] of logEntries) {{
                    const pid = info.path[0];
                    if (!byPidLog[pid]) byPidLog[pid] = [];
                    byPidLog[pid].push([id, info.path[1] || id]);
                }}
                for (const pid of Object.keys(byPidLog).sort()) {{
                    for (const [id, label] of byPidLog[pid]) {{
                        const row = document.createElement('div');
                        row.className = 'proc-pid-row';
                        const lbl = document.createElement('div');
                        lbl.className = 'proc-pid-label';
                        lbl.textContent = pid;
                        const chain = document.createElement('div');
                        chain.className = 'proc-chain';
                        chain.appendChild(makeChainNode(label, label, id, false));
                        row.appendChild(lbl);
                        row.appendChild(chain);
                        body.appendChild(row);
                    }}
                }}
                grp.appendChild(hdr);
                grp.appendChild(body);
                el.appendChild(grp);
            }}
            applyTreeSearch();
        }}
        function renderFlatList(data, keepActiveId) {{
            const treeEl = document.getElementById('tree');
            treeEl.innerHTML = '';
            
            // Group plots by participant (first element of path)
            const byParticipant = {{}};
            for (const [key, plot] of Object.entries(data)) {{
                const participant = plot.path[0] || 'unknown';
                if (!byParticipant[participant]) byParticipant[participant] = [];
                byParticipant[participant].push([key, plot]);
            }}
            
            
            // Sort participants
            const sortedParticipants = Object.keys(byParticipant).sort();
            
            for (const participant of sortedParticipants) {{
                // Create folder
                const folder = document.createElement('div');
                folder.className = 'tree-folder';
                folder.innerHTML = `<span class="tree-folder-icon expanded">▶</span> 📁 ${{participant}}`;
                
                const folderContent = document.createElement('div');
                folderContent.className = 'tree-folder-content expanded';
                
                // Toggle folder
                folder.onclick = () => {{
                    folderContent.classList.toggle('expanded');
                    folder.querySelector('.tree-folder-icon').classList.toggle('expanded');
                }};
                
                // Sort files within participant
                const sortedFiles = byParticipant[participant].sort((a, b) => {{
                    const nameA = a[1].path[1] || a[0];
                    const nameB = b[1].path[1] || b[0];
                    return nameA.localeCompare(nameB);
                }});
                
                // Filter files by search term (metadata only - no content search for logs since lazy)
                const filteredFiles = searchTerm ? sortedFiles.filter(([key, plot]) => {{
                    const searchableText = `${{plot.title}} ${{plot.path.join(' ')}} ${{key}}`;
                    return searchableText.toLowerCase().includes(searchTerm);
                }}) : sortedFiles;
                
                // Skip folder if no matching files
                if (filteredFiles.length === 0) continue;
                
                // Add file items
                for (const [key, plot] of filteredFiles) {{
                    const item = document.createElement('div');
                    item.className = 'tree-item';
                    const icon = plot.type === 'log' ? '📋' : '📊';
                    item.innerHTML = `${{icon}} ${{plot.path[1] || key}}`;
                    item.dataset.plotId = key;
                    item.onclick = (e) => {{
                        e.stopPropagation();
                        showPlot(key);
                    }};
                    folderContent.appendChild(item);
                }}
                
                treeEl.appendChild(folder);
                treeEl.appendChild(folderContent);
                
                // Global log folder: start collapsed — {project_name}.log can be very large
                if (participant === 'global') {{
                    folderContent.classList.remove('expanded');
                    folder.querySelector('.tree-folder-icon').classList.remove('expanded');
                }}
                // Expand first non-global folder and auto-show first plot only on initial load
                if (participant === sortedParticipants.find(p => p !== 'global') && !keepActiveId) {{
                    folder.querySelector('.tree-folder-icon').classList.add('expanded');
                    if (sortedFiles.length > 0) {{
                        showPlot(sortedFiles[0][0]);
                    }}
                }}
            }}
        }}
        
        async function showPlot(plotId) {{
            const meta = plotMeta[plotId];
            if (!meta) return;
            
            // Update active state in both list and tree views
            document.querySelectorAll('.tree-item, .proc-node').forEach(el => el.classList.remove('active'));
            document.querySelectorAll(`[data-plot-id="${{plotId}}"]`).forEach(el => el.classList.add('active'));
            
            const content = document.getElementById('content');
            
            if (meta.type === 'log') {{
                content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Loading log...</p></div>`;
                try {{
                    const data = await loadSidecar(plotId);
                    const logData = parseLogs(data.content);
                    content.innerHTML = `
                        <div class="plot-title">${{meta.title}}</div>
                        <div class="log-stats">
                            <span>Total lines: <strong>${{logData.total}}</strong></span>
                            <span class="error-count">Errors: ${{logData.errors.length}}</span>
                            <span class="warning-count">Warnings: ${{logData.warnings.length}}</span>
                            <span class="info-count">Info: ${{logData.infos.length}}</span>
                        </div>
                        <button class="log-toggle" onclick="toggleLogView()">
                            <span id="log-toggle-text">Show Errors / Warnings / Info Only</span>
                        </button>
                        <div class="log-container" id="log-display"></div>
                    `;
                    window.currentLog = logData;
                    window.showFullLog = true;
                    renderLog(true);
                }} catch(e) {{
                    content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Error loading log: ${{e.message}}</p></div>`;
                }}
            }} else {{
                content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Loading plot...</p></div>`;
                try {{
                    const plotJson = await loadSidecar(plotId);
                    const safeName = plotId.replace(/[^a-z0-9_-]/gi, '_');
                    content.innerHTML = `
                        <div class="plot-title">${{meta.title}}</div>
                        <div class="export-bar">
                            <button class="export-btn png" onclick="exportPlot('png','${{safeName}}')">&#8659; PNG</button>
                            <button class="export-btn svg" onclick="exportPlot('svg','${{safeName}}')">&#8659; SVG</button>
                            <button class="export-btn pdf" onclick="downloadPDF('${{safeName}}')">&#8659; PDF</button>
                            <span class="export-size">
                                Width: <select id="exp-w" onchange="resizePlotForExport()">
                                    <option value="800">800px</option>
                                    <option value="1200" selected>1200px</option>
                                    <option value="1600">1600px</option>
                                    <option value="2400">2400px (print)</option>
                                </select>
                                Height: <select id="exp-h" onchange="resizePlotForExport()">
                                    <option value="400">400px</option>
                                    <option value="600" selected>600px</option>
                                    <option value="900">900px</option>
                                    <option value="1200">1200px</option>
                                </select>
                            </span>
                        </div>
                        <div class="plot-container" id="plot-container"></div>
                    `;
                    Plotly.newPlot('plot-container', plotJson.data, plotJson.layout, {{responsive: true}});
                }} catch(e) {{
                    content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Error loading plot: ${{e.message}}</p></div>`;
                }}
            }}
        }}
        
        function parseLogs(logText) {{
            const lines = logText.split('\\n');
            const errors = [];
            const warnings = [];
            const infos = [];
            // Use case-insensitive word-boundary matching to catch WARNING/Warning, ERROR/Error, etc.
            const isError   = l => /\\berror\\b/i.test(l);
            const isWarning = l => /\\bwarning\\b/i.test(l) && !/\\berror\\b/i.test(l);
            const isInfo    = l => /\\bINFO:/i.test(l) || /\\[INFO\\]/i.test(l);
            
            lines.forEach((line, idx) => {{
                if (isError(line))        errors.push({{idx, line}});
                else if (isWarning(line)) warnings.push({{idx, line}});
                else if (isInfo(line))    infos.push({{idx, line}});
            }});
            
            return {{ lines, errors, warnings, infos, total: lines.length }};
        }}
        
        function classifyLine(line) {{
            if (/\\berror\\b/i.test(line))   return 'log-error';
            if (/\\bwarning\\b/i.test(line)) return 'log-warning';
            if (/\\bINFO:/i.test(line) || /\\[INFO\\]/i.test(line)) return 'log-info';
            return '';
        }}

        function highlightLine(line) {{
            let s = escapeHtml(line);
            if (searchTerm && line.toLowerCase().includes(searchTerm)) {{
                const rx = new RegExp(`(${{escapeRegex(searchTerm)}})`, 'gi');
                s = s.replace(rx, '<span class="search-highlight">$1</span>');
            }}
            return s;
        }}

        function renderLog(showFull) {{
            const logDisplay = document.getElementById('log-display');
            const log = window.currentLog;
            
            if (showFull) {{
                // Full log with colour-coding
                logDisplay.innerHTML = log.lines.map(line => {{
                    const cls = classifyLine(line);
                    return `<div class="log-line ${{cls}}">${{highlightLine(line)}}</div>`;
                }}).join('');
            }} else {{
                // Filtered view: errors + warnings + info lines in log order
                const filtered = [...log.errors, ...log.warnings, ...log.infos]
                    .sort((a, b) => a.idx - b.idx);
                const visible = searchTerm
                    ? filtered.filter(item => item.line.toLowerCase().includes(searchTerm))
                    : filtered;
                if (visible.length === 0) {{
                    const msg = searchTerm ? 'No matching lines found' : 'No errors, warnings or info lines found';
                    logDisplay.innerHTML = `<div class="log-line" style="color:#4ec9b0;">${{msg}}</div>`;
                }} else {{
                    logDisplay.innerHTML = visible.map(item => {{
                        const cls = classifyLine(item.line);
                        return `<div class="log-line ${{cls}}">${{highlightLine(item.line)}}</div>`;
                    }}).join('');
                }}
            }}
        }}
        
        function toggleLogView() {{
            window.showFullLog = !window.showFullLog;
            document.getElementById('log-toggle-text').textContent =
                window.showFullLog ? 'Show Errors / Warnings / Info Only' : 'Show Full Log';
            renderLog(window.showFullLog);
        }}

        function exportPlot(format, filename) {{
            const el = document.getElementById('plot-container');
            if (!el) return;
            const w = parseInt(document.getElementById('exp-w')?.value || 1200);
            const h = parseInt(document.getElementById('exp-h')?.value || 600);
            Plotly.downloadImage(el, {{format, filename, width: w, height: h}});
        }}

        function resizePlotForExport() {{
            // No-op: Plotly.downloadImage uses its own width/height, no resize needed
        }}

        async function downloadPDF(filename) {{
            const el = document.getElementById('plot-container');
            if (!el) return;
            const w = parseInt(document.getElementById('exp-w')?.value || 1200);
            const h = parseInt(document.getElementById('exp-h')?.value || 600);
            const title = document.querySelector('.plot-title')?.textContent || 'Plot';
            const btn = document.querySelector('.export-btn.pdf');
            if (btn) {{ btn.textContent = '⏳ Generating...'; btn.disabled = true; }}
            try {{
                const url = await Plotly.toImage(el, {{format: 'png', width: w, height: h, scale: 2}});
                if (!window.jspdf) throw new Error('jsPDF not loaded — check CDN connectivity');
                const {{ jsPDF }} = window.jspdf;
                // Choose orientation based on aspect ratio
                const orient = w >= h ? 'landscape' : 'portrait';
                // jsPDF uses points (72pt = 1in). Scale px → pt at 96dpi: pt = px * 72/96
                const ptW = Math.round(w * 72 / 96);
                const ptH = Math.round(h * 72 / 96);
                const pdf = new jsPDF({{ orientation: orient, unit: 'pt', format: [ptW, ptH] }});
                // Add title as small header
                pdf.setFontSize(10);
                pdf.setTextColor(80);
                pdf.text(title, 10, 12);
                // Image fills page below header
                pdf.addImage(url, 'PNG', 0, 18, ptW, ptH - 18);
                const safe = (filename || title).replace(/[^a-z0-9_-]/gi, '_');
                pdf.save(safe + '.pdf');
            }} catch(e) {{
                alert('PDF generation failed: ' + e.message);
            }} finally {{
                if (btn) {{ btn.textContent = '\u21D3 PDF'; btn.disabled = false; }}
            }}
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function escapeRegex(text) {{
            return text.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
        }}
        
        // Load plot metadata from pre-built meta.json (written by the pipeline).
        // Falls back to directory listing scan if meta.json is not yet available.
        async function discoverFromDirectory() {{
            const meta = {{}};
            try {{
                // Primary: meta.json
                const metaResp = await fetch('{project_name}_meta.json?_=' + Date.now()).catch(() => null);
                if (metaResp?.ok) {{
                    const loaded = await metaResp.json().catch(() => null);
                    if (loaded && typeof loaded === 'object') {{
                        Object.assign(meta, loaded);
                        if (!meta['global_log']) {{
                            const gHead = await fetch('{project_name}.log.parquet', {{ method: 'HEAD' }}).catch(() => null);
                            if (gHead?.ok)
                                meta['global_log'] = {{ title: 'Pipeline Log', path: ['global', 'log'], type: 'log', file: '.bin/{project_name}.log.parquet' }};
                        }}
                        return meta;
                    }}
                }}
                // Fallback: directory listing
                const rootResp = await fetch('/?_=' + Date.now()).catch(() => null);
                if (!rootResp?.ok) return meta;
                const rootHtml = await rootResp.text();
                const pidMatches = [...rootHtml.matchAll(/href="({project_name}_[A-Za-z0-9]+)\\/?"/g)];
                const pids = [...new Set(pidMatches.map(m => m[1]))];
                for (const pid of pids) {{
                    const logFile = pid + '/' + pid + '.log.parquet';
                    const logHead = await fetch(logFile, {{ method: 'HEAD' }}).catch(() => null);
                    if (logHead?.ok)
                        meta[pid + '_log'] = {{ title: pid + ' Log', path: [pid, 'log'], type: 'log', file: logFile }};
                    const plotsResp = await fetch('/' + pid + '/plots/?_=' + Date.now()).catch(() => null);
                    if (!plotsResp?.ok) continue;
                    const plotsHtml = await plotsResp.text();
                    const pqMatches = [...plotsHtml.matchAll(/href="([^"?#]+\\.parquet)"/g)];
                    for (const m of pqMatches) {{
                        const plotId = m[1].replace(/\\.parquet$/, '');
                        const plotName = plotId.startsWith(pid + '_') ? plotId.slice(pid.length + 1) : plotId;
                        meta[plotId] = {{ title: plotId, path: [pid, plotName], type: 'plot' }};
                    }}
                }}
                const gHead = await fetch('{project_name}.log.parquet', {{ method: 'HEAD' }}).catch(() => null);
                if (gHead?.ok) meta['global_log'] = {{ title: 'Pipeline Log', path: ['global', 'log'], type: 'log', file: '.bin/{project_name}.log.parquet' }};
            }} catch(e) {{ console.warn('Discovery failed:', e); }}
            return meta;
        }}

        (function startDiscovery() {{
            let lastJson = '';
            let indicator = null;

            function updateIndicator(n) {{
                if (!indicator) {{
                    indicator = document.getElementById('discovery-indicator');
                }}
                if (!indicator) return;
                indicator.textContent = n + ' plots loaded';
                indicator.style.opacity = '1';
                clearTimeout(indicator._hide);
                indicator._hide = setTimeout(() => {{ indicator.style.opacity = '0'; }}, 3000);
            }}

            async function discover() {{
                try {{
                    const newMeta = await discoverFromDirectory();
                    const newJson = JSON.stringify(newMeta);
                    if (newJson !== lastJson) {{
                        plotMeta = newMeta;
                        lastJson = newJson;
                        const currentId = document.querySelector('.tree-item.active, .proc-node.active')?.dataset?.plotId;
                        renderFlatList(plotMeta, currentId);
                        renderProcedureTree(plotMeta);
                        if (currentId) {{
                            document.querySelectorAll('[data-plot-id="' + currentId + '"]').forEach(el => el.classList.add('active'));
                        }}
                        updateIndicator(Object.keys(newMeta).length);
                    }}
                }} catch(e) {{ /* server not running — silent */ }}
                setTimeout(discover, 10000);
            }}
            discover();
        }})();
    </script>
</body>
</html>"""

def _write_meta_json(archive_dir, meta, meta_name):
    """Write {project}_meta.json at the archive root — polled by the HTML page every 10s for live sidebar updates."""
    try:
        meta_path = os.path.join(archive_dir, meta_name)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
    except Exception:
        pass  # non-critical — polling will just skip this cycle


def update_archive(archive_path, participant_id, plot_id, plot_title, tree_path, relative_file=None):
    """Register a plot in the unified archive meta.json and rewrite the HTML shell.

    Args:
        relative_file: Path to the parquet sidecar relative to the archive root.
                       Falls back to '{participant_id}/plots/{plot_id}.parquet' if not given.
    Logs are stored as .parquet sidecars via add_log_to_archive().
    """
    archive_path = os.path.abspath(archive_path)
    archive_dir = os.path.dirname(archive_path)
    os.makedirs(archive_dir, exist_ok=True)
    
    # Lock lives next to the HTML — always accessible
    import hashlib, time
    path_hash = hashlib.md5(archive_path.encode()).hexdigest()[:16]
    lock_path = os.path.join(archive_dir, f'.lock_{path_hash}')
    
    # Retry opening the lock file — transient ENOENT can occur on WSL/network mounts under load
    for attempt in range(10):
        try:
            os.makedirs(archive_dir, exist_ok=True)
            lock_file = open(lock_path, 'w')
            break
        except FileNotFoundError:
            time.sleep(0.5 * (attempt + 1))
    else:
        raise FileNotFoundError(f"Could not open lock file after retries: {lock_path}")
    try:
        acquire_file_lock(lock_file)
        
        # Load existing metadata only
        existing_meta = load_or_create_archive(archive_path)
        
        # Update metadata entry
        existing_meta[plot_id] = {
            'title': plot_title,
            'path': tree_path,
            'type': 'plot',
            'file': relative_file or f'{participant_id}/plots/{plot_id}.parquet'
        }
        # Write {project}_meta.json (polled by open browser tabs for live updates)
        _write_meta_json(archive_dir, existing_meta, _meta_filename(archive_path))
        # Create HTML shell once — never needs to be regenerated
        if not os.path.exists(archive_path):
            _pn = _meta_filename(archive_path).replace('_meta.json', '')
            with open(archive_path, 'w', encoding='utf-8') as f:
                f.write(create_archive_html(_pn))
            _write_launcher_sidecar(archive_path)
        return archive_path
    finally:
        release_file_lock(lock_file)
        lock_file.close()
        try:
            os.remove(lock_path)
        except:
            pass

def add_log_to_archive(archive_path, participant_id, log_path, log_name):
    """Add a log file to the unified archive with file locking.
    
    Args:
        archive_path: Path to the HTML archive
        participant_id: Participant ID (or 'global' for project logs)
        log_path: Path to the log file
        log_name: Display name for the log
    """
    # Normalize path immediately to avoid issues with relative paths
    archive_path = os.path.abspath(archive_path)
    
    # Ensure parent directory exists
    archive_dir = os.path.dirname(archive_path)
    os.makedirs(archive_dir, exist_ok=True)
    archive_root = os.path.dirname(archive_dir) if os.path.basename(archive_dir) == '.bin' else archive_dir

    log_id = f'{participant_id}_log'

    if log_path.endswith('.log.parquet') and os.path.exists(log_path):
        # Parquet was written live during the run — use it at its existing location.
        # Just register the path in meta.json; no re-write needed.
        sidecar_path = os.path.abspath(log_path)
        relative_file = os.path.relpath(sidecar_path, archive_root).replace('\\', '/')
    else:
        # Text log file — read content and write as parquet sidecar (legacy path).
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"Error reading log file: {e}"

        # Write log content as parquet sidecar
        # Global logs: {log_name}.parquet inside .bin/; participant logs: alongside their plots/ folder
        if participant_id == 'global':
            log_filename  = f'{log_name}.parquet'
            sidecar_dir   = archive_dir
            relative_file = '.bin/' + log_filename
        else:
            log_filename  = f'{participant_id}.log.parquet'
            sidecar_dir   = os.path.join(archive_root, participant_id)
            relative_file = f'{participant_id}/{log_filename}'
        os.makedirs(sidecar_dir, exist_ok=True)
        sidecar_path = os.path.join(sidecar_dir, log_filename)
        # For the global log: append to existing parquet content so each participant's
        # finalization adds to the running log rather than overwriting it.
        if participant_id == 'global' and os.path.exists(sidecar_path):
            try:
                existing = pl.read_parquet(sidecar_path)
                existing_content = existing['content'][0] if len(existing) > 0 else ''
                log_content = (existing_content or '') + log_content
            except Exception:
                pass  # start fresh if existing parquet is unreadable
        pl.DataFrame({'content': [log_content]}).write_parquet(sidecar_path, compression='snappy')

    # Lock lives next to HTML
    import hashlib, time
    path_hash = hashlib.md5(archive_path.encode()).hexdigest()[:16]
    lock_path = os.path.join(archive_dir, f'.lock_{path_hash}')
    
    for attempt in range(10):
        try:
            os.makedirs(archive_dir, exist_ok=True)
            lock_file = open(lock_path, 'w')
            break
        except FileNotFoundError:
            time.sleep(0.5 * (attempt + 1))
    else:
        raise FileNotFoundError(f"Could not open lock file after retries: {lock_path}")
    try:
        acquire_file_lock(lock_file)
        
        existing_meta = load_or_create_archive(archive_path)
        existing_meta[log_id] = {
            'title': log_name,
            'path': [participant_id, log_name],
            'type': 'log',
            'file': relative_file   # relative path from archive root for JS fetch
        }

        _write_meta_json(archive_dir, existing_meta, _meta_filename(archive_path))
        # Create HTML shell once — never needs to be regenerated
        if not os.path.exists(archive_path):
            _pn = _meta_filename(archive_path).replace('_meta.json', '')
            with open(archive_path, 'w', encoding='utf-8') as f:
                f.write(create_archive_html(_pn))
            _write_launcher_sidecar(archive_path)
        return archive_path
    finally:
        release_file_lock(lock_file)
        lock_file.close()
        try:
            os.remove(lock_path)
        except:
            pass

def run(inp, out_dir, pre, project_name='procedure', sidecar_dir=None):
    """Copy _vis.parquet sidecar and register it in the HTML archive.

    Args:
        sidecar_dir: Directory for the sidecar parquet (required).
                     Passed as CONTEXT_PLOT_DIR from the IOInterface bash block.

    The browser reads the .parquet directly via hyparquet (no server-side
    Plotly rendering). This keeps Python out of the figure-building loop.

    Args:
        inp: Input _vis.parquet file
        out_dir: Output directory (archive root, used to locate .bin/)
        pre: Prefix for output file (e.g. EV_002_xdf4_extr1_filt_vis)
        project_name: Project name used for the HTML filename
        sidecar_dir: Explicit directory for the sidecar parquet. Overrides the
                     default <out_dir>/<pid>/plots/ so callers can place sidecars
                     in EV_l1/<pid>/plots/ without changing out_dir.
    """
    import shutil
    print(f"[interactive_plotter] Input: {inp}")

    try:
        df = pl.read_parquet(inp)
    except Exception as e:
        print(f"[interactive_plotter] ERROR: Failed to read {inp}: {e}")
        return

    # Extract participant ID from prefix (e.g. EV_002_xdf4_extr1_filt -> EV_002)
    parts = pre.split('_')
    participant_id = '_'.join(parts[:2]) if len(parts) >= 2 else 'participant'

    # Read title from parquet (first row)
    try:
        plot_title = df.row(0, named=True).get('title', pre) if len(df) > 0 else pre
    except Exception:
        plot_title = pre

    tree_path = parse_filename_to_tree_path(pre, participant_id)
    out_dir_abs = os.path.abspath(out_dir)
    archive_path = os.path.join(out_dir_abs, '.bin', f"{project_name}_results.html")

    # Copy parquet to sidecar location (sidecar_dir is always supplied by IOInterface).
    if not sidecar_dir:
        print(f"[interactive_plotter] ERROR: sidecar_dir is required (was None). Skipping {pre}.")
        return
    participant_plots_dir = os.path.abspath(sidecar_dir)
    os.makedirs(participant_plots_dir, exist_ok=True)
    sidecar_dest = os.path.join(participant_plots_dir, f'{pre}.parquet')
    shutil.copy2(inp, sidecar_dest)
    print(f"[interactive_plotter] Copied sidecar: {sidecar_dest} ({os.path.getsize(sidecar_dest)//1024} KB)")

    # Compute the sidecar path relative to the archive root (parent of .bin/)
    sidecar_rel = os.path.relpath(sidecar_dest, out_dir_abs).replace('\\', '/')
    try:
        update_archive(archive_path, participant_id, pre, plot_title or pre, tree_path, relative_file=sidecar_rel)
        print(f"[interactive_plotter] Updated archive: {archive_path} ({os.path.getsize(archive_path)//1024} KB)")
    except Exception as e:
        print(f"[interactive_plotter] ERROR: Failed to update archive: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) >= 2 else ''

    if cmd == 'init':
        # Create the static HTML shell (only needed once, or to recover a deleted file)
        # Usage: interactive_plotter.py init <html_path>
        if len(sys.argv) < 3:
            print('Usage: interactive_plotter.py init <html_path>')
            sys.exit(1)
        html_path = os.path.abspath(sys.argv[2])
        # Derive project_name from filename: EV_procedure_results.html → EV_procedure
        base = os.path.basename(html_path)
        proj = base.replace('_results.html', '') if base.endswith('_results.html') else 'procedure'
        os.makedirs(os.path.dirname(html_path), exist_ok=True)
        if not os.path.exists(html_path):
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(create_archive_html(proj))
            _write_launcher_sidecar(html_path)
        print(f'[interactive_plotter] Initialized: {html_path}')

    elif cmd == 'add-log':
        # Register a log file in the archive
        # Usage: interactive_plotter.py add-log <html> <pid> <log_path> <name>
        if len(sys.argv) < 6:
            print('Usage: interactive_plotter.py add-log <html> <pid> <log_path> <name>')
            sys.exit(1)
        add_log_to_archive(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])

    elif len(sys.argv) >= 4:
        # Register a plot parquet sidecar
        # Usage: interactive_plotter.py <vis.parquet> <out_dir> <prefix> [project] [sidecar_dir]
        project_name = sys.argv[4] if len(sys.argv) >= 5 else 'procedure'
        sidecar_dir  = sys.argv[5] if len(sys.argv) >= 6 else None
        run(sys.argv[1], sys.argv[2], sys.argv[3], project_name, sidecar_dir)

    else:
        print('[interactive_plotter] Subcommands:')
        print('  init <html>                             — Create the static HTML shell')
        print('  add-log <html> <pid> <log> <name>       — Register a log file')
        print('  <parquet> <out_dir> <prefix> [project]  — Register a plot')
        sys.exit(1)

