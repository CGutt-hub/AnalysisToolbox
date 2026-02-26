"""Interactive Plotter - Generate unified HTML archive with file tree navigation.

This plotter creates a single HTML file per participant containing all procedure plots
with a collapsible file tree for navigation.
"""
import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import sys
import os
import json
import re
import time
import fcntl
from typing import Any, IO

# Import shared helpers from plotter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plotter import to_lst

# Type-safe fcntl constants and functions
flock: Any = getattr(fcntl, 'flock')
LOCK_EX: int = getattr(fcntl, 'LOCK_EX')
LOCK_NB: int = getattr(fcntl, 'LOCK_NB')
LOCK_UN: int = getattr(fcntl, 'LOCK_UN')

def acquire_file_lock(f: IO[Any], timeout: float = 120) -> None:
    """Acquire exclusive lock on file with timeout (increased for large HTML files)."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            flock(f.fileno(), LOCK_EX | LOCK_NB)
            return
        except IOError:
            time.sleep(0.1)
    raise TimeoutError("Could not acquire file lock")

def release_file_lock(f: IO[Any]) -> None:
    """Release file lock."""
    flock(f.fileno(), LOCK_UN)

def compress_timeseries(x_data, y_data, max_points=5000):
    """Compress time series data using adaptive decimation that preserves peaks.
    
    Args:
        x_data: Time points (list or array)
        y_data: Signal values (list or array)
        max_points: Maximum number of points to retain
    
    Returns:
        Tuple of (compressed_x, compressed_y)
    """
    if len(x_data) <= max_points:
        return x_data, y_data
    
    # Use LTTB (Largest Triangle Three Buckets) decimation - preserves visual features
    x = np.array(x_data)
    y = np.array(y_data)
    
    # Simple bucket-based decimation with peak preservation
    bucket_size = len(x) // max_points
    indices = []
    
    for i in range(0, len(x), bucket_size):
        bucket_end = min(i + bucket_size, len(x))
        bucket_y = y[i:bucket_end]
        
        # Keep both min and max in each bucket to preserve peaks/troughs
        if len(bucket_y) > 0:
            min_idx = i + np.argmin(bucket_y)
            max_idx = i + np.argmax(bucket_y)
            indices.extend([min_idx, max_idx])
    
    # Add first and last points
    indices = sorted(set([0] + indices + [len(x) - 1]))
    
    return x[indices].tolist(), y[indices].tolist()

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

def create_plotly_json(df):
    """Create Plotly-compatible JSON data from vis dataframe."""
    
    # Check if we have multiple rows with grid plot type (e.g., EA11 with conditions)
    if len(df) > 1 and all(df['plot_type'] == 'grid'):
        # Multiple condition grid - each row is one condition
        first_row = df.row(0, named=True)
        x_label = first_row.get('x_label', '')
        y_label = first_row.get('y_label', '')
        title = first_row.get('title', '')
        
        # Create subplot for each condition
        fig = make_subplots(
            rows=1, cols=len(df),
            subplot_titles=[row.get('condition', f'Condition {i+1}') for i, row in enumerate(df.iter_rows(named=True))],
            horizontal_spacing=0.05
        )
        
        for idx, row_dict in enumerate(df.iter_rows(named=True)):
            x_data = to_lst(row_dict.get('x_data', []))
            y_data = to_lst(row_dict.get('y_data', []))
            y_var = to_lst(row_dict.get('y_var', []))
            
            if not x_data:
                x_data = list(range(len(y_data)))
            
            fig.add_trace(
                go.Bar(
                    x=x_data,
                    y=y_data,
                    error_y=dict(type='data', array=y_var) if y_var and any(v is not None for v in y_var) else None,
                    marker_color='dimgray',
                    showlegend=False
                ),
                row=1, col=idx+1
            )
            
            fig.update_xaxes(title_text=x_label if idx == len(df) // 2 else '', row=1, col=idx+1)
            fig.update_yaxes(title_text=y_label if idx == 0 else '', row=1, col=idx+1)
        
        fig.update_layout(
            title=title,
            template='plotly_white',
            height=600,
            margin=dict(l=60, r=40, t=60, b=60)
        )
        
        fig_json = fig.to_json()
        return json.loads(fig_json) if fig_json else {}, title
    
    # Single row processing (original logic)
    row = df.row(0, named=True)
    
    plot_type = row.get('plot_type', 'line')
    labels = to_lst(row.get('labels', []))
    x_label = row.get('x_label', '')
    y_label = row.get('y_label', '')
    title = row.get('title', '')
    
    # Get data and detect concatenation by inspecting structure (same as plotter.py)
    x_data_raw = row.get('x_data')
    y_data_raw = row.get('y_data')
    y_var = row.get('y_var')
    
    x_data = to_lst(x_data_raw)
    y_data = to_lst(y_data_raw)
    
    # Detect concatenation by checking if data contains nested lists
    is_concat_y = y_data and len(y_data) > 0 and isinstance(y_data[0], (list, tuple))
    is_concat_x = x_data and len(x_data) > 0 and isinstance(x_data[0], (list, tuple))
    is_concatenated = is_concat_y or is_concat_x
    
    # Create figure
    if plot_type in ('grid', 'line_grid') and is_concatenated:
        # Grid layout for concatenated conditions
        n_conditions = len(y_data) if is_concat_y else len(x_data)
        fig = make_subplots(
            rows=1, cols=n_conditions,
            subplot_titles=labels if labels else [f'Condition {i+1}' for i in range(n_conditions)],
            horizontal_spacing=0.05
        )
        
        y_var_list = to_lst(y_var) if y_var else []
        
        for idx in range(n_conditions):
            xd = to_lst(x_data[idx]) if is_concat_x else x_data
            yd = to_lst(y_data[idx]) if is_concat_y else y_data
            yv = to_lst(y_var_list[idx]) if y_var_list and idx < len(y_var_list) else None
            
            # Ensure xd has proper length
            if not xd or len(xd) == 0:
                xd = list(range(len(yd)))
            
            # Bar chart for grid
            fig.add_trace(
                go.Bar(
                    x=xd,
                    y=yd,
                    error_y=dict(type='data', array=yv) if yv else None,
                    marker_color='dimgray',
                    showlegend=False
                ),
                row=1, col=idx+1
            )
            
            fig.update_xaxes(title_text=x_label if idx == n_conditions // 2 else '', row=1, col=idx+1)
            fig.update_yaxes(title_text=y_label if idx == 0 else '', row=1, col=idx+1)
    
    elif plot_type == 'line':
        # Line plot (potentially many channels)
        fig = go.Figure()
        
        # Handle multi-channel time series
        if labels and len(labels) > 1 and is_concat_y:
            # Multiple channels - use staggered/offset visualization for clarity
            # First pass: collect all channel data and calculate offset spacing
            channel_data = []
            for idx, label in enumerate(labels):
                if idx < len(y_data):
                    yd_raw = to_lst(y_data[idx])
                    xd_raw = to_lst(x_data[idx]) if is_concat_x and idx < len(x_data) else list(range(len(yd_raw)))
                    
                    # Compress to max 5000 points per channel
                    xd, yd = compress_timeseries(xd_raw, yd_raw, max_points=5000)
                    channel_data.append((xd, yd, str(label)))
            
            # Calculate appropriate vertical offset based on signal range
            if channel_data:
                # Use the std of all channels to determine spacing
                all_y_values = []
                for _, yd, _ in channel_data:
                    all_y_values.extend(yd)
                
                if all_y_values:
                    y_std = np.std(all_y_values)
                    y_range = np.max(all_y_values) - np.min(all_y_values)
                    # Use 3x std or 1.5x range as offset spacing, whichever is larger
                    offset_spacing = max(3 * y_std, y_range / len(channel_data) * 1.5)
                else:
                    offset_spacing = 1.0
                
                # Second pass: add traces with staggered offsets
                channel_positions = []  # Track center position of each channel for y-axis labels
                for idx, (xd, yd, label) in enumerate(channel_data):
                    # Apply vertical offset (reverse order so first channel is on top)
                    offset = (len(channel_data) - 1 - idx) * offset_spacing
                    channel_positions.append((offset, label))
                    yd_offset = [y + offset for y in yd]
                    
                    fig.add_trace(go.Scatter(
                        x=xd,
                        y=yd_offset,
                        mode='lines',
                        name=label,
                        line=dict(width=1),
                        opacity=0.8,
                        hovertemplate=f'<b>{label}</b><br>Time: %{{x}}<br>Value: %{{customdata}}<extra></extra>',
                        customdata=[y for y in yd]  # Show original values in hover
                    ))
                
                # Add baseline reference lines for each channel at their offset
                for offset, label in channel_positions:
                    fig.add_hline(
                        y=offset,
                        line_dash="dot",
                        line_color="lightgray",
                        line_width=0.5,
                        annotation_text=label,
                        annotation_position="right",
                        annotation=dict(font=dict(size=10, color="gray"))
                    )
                
                # Configure y-axis to show offsets (meaningful for visual separation)
                fig.update_yaxes(
                    title_text='Channel Position (staggered)',
                    showgrid=False,
                    zeroline=False
                )
        else:
            # Single channel
            yd_raw = to_lst(y_data)
            xd_raw = to_lst(x_data) if x_data else list(range(len(yd_raw)))
            
            xd, yd = compress_timeseries(xd_raw, yd_raw, max_points=10000)
            
            fig.add_trace(go.Scatter(
                x=xd,
                y=yd,
                mode='lines',
                line=dict(color='dimgray', width=1.5)
            ))
            
            # Single channel y-axis with meaningful amplitude scale
            fig.update_yaxes(title_text=y_label)
        
        fig.update_xaxes(title_text=x_label)
    
    elif plot_type == 'bar':
        # Simple bar chart
        fig = go.Figure()
        
        xd = to_lst(x_data)
        yd = to_lst(y_data)
        yv = to_lst(y_var) if y_var else None
        
        fig.add_trace(go.Bar(
            x=xd,
            y=yd,
            error_y=dict(type='data', array=yv) if yv else None,
            marker_color='dimgray'
        ))
        
        fig.update_xaxes(title_text=x_label)
        fig.update_yaxes(title_text=y_label)
    
    elif plot_type == 'scatter':
        # Scatter plot with optional compression
        fig = go.Figure()
        
        xd_raw = to_lst(x_data)
        yd_raw = to_lst(y_data)
        
        # Compress if too many points
        if len(xd_raw) > 10000:
            indices = np.linspace(0, len(xd_raw) - 1, 10000, dtype=int)
            xd = [xd_raw[i] for i in indices]
            yd = [yd_raw[i] for i in indices]
        else:
            xd, yd = xd_raw, yd_raw
        
        fig.add_trace(go.Scatter(
            x=xd,
            y=yd,
            mode='markers',
            marker=dict(color='dimgray', size=4, opacity=0.6)
        ))
        
        fig.update_xaxes(title_text=x_label)
        fig.update_yaxes(title_text=y_label)
    
    else:
        print(f"[interactive_plotter] Warning: Unknown plot type '{plot_type}', creating empty plot")
        fig = go.Figure()
        fig.add_annotation(
            text=f"Unsupported plot type: {plot_type}",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=20)
        )
    
    # Update layout for interactivity
    fig.update_layout(
        title=title,
        template='plotly_white',
        hovermode='closest',
        height=600,
        margin=dict(l=60, r=40, t=60, b=60)
    )
    
    # Return figure JSON
    fig_json = fig.to_json()
    return json.loads(fig_json) if fig_json else {}, title

def load_or_create_archive(archive_path, participant_id):
    """Load existing archive metadata (titles/paths only, no plot data)."""
    if os.path.exists(archive_path):
        with open(archive_path, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r'(?:const|let) plotMeta = (\{.*?\});', content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return {}
    return {}

def create_archive_html(participant_id, plot_meta, inline_data=None):
    """Create HTML archive with all plot data embedded inline.
    
    inline_data: dict of plot_id -> plot JSON (embedded into window._EV_sidecar so no
    dynamic script injection is needed — works from file://, VS Code webview, anywhere).
    """
    sidecar_init = json.dumps(inline_data or {})
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>EmotiView - Interactive Procedure Archive</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
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
        .plot-container {{ width: 100%; height: calc(100vh - 150px); }}
        .plot-title {{ font-size: 24px; font-weight: 600; color: #2c3e50; margin-bottom: 20px; }}
        .empty-state {{ text-align: center; padding: 100px 20px; color: #999; }}
        .empty-state h2 {{ margin-bottom: 10px; }}
        .log-container {{ width: 100%; height: calc(100vh - 150px); font-family: 'Courier New', monospace; font-size: 12px; background: #1e1e1e; color: #d4d4d4; padding: 20px; overflow-y: auto; border-radius: 4px; }}
        .log-line {{ padding: 2px 0; }}
        .log-error {{ color: #f48771; font-weight: 600; }}
        .log-warning {{ color: #dcdcaa; }}
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
        <div id="tree"></div>
    </div>
    <div id="content">
        <div class="empty-state">
            <h2>Select a plot from the sidebar</h2>
            <p>Click on any item to view its interactive plot.</p>
        </div>
    </div>
    
    <script>
        let plotMeta = {json.dumps(plot_meta)};
        let searchTerm = '';
        // All plot data is embedded inline \u2014 no dynamic file loading needed.
        // Works from file://, VS Code webview, Simple Browser, or any context.
        window._EV_sidecar = {sidecar_init};
        
        function loadSidecar(id) {{
            return new Promise((resolve, reject) => {{
                // Already cached (either pre-embedded or previously loaded)
                if (window._EV_sidecar[id] !== undefined) {{
                    resolve(window._EV_sidecar[id]);
                    return;
                }}
                // Load on-demand via script tag (requires HTTP server, not file://)
                const script = document.createElement('script');
                script.src = 'plots/' + id + '.js';
                script.onload = () => {{
                    if (window._EV_sidecar[id] !== undefined) {{
                        resolve(window._EV_sidecar[id]);
                    }} else {{
                        reject(new Error('Sidecar loaded but data missing for ' + id));
                    }}
                }};
                script.onerror = () => reject(new Error(
                    'Could not load plots/' + id + '.js — open via HTTP server (AnalysisToolbox/Python/utils/serve_html.ps1)'
                ));
                document.head.appendChild(script);
            }});
        }}
        
        // Search functionality
        document.getElementById('search-input').addEventListener('input', (e) => {{
            searchTerm = e.target.value.toLowerCase();
            renderFlatList(plotMeta);
            
            // If viewing log, re-render with search highlight
            if (window.currentLog && document.getElementById('log-display')) {{
                renderLog(window.showFullLog);
            }}
        }});
        
        function renderFlatList(data) {{
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
                
                // Expand first folder and show first plot
                if (participant === sortedParticipants[0]) {{
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
            
            // Update active state
            document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`[data-plot-id="${{plotId}}"]`)?.classList.add('active');
            
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
                            <span id="log-toggle-text">Show Full Log</span>
                        </button>
                        <div class="log-container" id="log-display"></div>
                    `;
                    window.currentLog = logData;
                    window.showFullLog = false;
                    renderLog(false);
                }} catch(e) {{
                    content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Error loading log: ${{e.message}}</p></div>`;
                }}
            }} else {{
                content.innerHTML = `<div class="plot-title">${{meta.title}}</div><div class="empty-state"><p>Loading plot...</p></div>`;
                try {{
                    const plotJson = await loadSidecar(plotId);
                    content.innerHTML = `
                        <div class="plot-title">${{meta.title}}</div>
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
            const isError   = l => /\berror\b/i.test(l);
            const isWarning = l => /\bwarning\b/i.test(l) && !/\berror\b/i.test(l);
            const isInfo    = l => /\bINFO:/i.test(l) || /\[INFO\]/i.test(l);
            
            lines.forEach((line, idx) => {{
                if (isError(line))        errors.push({{idx, line}});
                else if (isWarning(line)) warnings.push({{idx, line}});
                else if (isInfo(line))    infos.push({{idx, line}});
            }});
            
            return {{ lines, errors, warnings, infos, total: lines.length }};
        }}
        
        function classifyLine(line) {{
            if (/\berror\b/i.test(line))   return 'log-error';
            if (/\bwarning\b/i.test(line)) return 'log-warning';
            if (/\bINFO:/i.test(line) || /\[INFO\]/i.test(line)) return 'log-info';
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
                window.showFullLog ? 'Show Errors / Warnings / Info' : 'Show Full Log';
            renderLog(window.showFullLog);
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function escapeRegex(text) {{
            return text.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
        }}
        
        // Initialize
        renderFlatList(plotMeta);
        
        // Live update: poll plots/meta.json every 10s so the sidebar stays current
        // while a pipeline run is in progress — no page reload needed.
        (function startPolling() {{
            let lastCount = Object.keys(plotMeta).length;
            let indicator = null;
            
            function updateIndicator(n) {{
                if (!indicator) {{
                    indicator = document.createElement('div');
                    indicator.style.cssText = 'position:fixed;bottom:10px;right:10px;background:#27ae60;color:white;padding:6px 12px;border-radius:4px;font-size:12px;z-index:9999;';
                    document.body.appendChild(indicator);
                }}
                indicator.textContent = '&#x27F3; ' + n + ' plots';
                clearTimeout(indicator._hide);
                indicator._hide = setTimeout(() => {{ if (indicator) {{ indicator.style.opacity = '0.4'; }} }}, 3000);
                indicator.style.opacity = '1';
            }}
            
            async function poll() {{
                try {{
                    const r = await fetch('plots/meta.json?_=' + Date.now());
                    if (!r.ok) return;
                    const newMeta = await r.json();
                    const newCount = Object.keys(newMeta).length;
                    if (newCount !== lastCount) {{
                        plotMeta = newMeta;
                        lastCount = newCount;
                        const currentId = document.querySelector('.tree-item.active')?.dataset?.plotId;
                        renderFlatList(plotMeta);
                        // Restore active state without re-loading the plot
                        if (currentId) {{
                            document.querySelector('[data-plot-id="' + currentId + '"]')?.classList.add('active');
                        }}
                        updateIndicator(newCount);
                    }}
                }} catch(e) {{ /* server not running or no meta.json yet — silent */ }}
                setTimeout(poll, 10000);
            }}
            setTimeout(poll, 10000);
        }})();
    </script>
</body>
</html>"""

def _read_all_sidecars(plots_dir, plot_meta, max_bytes=None):
    """Read sidecar .js files and return dict of plot_id -> plot data.
    
    If max_bytes is set, sidecars larger than that are skipped (left for dynamic loading).
    """
    inline_data = {}
    for plot_id in plot_meta:
        js_path = os.path.join(plots_dir, f'{plot_id}.js')
        if not os.path.exists(js_path):
            continue
        if max_bytes and os.path.getsize(js_path) > max_bytes:
            continue  # too large — browser will load dynamically over HTTP
        try:
            with open(js_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Extract JSON: window._EV_sidecar["id"] = {...};
            m = re.search(r'window\._EV_sidecar\["[^"]+"\] = (\{.+\});\s*$', content, re.DOTALL)
            if m:
                inline_data[plot_id] = json.loads(m.group(1))
        except Exception:
            pass  # skip unreadable/invalid sidecar
    return inline_data


def _write_meta_json(plots_dir, meta):
    """Write plots/meta.json — polled by the HTML page every 10s for live sidebar updates."""
    try:
        meta_path = os.path.join(plots_dir, 'meta.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
    except Exception:
        pass  # non-critical — polling will just skip this cycle


def update_archive(archive_path, participant_id, plot_id, plot_data, plot_title, tree_path):
    """Add or update a plot in the unified archive.
    
    Sidecar .js file holds the plot data; plots/meta.json holds the metadata
    and is polled every 10s by the open browser tab for live sidebar updates.
    """
    archive_path = os.path.abspath(archive_path)
    archive_dir = os.path.dirname(archive_path)
    os.makedirs(archive_dir, exist_ok=True)  # explicit — mount may need this under concurrent load
    plots_dir = os.path.join(archive_dir, 'plots')
    os.makedirs(plots_dir, exist_ok=True)
    
    # Write sidecar as a .js file so it loads via <script src> from file:// without CORS issues.
    # The script assigns into window._EV_sidecar which showPlot() reads.
    sidecar_path = os.path.join(plots_dir, f'{plot_id}.js')
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write('window._EV_sidecar = window._EV_sidecar || {};\n')
        f.write(f'window._EV_sidecar[{json.dumps(plot_id)}] = {json.dumps(plot_data)};\n')
    
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
        existing_meta = load_or_create_archive(archive_path, participant_id)
        
        # Update metadata entry
        existing_meta[plot_id] = {
            'title': plot_title,
            'path': tree_path,
            'type': 'plot'
        }
        
        # Write meta.json first (polled by open browser tabs for live updates)
        _write_meta_json(plots_dir, existing_meta)
        
        # Embed sidecars < 500 KB inline so result plots load from file:// without HTTP server
        inline_data = _read_all_sidecars(plots_dir, existing_meta, max_bytes=500_000)
        html_content = create_archive_html(participant_id, existing_meta, inline_data)
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
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
        participant_id: Participant ID (or 'Global' for project logs)
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
    
    # Write log content to sidecar file
    archive_dir = os.path.dirname(archive_path)
    os.makedirs(archive_dir, exist_ok=True)
    plots_dir = os.path.join(archive_dir, 'plots')
    os.makedirs(plots_dir, exist_ok=True)
    log_id = f"{participant_id}_log_{log_name}"
    sidecar_path = os.path.join(plots_dir, f'{log_id}.js')
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write('window._EV_sidecar = window._EV_sidecar || {};\n')
        f.write(f'window._EV_sidecar[{json.dumps(log_id)}] = {json.dumps({"content": log_content})};\n')
    
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
        
        existing_meta = load_or_create_archive(archive_path, participant_id)
        existing_meta[log_id] = {
            'title': log_name,
            'path': [participant_id, log_name],
            'type': 'log'
        }
        
        _write_meta_json(plots_dir, existing_meta)
        
        inline_data = _read_all_sidecars(plots_dir, existing_meta, max_bytes=500_000)
        html_content = create_archive_html(participant_id, existing_meta, inline_data)
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return archive_path
    finally:
        release_file_lock(lock_file)
        lock_file.close()
        try:
            os.remove(lock_path)
        except:
            pass

def run(inp, out_dir, pre, project_name='procedure'):
    """Run interactive plotter for procedure visualization.
    
    Args:
        inp: Input _vis.parquet file
        out_dir: Output directory (parent results folder)
        pre: Prefix for output file
        project_name: Name of the project (default: 'procedure')
    """
    print(f"[interactive_plotter] Input: {inp}")
    
    try:
        df = pl.read_parquet(inp)
    except Exception as e:
        print(f"[interactive_plotter] ERROR: Failed to read {inp}: {e}")
        return
    
    # Extract participant ID from prefix (e.g., EV_002_xdf4_extr1_filt -> EV_002)
    parts = pre.split('_')
    if len(parts) >= 2:
        participant_id = '_'.join(parts[:2])
    else:
        participant_id = 'participant'
    
    # Ensure output directory exists
    os.makedirs(out_dir, exist_ok=True)
    
    # Project-level HTML archive (shared across all participants)
    archive_path = os.path.join(out_dir, f"{project_name}_interactive.html")
    
    try:
        # Create plot JSON
        plot_json, plot_title = create_plotly_json(df)
        
        # Determine tree path with participant folder
        tree_path = parse_filename_to_tree_path(pre, participant_id)
        
        # Update archive
        update_archive(archive_path, participant_id, pre, plot_json, plot_title or pre, tree_path)
        
        file_size = os.path.getsize(archive_path) / 1024  # KB
        print(f"[interactive_plotter] Updated archive: {archive_path} ({file_size:.1f} KB)")
        
    except Exception as e:
        print(f"[interactive_plotter] ERROR: Failed to update archive: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) >= 4:
        project_name = sys.argv[4] if len(sys.argv) >= 5 else 'procedure'
        run(sys.argv[1], sys.argv[2], sys.argv[3], project_name)
    else:
        print('[interactive_plotter] Create unified interactive HTML archive for procedure/QC visualization.')
        print('[interactive_plotter] Usage: interactive_plotter.py <input_vis.parquet> <output_dir> <prefix> [project_name]')
        sys.exit(1)

