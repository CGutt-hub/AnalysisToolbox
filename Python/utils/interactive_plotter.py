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
        EV_003_xdf3_log_tddr, EV_003 -> ['EV_003', 'xdf3_log_tddr']
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
<html>
<head>
    <meta charset="UTF-8">
    <title>EmotiView - Interactive Procedure Archive</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; display: flex; height: 100vh; overflow: hidden; }}
        #header {{ position: fixed; top: 0; left: 0; right: 0; height: 50px; background: #2c3e50; color: white; display: flex; align-items: center; padding: 0 20px; z-index: 1000; }}
        #header h1 {{ font-size: 18px; font-weight: 600; }}
        #search-box {{ position: fixed; top: 50px; left: 0; width: 280px; padding: 15px; background: #ecf0f1; border-bottom: 1px solid #ddd; z-index: 999; }}
        #search-input {{ width: 100%; padding: 8px 12px; border: 1px solid #bdc3c7; border-radius: 4px; font-size: 13px; }}
        #search-input:focus {{ outline: none; border-color: #3498db; }}
        #sidebar {{ position: fixed; left: 0; top: 105px; bottom: 0; width: 280px; background: #f5f5f5; border-right: 1px solid #ddd; overflow-y: auto; padding: 20px; }}
        #content {{ position: fixed; left: 280px; top: 50px; right: 0; bottom: 0; padding: 20px; overflow-y: auto; background: white; }}
        .tree-item {{ padding: 8px 12px; cursor: pointer; user-select: none; color: #555; transition: all 0.2s; border-radius: 4px; margin: 2px 0; font-size: 13px; }}
        .tree-item:hover {{ background: #e8e8e8; color: #2c3e50; }}
        .tree-item.active {{ background: #3498db; color: white; font-weight: 600; }}
        .tree-folder {{ padding: 8px 12px; cursor: pointer; user-select: none; color: #2c3e50; font-weight: 600; font-size: 14px; margin: 4px 0; }}
        .tree-folder:hover {{ background: #e8e8e8; }}
        .tree-folder-content {{ margin-left: 12px; display: none; }}
        .tree-folder-content.expanded {{ display: block; }}
        .tree-folder-icon {{ display: inline-block; width: 16px; transition: transform 0.2s; }}
        .tree-folder-icon.expanded {{ transform: rotate(90deg); }}
        .plot-container {{ width: 100%; height: calc(100vh - 200px); }}
        .plot-title {{ font-size: 24px; font-weight: 600; color: #2c3e50; margin-bottom: 10px; }}
        .export-bar {{ display: flex; gap: 8px; margin-bottom: 12px; align-items: center; flex-wrap: wrap; }}
        .export-btn {{ padding: 6px 14px; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600; }}
        .export-btn.png {{ background: #27ae60; color: white; }}
        .export-btn.svg {{ background: #2980b9; color: white; }}
        .export-btn.pdf {{ background: #8e44ad; color: white; }}
        .export-btn:hover {{ opacity: 0.85; }}
        .export-size {{ font-size: 12px; color: #555; display: flex; gap: 6px; align-items: center; }}
        .export-size select {{ font-size: 12px; padding: 4px; border-radius: 4px; border: 1px solid #ccc; }}
        .empty-state {{ text-align: center; padding: 100px 20px; color: #999; }}
        .empty-state h2 {{ margin-bottom: 10px; }}
        .log-container {{ width: 100%; height: calc(100vh - 150px); font-family: 'Courier New', monospace; font-size: 12px; background: #1e1e1e; color: #d4d4d4; padding: 20px; overflow-y: auto; border-radius: 4px; }}
        .log-line {{ padding: 2px 0; }}
        .log-error {{ color: #f48771; font-weight: 600; }}
        .log-warning {{ color: #dcdcaa; }}
        .log-info {{ color: #9cdcfe; }}
        .log-info {{ color: #4fc1ff; }}
        .log-toggle {{ margin-bottom: 15px; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 600; }}
        .log-toggle:hover {{ background: #2980b9; }}
        .log-stats {{ margin-bottom: 15px; padding: 10px; background: #f5f5f5; border-radius: 4px; font-size: 13px; }}
        .log-stats span {{ margin-right: 20px; }}
        .log-stats .error-count {{ color: #e74c3c; font-weight: 600; }}
        .log-stats .warning-count {{ color: #f39c12; font-weight: 600; }}
        .log-stats .info-count {{ color: #3498db; font-weight: 600; }}
        .search-highlight {{ background-color: #f39c12; color: #000; font-weight: 600; padding: 0 2px; }}
        .search-result-count {{ margin-left: 10px; font-size: 12px; color: #7f8c8d; }}
        .hidden {{ display: none !important; }}
        .sidebar-tabs {{ display:flex; border-bottom:2px solid #ddd; margin-bottom:10px; }}
        .sidebar-tab {{ flex:1; padding:8px 4px; text-align:center; cursor:pointer; font-size:12px; font-weight:600; color:#888; background:none; border:none; border-bottom:3px solid transparent; transition:all 0.2s; }}
        .sidebar-tab.active {{ color:#3498db; border-bottom:3px solid #3498db; }}
        .proc-group {{ margin-bottom:6px; }}
        .proc-group-hdr {{ padding:7px 10px; font-size:12px; font-weight:700; color:#fff; background:#34495e; border-radius:4px; cursor:pointer; display:flex; align-items:center; gap:8px; user-select:none; }}
        .proc-group-hdr:hover {{ background:#2c3e50; }}
        .proc-group-body {{ padding:4px 0 2px 8px; }}
        .proc-pid-row {{ margin-bottom:5px; }}
        .proc-pid-label {{ font-size:10px; font-weight:700; text-transform:uppercase; color:#95a5a6; letter-spacing:0.5px; margin-bottom:2px; }}
        .proc-chain {{ display:flex; flex-wrap:wrap; align-items:center; gap:3px; }}
        .proc-node {{ display:inline-flex; align-items:center; padding:3px 8px; border-radius:10px; font-size:11px; border:1px solid transparent; transition:all 0.15s; white-space:nowrap; }}
        .proc-node.ok {{ background:#d5f5e3; color:#1e8449; border-color:#a9dfbf; cursor:pointer; }}
        .proc-node.ok:hover {{ background:#a9dfbf; }}
        .proc-node.ok.active {{ background:#27ae60; color:#fff; border-color:#1e8449; }}
        .proc-node.missing {{ background:#f5f5f5; color:#ccc; border-color:#eee; font-style:italic; }}
        .proc-node.group-ok {{ background:#d6eaf8; color:#1a5276; border-color:#a9cce3; cursor:pointer; }}
        .proc-node.group-ok:hover {{ background:#a9cce3; }}
        .proc-node.group-ok.active {{ background:#2980b9; color:#fff; border-color:#1a5276; }}
        .proc-node.group-missing {{ background:#f5f5f5; color:#ccc; border-color:#eee; font-style:italic; }}
        .proc-arrow {{ color:#ccc; font-size:13px; line-height:1; }}
        .proc-group-divider {{ border:none; border-top:1px solid #e8e8e8; margin:5px 0; }}
    </style>
</head>
<body>
    <div id="header">
        <h1>EmotiView - Interactive Procedure Archive</h1>
    </div>
    <div id="search-box">
        <input type="text" id="search-input" placeholder="Search plots and logs..." />
    </div>
    <div id="sidebar">
        <div class="sidebar-tabs">
            <button class="sidebar-tab active" id="tab-tree" onclick="switchTab('tree')">🌲 Tree</button>
            <button class="sidebar-tab" id="tab-list" onclick="switchTab('list')">📋 List</button>
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
            <p>Click on any item to view its interactive plot.</p>
        </div>
    </div>
    
    <script>
        let plotMeta = {{}};  // populated by directory discovery below
        let searchTerm = '';

        // ── Parquet sidecar loader (hyparquet, pure JS — no WASM startup) ─────────
        const _EV_cache = {{}};
        let _hyparquetPromise = null;
        function getHyparquet() {{
            if (!_hyparquetPromise) _hyparquetPromise = import('https://esm.sh/hyparquet@1');
            return _hyparquetPromise;
        }}

        async function loadSidecar(id) {{
            if (_EV_cache[id] !== undefined) return _EV_cache[id];
            const meta = plotMeta[id];
            if (!meta) throw new Error('Unknown plot id: ' + id);
            const pid = (meta.path && meta.path[0]) || 'unknown';

            if (meta.type === 'log') {{
                // Path stored in meta.file (relative to archive root) — no URL construction needed
                const url = meta.file;
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('HTTP ' + resp.status + ' fetching ' + url + ' — open via HTTP server (serve_html.ps1 or GitHub Pages)');
                const buf = await resp.arrayBuffer();
                const {{ parquetRead }} = await getHyparquet();
                const rows = [];
                await parquetRead({{ file: buf, onComplete: data => rows.push(...data) }});
                const content = rows.length > 0 ? (rows[0].content || '') : '';
                const result = {{ content }};
                _EV_cache[id] = result;
                return result;
            }}

            // Plot: fetch .parquet, decode, build Plotly figure
            const url = pid + '/plots/' + id + '.parquet';
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('HTTP ' + resp.status + ' fetching ' + url + ' — open via HTTP server (serve_html.ps1 or GitHub Pages)');
            const buf = await resp.arrayBuffer();
            const {{ parquetRead }} = await getHyparquet();
            const rows = [];
            await parquetRead({{ file: buf, onComplete: data => rows.push(...data) }});
            const result = buildFigureFromTable(rows);
            _EV_cache[id] = result;
            return result;
        }}

        // ── Helpers ──────────────────────────────────────────────────────────────
        function toLst(v) {{
            if (v === null || v === undefined) return [];
            return Array.isArray(v) ? v : [v];
        }}
        function stdDev(arr) {{
            if (!arr.length) return 0;
            const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
            return Math.sqrt(arr.reduce((s, v) => s + (v - mean) ** 2, 0) / arr.length);
        }}
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

        // ── Figure builder — ports create_plotly_json() from Python ──────────────
        function buildFigureFromTable(rows) {{
            if (!rows || !rows.length) return {{ data: [], layout: {{ title: 'No data' }}, title: 'No data' }};
            const first = rows[0];
            const plotType = String(first.plot_type || 'line');
            const title   = String(first.title   || '');
            const xLabel  = String(first.x_label || '');
            const yLabel  = String(first.y_label || '');
            const labels  = toLst(first.labels).map(String);
            const base = {{ title, template: 'plotly_white', hovermode: 'closest', height: 600,
                margin: {{ l: 60, r: 40, t: 60, b: 60 }},
                xaxis: {{ title: xLabel }}, yaxis: {{ title: yLabel }} }};

            // Multi-row grid (each row = one condition, e.g. ea11)
            if (rows.length > 1 && rows.every(r => r.plot_type === 'grid')) {{
                const n = rows.length;
                const subL = rows.map(r => String(r.condition || ''));
                const data = rows.map((row, i) => ({{
                    type: 'bar', x: toLst(row.x_data), y: toLst(row.y_data),
                    error_y: buildErrorY(toLst(row.y_var)),
                    marker: {{ color: 'dimgray' }}, showlegend: false,
                    xaxis: 'x' + (i > 0 ? String(i + 1) : ''),
                    yaxis: 'y' + (i > 0 ? String(i + 1) : ''),
                }}));
                return {{ data, layout: buildGridLayout(subL, n, title, xLabel, yLabel), title }};
            }}

            const xR = toLst(first.x_data), yR = toLst(first.y_data), yVR = toLst(first.y_var);
            const cy = yR.length > 0 && Array.isArray(yR[0]);
            const cx = xR.length > 0 && Array.isArray(xR[0]);

            // Grid / line_grid with concatenated conditions
            if ((plotType === 'grid' || plotType === 'line_grid') && (cy || cx)) {{
                const n = cy ? yR.length : xR.length;
                const subL = labels.length ? labels : Array.from({{ length: n }}, (_, i) => 'Cond ' + (i + 1));
                const data = Array.from({{ length: n }}, (_, i) => ({{
                    type: 'bar',
                    x: cx ? toLst(xR[i]) : xR, y: cy ? toLst(yR[i]) : yR,
                    error_y: buildErrorY(yVR.length > i ? toLst(yVR[i]) : null),
                    marker: {{ color: 'dimgray' }}, showlegend: false,
                    xaxis: 'x' + (i > 0 ? String(i + 1) : ''),
                    yaxis: 'y' + (i > 0 ? String(i + 1) : ''),
                }}));
                return {{ data, layout: buildGridLayout(subL, n, title, xLabel, yLabel), title }};
            }}

            // Line
            if (plotType === 'line') {{
                if (labels.length > 1 && cy) {{
                    const ch = labels.map((lbl, i) => {{
                        if (i >= yR.length) return null;
                        const yd = toLst(yR[i]);
                        const xd = (cx && i < xR.length) ? toLst(xR[i]) : Array.from({{ length: yd.length }}, (_, j) => j);
                        return {{ xd, yd, lbl: String(lbl) }};
                    }}).filter(Boolean);
                    const allY = ch.flatMap(c => c.yd);
                    const yRange = allY.length ? Math.max(...allY) - Math.min(...allY) : 1;
                    const off = Math.max(3 * stdDev(allY), yRange / ch.length * 1.5) || 1;
                    const data = ch.map(({{ xd, yd, lbl }}, i) => ({{
                        type: 'scatter', mode: 'lines', x: xd, y: yd.map(v => v + (ch.length - 1 - i) * off),
                        name: lbl, line: {{ width: 1 }}, opacity: 0.8, customdata: yd,
                        hovertemplate: '<b>' + lbl + '</b><br>Time: %{{x}}<br>Value: %{{customdata}}<extra></extra>'
                    }}));
                    return {{ data, layout: {{ ...base, yaxis: {{ title: 'Channel (staggered)', showgrid: false, zeroline: false }} }}, title }};
                }}
                const yd = toLst(yR), xd = xR.length ? toLst(xR) : Array.from({{ length: yd.length }}, (_, i) => i);
                return {{ data: [{{ type: 'scatter', mode: 'lines', x: xd, y: yd, line: {{ color: 'dimgray', width: 1.5 }} }}], layout: base, title }};
            }}

            // Bar
            if (plotType === 'bar') {{
                if (cy || cx) {{
                    const nC = yR.length;
                    const xCats = labels.length ? labels : Array.from({{ length: nC }}, (_, i) => 'Cond ' + (i + 1));
                    const pairs = xR.length ? toLst(xR) : [''];
                    const data = pairs.map((pn, pi) => ({{
                        type: 'bar', name: String(pn), x: xCats,
                        y: Array.from({{ length: nC }}, (_, ci) => {{ const v = toLst(yR[ci]); return v[pi] ?? null; }}),
                        error_y: buildErrorY(yVR.length ? Array.from({{ length: nC }}, (_, ci) => {{ const v = toLst(yVR[ci]); return v[pi] ?? null; }}) : null),
                        marker: pairs.length === 1 ? {{ color: 'dimgray' }} : {{}}
                    }}));
                    const layout = {{ ...base }};
                    if (pairs.length > 1) layout.barmode = 'group';
                    return {{ data, layout, title }};
                }}
                return {{ data: [{{ type: 'bar', x: toLst(xR), y: toLst(yR), error_y: buildErrorY(yVR), marker: {{ color: 'dimgray' }} }}], layout: base, title }};
            }}

            // Scatter
            if (plotType === 'scatter') {{
                return {{ data: [{{ type: 'scatter', mode: 'markers', x: toLst(xR), y: toLst(yR), marker: {{ color: 'dimgray', size: 4, opacity: 0.6 }} }}], layout: base, title }};
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

        const PIPELINE_SCHEMA = [
            {{
                label: '📝 Questionnaires',
                steps: [
                    {{name: 'PANAS',   match: s => s === 'txt_tree_panas'}},
                    {{name: 'BIS/BAS', match: s => s === 'txt_tree_bisbas'}},
                    {{name: 'SAM',     match: s => s === 'txt_tree_sam'}},
                    {{name: 'BE7',     match: s => s === 'txt_tree_be7'}},
                    {{name: 'EA-11',   match: s => s === 'txt_tree_ea11'}},
                ],
                shared: [
                    {{name: 'SAM \u03a3',   match: s => s === 'sam_concat'}},
                    {{name: 'BE7 \u03a3',   match: s => s === 'be7_concat'}},
                    {{name: 'EA-11 \u03a3', match: s => s === 'ea11_concat'}},
                ]
            }},
            {{
                label: '\u26a1 EDA Chain',
                steps: [
                    {{name: 'Filtered',  match: s => /extr1_filt$/.test(s)}},
                    {{name: 'Artefact',  match: s => /extr1_filt_rej$/.test(s)}},
                    {{name: 'Epoched',   match: s => /extr1_filt_rej_epochs$/.test(s)}},
                    {{name: 'Bootstrap', match: s => /extr1.*windowed/.test(s)}},
                ],
                shared: [
                    {{name: 'EDA \u03a3', match: s => s === 'eda_concat'}},
                ]
            }},
            {{
                label: '\u2764\ufe0f HRV Chain',
                steps: [
                    {{name: 'Filtered', match: s => /extr2_filt$/.test(s)}},
                    {{name: 'Artefact', match: s => /extr2_filt_rej$/.test(s)}},
                    {{name: 'Peaks',    match: s => /extr2_filt_rej_peaks$/.test(s)}},
                ],
                shared: [
                    {{name: 'HRV \u03a3', match: s => s === 'hrv_concat'}},
                ]
            }},
            {{
                label: '\U0001f9e0 EEG / PSD',
                steps: [
                    {{name: 'Reref', match: s => /extr4_reref$/.test(s)}},
                    {{name: 'ICA',   match: s => /extr4_reref_filt_ica$/.test(s)}},
                    {{name: 'PSD',   match: s => /epochs_psd$/.test(s)}},
                ],
                shared: [
                    {{name: 'PSD \u03a3', match: s => s === 'psd_concat'}},
                    {{name: 'FAI \u03a3', match: s => s === 'psd_fai_concat'}},
                ]
            }},
            {{
                label: '\U0001fac0 fNIRS Chain',
                steps: [
                    {{name: 'Log',     match: s => s.startsWith('xdf') && s.endsWith('_log')}},
                    {{name: 'TDDR',    match: s => s.startsWith('xdf') && s.endsWith('_log_tddr')}},
                    {{name: 'MBLL',    match: s => s.startsWith('xdf') && s.endsWith('_log_tddr_regr_lin')}},
                    {{name: 'HbC Ep.', match: s => /epochs_hbc$/.test(s)}},
                ],
                shared: [
                    {{name: 'HbC \u03a3', match: s => s === 'hbc_concat'}},
                    {{name: 'FAI \u03a3', match: s => s === 'hbc_fai_concat'}},
                ]
            }},
        ];

        // Filter proc-tree DOM by searchTerm — called after render and on search input
        function applyTreeSearch() {{
            const el = document.getElementById('proc-tree');
            if (!el) return;
            if (!searchTerm) {{
                // Show everything
                el.querySelectorAll('.proc-pid-row, .proc-group').forEach(n => n.style.display = '');
                return;
            }}
            // Show/hide individual pid rows whose text matches
            el.querySelectorAll('.proc-pid-row').forEach(row => {{
                row.style.display = row.textContent.toLowerCase().includes(searchTerm) ? '' : 'none';
            }});
            // Hide entire proc-group sections that have no visible rows
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
            const allSteps = {{}};
            for (const [id, info] of Object.entries(data)) {{
                const pid = info.path[0];
                const step = info.path[1];
                if (!byPid[pid]) byPid[pid] = {{}};
                byPid[pid][step] = id;
                allSteps[step] = id;
            }}
            const sortedPids = Object.keys(byPid).sort();
            const el = document.getElementById('proc-tree');
            if (!el) return;
            el.innerHTML = '';

            for (const section of PIPELINE_SCHEMA) {{
                const grp = document.createElement('div');
                grp.className = 'proc-group';
                const hdr = document.createElement('div');
                hdr.className = 'proc-group-hdr';
                hdr.innerHTML = `<span class="proc-toggle">\u25bc</span> ${{section.label}}`;
                const body = document.createElement('div');
                body.className = 'proc-group-body';

                hdr.onclick = () => {{
                    const open = body.style.display !== 'none';
                    body.style.display = open ? 'none' : '';
                    hdr.querySelector('.proc-toggle').textContent = open ? '\u25b6' : '\u25bc';
                }};

                let anyRendered = false;
                for (const pid of sortedPids) {{
                    const pidSteps = byPid[pid] || {{}};
                    let anyInSection = false;
                    const chain = document.createElement('div');
                    chain.className = 'proc-chain';

                    section.steps.forEach((step, i) => {{
                        let plotId = null;
                        for (const [s, id] of Object.entries(pidSteps)) {{
                            if (step.match(s)) {{ plotId = id; break; }}
                        }}
                        if (plotId) anyInSection = true;
                        if (i > 0) {{
                            const arr = document.createElement('span');
                            arr.className = 'proc-arrow';
                            arr.textContent = '\u2192';
                            chain.appendChild(arr);
                        }}
                        const node = document.createElement('span');
                        node.className = 'proc-node ' + (plotId ? 'ok' : 'missing');
                        node.textContent = step.name;
                        node.title = step.name;
                        if (plotId) {{
                            node.dataset.plotId = plotId;
                            node.onclick = () => showPlot(plotId);
                        }}
                        chain.appendChild(node);
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

                if (section.shared && section.shared.length > 0) {{
                    const sharedChain = document.createElement('div');
                    sharedChain.className = 'proc-chain';
                    let anyShared = false;
                    section.shared.forEach((step, i) => {{
                        let plotId = null;
                        for (const s of Object.keys(allSteps)) {{
                            if (step.match(s)) {{ plotId = allSteps[s]; break; }}
                        }}
                        if (plotId) anyShared = true;
                        if (i > 0) {{
                            const dot = document.createElement('span');
                            dot.style.cssText = 'color:#aaa;font-size:11px;padding:0 2px;';
                            dot.textContent = '\u00b7';
                            sharedChain.appendChild(dot);
                        }}
                        const node = document.createElement('span');
                        node.className = 'proc-node ' + (plotId ? 'group-ok' : 'group-missing');
                        node.textContent = step.name;
                        node.title = step.name;
                        if (plotId) {{
                            node.dataset.plotId = plotId;
                            node.onclick = () => showPlot(plotId);
                        }}
                        sharedChain.appendChild(node);
                    }});
                    if (anyShared) {{
                        const hr = document.createElement('hr');
                        hr.className = 'proc-group-divider';
                        body.appendChild(hr);
                        const sharedRow = document.createElement('div');
                        sharedRow.className = 'proc-pid-row';
                        const lbl = document.createElement('div');
                        lbl.className = 'proc-pid-label';
                        lbl.textContent = '\U0001f465 group';
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

            // Logs section
            const logEntries = Object.entries(data).filter(([, m]) => m.type === 'log');
            if (logEntries.length > 0) {{
                const grp = document.createElement('div');
                grp.className = 'proc-group';
                const hdr = document.createElement('div');
                hdr.className = 'proc-group-hdr';
                hdr.innerHTML = '<span class="proc-toggle">\u25b6</span> \U0001f4cb Logs';
                const body = document.createElement('div');
                body.className = 'proc-group-body';
                body.style.display = 'none'; // start collapsed — Global EV_log can be large
                hdr.onclick = () => {{
                    const open = body.style.display !== 'none';
                    body.style.display = open ? 'none' : '';
                    hdr.querySelector('.proc-toggle').textContent = open ? '\u25b6' : '\u25bc';
                }};
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
                        const node = document.createElement('span');
                        node.className = 'proc-node ok';
                        node.textContent = '\U0001f4cb ' + label;
                        node.title = label;
                        node.dataset.plotId = id;
                        node.onclick = () => showPlot(id);
                        chain.appendChild(node);
                        row.appendChild(lbl);
                        row.appendChild(chain);
                        body.appendChild(row);
                    }}
                }}
                grp.appendChild(hdr);
                grp.appendChild(body);
                el.appendChild(grp);
            }}
            // Apply any active search filter to the freshly rendered tree
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
                
                // Global log folder: start collapsed — EV_log can be very large
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
                            <span class="error-count">&#10008; Errors: ${{logData.errors.length}}</span>
                            <span class="warning-count">&#9888; Warnings: ${{logData.warnings.length}}</span>
                            <span class="info-count">&#9432; Info: ${{logData.infos.length}}</span>
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
                            <button class="export-btn pdf" onclick="downloadPDF('${{safeName}}')">&#128196; Download PDF</button>
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
                    logDisplay.innerHTML = `<div class="log-line" style="color:#4ec9b0;">&#10003; ${{msg}}</div>`;
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
                if (btn) {{ btn.textContent = '\U0001F4C4 Download PDF'; btn.disabled = false; }}
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
        
        // Discover plots from the HTTP server directory listing — no meta.json dependency.
        // Scans EV_*/plots/*.parquet and EV_*/*.log.parquet on load and every 10 s.
        async function discoverFromDirectory() {{
            const meta = {{}};
            try {{
                const rootResp = await fetch('./?_=' + Date.now());
                if (!rootResp.ok) return meta;
                const rootHtml = await rootResp.text();
                const pidMatches = [...rootHtml.matchAll(/href="(EV_[A-Za-z0-9]+)\/?"/g)];
                const pids = [...new Set(pidMatches.map(m => m[1]))];
                for (const pid of pids) {{
                    const logFile = pid + '/' + pid + '.log.parquet';
                    const logHead = await fetch(logFile, {{ method: 'HEAD' }}).catch(() => null);
                    if (logHead?.ok) {{
                        meta[pid + '_log'] = {{ title: pid + ' Log', path: [pid, 'log'], type: 'log', file: logFile }};
                    }}
                    const plotsResp = await fetch(pid + '/plots/?_=' + Date.now()).catch(() => null);
                    if (!plotsResp?.ok) continue;
                    const plotsHtml = await plotsResp.text();
                    const pqMatches = [...plotsHtml.matchAll(/href="([^"?#]+\.parquet)"/g)];
                    for (const m of pqMatches) {{
                        const plotId = m[1].replace(/\.parquet$/, '');
                        const plotName = plotId.startsWith(pid + '_') ? plotId.slice(pid.length + 1) : plotId;
                        meta[plotId] = {{ title: plotId, path: [pid, plotName], type: 'plot' }};
                    }}
                }}
                const gHead = await fetch('EV_log.parquet', {{ method: 'HEAD' }}).catch(() => null);
                if (gHead?.ok) meta['global_log'] = {{ title: 'Pipeline Log', path: ['global', 'log'], type: 'log', file: 'EV_log.parquet' }};
            }} catch(e) {{ console.warn('Discovery failed:', e); }}
            return meta;
        }}

        (function startDiscovery() {{
            let lastJson = '';
            let indicator = null;

            function updateIndicator(n) {{
                if (!indicator) {{
                    indicator = document.createElement('div');
                    indicator.style.cssText = 'position:fixed;bottom:10px;right:10px;background:#27ae60;color:white;padding:6px 12px;border-radius:4px;font-size:12px;z-index:9999;';
                    document.body.appendChild(indicator);
                }}
                indicator.textContent = '\u27f3 ' + n + ' plots';
                clearTimeout(indicator._hide);
                indicator._hide = setTimeout(() => {{ if (indicator) indicator.style.opacity = '0.4'; }}, 3000);
                indicator.style.opacity = '1';
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


def update_archive(archive_path, participant_id, plot_id, plot_title, tree_path):
    """Register a plot in the unified archive meta.json and rewrite the HTML shell.

    The .parquet sidecar must already be copied to
    <archive_dir>/<participant_id>/plots/<plot_id>.parquet before calling this.
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
            'type': 'plot'
        }
        # Write {project}_meta.json (polled by open browser tabs for live updates)
        _write_meta_json(archive_dir, existing_meta, _meta_filename(archive_path))
        # Create HTML shell once — never needs to be regenerated
        if not os.path.exists(archive_path):
            _pn = _meta_filename(archive_path).replace('_meta.json', '')
            with open(archive_path, 'w', encoding='utf-8') as f:
                f.write(create_archive_html(_pn))
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
    
    # Read log content
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()
    except Exception as e:
        log_content = f"Error reading log file: {e}"
    
    # Write log content as parquet sidecar
    # Global logs: {log_name}.parquet at the archive root (e.g. EV_log.parquet)
    # Participant logs: {pid}/{pid}.log.parquet alongside their plots/ folder
    archive_dir = os.path.dirname(os.path.abspath(archive_path))
    os.makedirs(archive_dir, exist_ok=True)
    if participant_id == 'global':
        log_filename = f'{log_name}.parquet'         # e.g. EV_log.parquet
        sidecar_dir = archive_dir
        relative_file = log_filename
    else:
        log_filename = f'{participant_id}.log.parquet'  # e.g. EV_002.log.parquet
        sidecar_dir = os.path.join(archive_dir, participant_id)
        relative_file = f'{participant_id}/{log_filename}'
    log_id = f'{participant_id}_log'  # unique key per participant in meta.json
    os.makedirs(sidecar_dir, exist_ok=True)
    sidecar_path = os.path.join(sidecar_dir, log_filename)
    log_df = pl.DataFrame({'content': [log_content]})
    log_df.write_parquet(sidecar_path, compression='snappy')
    
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
        return archive_path
    finally:
        release_file_lock(lock_file)
        lock_file.close()
        try:
            os.remove(lock_path)
        except:
            pass

def run(inp, out_dir, pre, project_name='procedure'):
    """Copy _vis.parquet sidecar and register it in the HTML archive.

    The browser reads the .parquet directly via hyparquet (no server-side
    Plotly rendering). This keeps Python out of the figure-building loop.

    Args:
        inp: Input _vis.parquet file
        out_dir: Output directory (parent results folder = archive root)
        pre: Prefix for output file (e.g. EV_002_xdf4_extr1_filt_vis)
        project_name: Project name used for the HTML filename
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
    archive_path = os.path.join(os.path.abspath(out_dir), f"{project_name}_results.html")

    # Copy parquet to sidecar location: <archive_dir>/<pid>/plots/<pre>.parquet
    archive_dir = os.path.dirname(archive_path)
    participant_plots_dir = os.path.join(archive_dir, participant_id, 'plots')
    os.makedirs(participant_plots_dir, exist_ok=True)
    sidecar_dest = os.path.join(participant_plots_dir, f'{pre}.parquet')
    shutil.copy2(inp, sidecar_dest)
    print(f"[interactive_plotter] Copied sidecar: {sidecar_dest} ({os.path.getsize(sidecar_dest)//1024} KB)")

    try:
        update_archive(archive_path, participant_id, pre, plot_title or pre, tree_path)
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
        # Usage: interactive_plotter.py <vis.parquet> <out_dir> <prefix> [project]
        project_name = sys.argv[4] if len(sys.argv) >= 5 else 'procedure'
        run(sys.argv[1], sys.argv[2], sys.argv[3], project_name)

    else:
        print('[interactive_plotter] Subcommands:')
        print('  init <html>                             — Create the static HTML shell')
        print('  add-log <html> <pid> <log> <name>       — Register a log file')
        print('  <parquet> <out_dir> <prefix> [project]  — Register a plot')
        sys.exit(1)

