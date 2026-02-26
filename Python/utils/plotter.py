import polars as pl, matplotlib.pyplot as plt, sys, os, shutil, tempfile, re
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

sanitize = lambda v: re.sub(r"[^A-Za-z0-9._-]", "_", str(v))
truncate = lambda s, max_len=40: (s if len(s) <= max_len else s[:max_len-3] + '...') if isinstance(s, str) else s
safe_yerr = lambda yerr: [np.nan if x is None else x for x in yerr] if yerr else None
attach = lambda t, o, i: ((lambda r, w: ([w.add_page(p) for p in r.pages], w.add_metadata({"/Producer": "EmotiView", "/Conformance": "/PDF/A-1b"}), w.add_attachment(os.path.basename(i), open(i, 'rb').read()), (lambda f: (w.write(f), f.close()))(open(o, 'wb')), os.remove(t), o))(__import__('pypdf').PdfReader(t), __import__('pypdf').PdfWriter()) if __import__('importlib').util.find_spec('pypdf') else (shutil.move(t, o), o)[-1])

def to_lst(x):
    """Flatten single-element nested lists, convert polars Series, ensure list output."""
    if isinstance(x, pl.Series):
        return x.to_list()
    if isinstance(x, list):
        while isinstance(x, list) and len(x) == 1 and isinstance(x[0], (list, np.ndarray)):
            x = x[0]
        return x if isinstance(x, list) else list(x) if hasattr(x, '__iter__') and not isinstance(x, str) else [x]
    return []

def apply_y_limits(ax, row, global_lim=None, plot_type='bar'):
    """Apply Y-axis limits based on y_ticks/y_labels configuration."""
    yt, yl = row.get('y_ticks'), row.get('y_labels')
    if yl and isinstance(yl, (list, tuple)) and len(yl) > 2:
        # Full labels list (PANAS, BISBAS)
        ax.set_ylim(0.5, len(yl) + 0.5)
        if plot_type == 'grid':
            ax.set_yticks(list(range(1, len(yl) + 1)))
            ax.set_yticklabels(yl, fontsize=9)
    elif yt and isinstance(yt, (int, float)):
        if yl and isinstance(yl, (list, tuple)) and len(yl) == 2:
            # Endpoint labels
            ax.set_ylim(0.5, yt + 0.5)
            if plot_type == 'grid':
                ax.set_yticks(list(range(1, int(yt) + 1)))
                ytick_labels = [''] * int(yt)
                ytick_labels[0], ytick_labels[-1] = str(yl[0]), str(yl[1])
                ax.set_yticklabels(ytick_labels, fontsize=9)
        elif yl and isinstance(yl, (list, tuple)) and len(yl) == 3:
            # 3 labels: bottom, middle, top
            if plot_type == 'grid':
                ax.set_yticks(list(range(1, int(yt) + 1)))
                ytick_labels = [''] * int(yt)
                ytick_labels[0] = str(yl[0])
                ytick_labels[(int(yt) - 1) // 2] = str(yl[1])
                ytick_labels[-1] = str(yl[2])
                ax.set_yticklabels(ytick_labels, fontsize=9)
        else:
            # Numeric symmetric limit
            ax.set_ylim(-abs(yt), abs(yt)) if plot_type == 'grid' else ax.set_ylim(0, yt)
    elif global_lim:
        ax.set_ylim(global_lim)

def style_axis(ax, xlabel='', ylabel='', show_grid=True):
    """Apply consistent styling to axis."""
    ax.set_xlabel(xlabel, fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    if show_grid:
        ax.grid(True, alpha=0.25, linestyle='--', linewidth=0.8)
    [ax.spines[s].set_visible(False) for s in ['top', 'right']]
    [ax.spines[s].set_linewidth(1.2) for s in ['left', 'bottom']]

def plot_single_panel(ax, x_data, y_data, y_var, plot_type, row, label=None, global_lim=None, global_x_lim=None):
    """Plot a single panel (for grid layout or single plot)."""
    xd, yd = to_lst(x_data), to_lst(y_data)
    yv = to_lst(y_var) if y_var else None
    
    if plot_type == 'grid':
        # Bar chart
        ax.bar(range(len(yd)), yd, yerr=safe_yerr(yv) if yv else None, color='dimgray', alpha=0.85, capsize=4, error_kw={'linewidth': 1.5})
        ax.set_xticks(range(len(xd)))
        ax.set_xticklabels([truncate(str(x)) for x in xd], rotation=45, ha='right', fontsize=9)
        ax.xaxis.set_ticks_position('bottom')
        ax.tick_params(axis='x', which='both', bottom=True, labelbottom=True)
        ax.spines['bottom'].set_position(('axes', 0))
    elif plot_type == 'line_grid':
        # Line chart
        ax.plot(xd, yd, linewidth=2.5, alpha=0.85, color='dimgray')
        if yv and all(v is not None for v in yv):
            ax.fill_between(xd, [y - e for y, e in zip(yd, yv)], [y + e for y, e in zip(yd, yv)], alpha=0.3, color='dimgray')
        if global_x_lim:
            ax.set_xlim(global_x_lim)
    
    apply_y_limits(ax, row, global_lim, plot_type)
    if label:
        ax.set_title(label, fontsize=14, fontweight='bold')
    style_axis(ax, row.get('x_label', ''), row.get('y_label', ''))

def plot(df, pdf_path):
    """Generic plotter: handles concatenated data from concatenating_processor."""
    print(f"[plotter] Plotting started: {pdf_path}")
    pdf = PdfPages(pdf_path)
    row = df.to_dicts()[0]
    
    if 'x_data' not in row or 'y_data' not in row:
        print(f"[plotter] ERROR: Missing required fields (x_data/y_data), available: {list(row.keys())}")
        print(f"[plotter] Cannot plot - closing PDF and exiting")
        pdf.close()
        return
    
    x_data, y_data, y_var, labels = to_lst(row['x_data']), to_lst(row['y_data']), to_lst(row.get('y_var', [])), to_lst(row.get('labels', []))
    raw_plot_type = row.get('plot_type', 'bar')
    if not raw_plot_type:
        raw_plot_type = 'bar'
    plot_type = raw_plot_type[0] if isinstance(raw_plot_type, list) else raw_plot_type
    plot_type = plot_type[0] if isinstance(plot_type, list) else plot_type
    
    is_concat_y = y_data and isinstance(y_data[0], (list, tuple))
    is_concat_x = x_data and isinstance(x_data[0], (list, tuple))
    is_concat = is_concat_y or is_concat_x
    lbl = lambda i: labels[i] if i < len(labels) else f'Dataset {i+1}'
    
    print(f"[plotter] Plot type: {plot_type}, Concatenated: {is_concat} (x={is_concat_x}, y={is_concat_y}), Labels: {labels if labels else 'none'}")
    
    if (plot_type in ('line_grid', 'grid')) and is_concat:
        # Grid layout
        n_plots = len(y_data) if is_concat_y else len(x_data)
        n_cols, n_rows = min(3, n_plots), (n_plots + 2) // 3
        print(f"[plotter] Creating grid: {n_rows}x{n_cols} for {n_plots} conditions")
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(6*n_cols, 4*n_rows + 0.5))
        axes = axes.flatten() if n_plots > 1 else [axes]
        
        # Calculate global limits for consistent scaling
        all_x, all_y = [], []
        for i in range(n_plots):
            xd = x_data[i] if is_concat_x else x_data
            yd = y_data[i]
            all_x.extend([v for v in to_lst(xd) if isinstance(v, (int, float))])
            all_y.extend([v for v in to_lst(yd) if isinstance(v, (int, float))])
        
        global_y_lim = None
        if all_y:
            y_min, y_max = float(min(all_y)), float(max(all_y))
            global_y_lim = (y_min - (y_max - y_min) * 0.1, y_max + (y_max - y_min) * 0.1)
        
        global_x_lim = None
        if plot_type == 'line_grid' and all_x:
            x_min, x_max = float(min(all_x)), float(max(all_x))
            global_x_lim = (x_min - (x_max - x_min) * 0.05, x_max + (x_max - x_min) * 0.05)
        
        for i in range(n_plots):
            xd = x_data[i] if is_concat_x else x_data
            yd, yv = y_data[i], (y_var[i] if y_var and i < len(y_var) else None)
            plot_single_panel(axes[i], xd, yd, yv, plot_type, row, lbl(i), global_y_lim, global_x_lim)
        
        [axes[i].set_visible(False) for i in range(n_plots, len(axes))]
        fig.tight_layout(rect=(0, 0.05, 1, 1))
    else:
        # Single plot
        fig, ax = plt.subplots(figsize=(12, 6))
        colors = ['dimgray', 'darkgray', 'gray', 'lightgray', 'silver']
        
        if is_concat:
            if plot_type == 'line':
                # Multi-channel line plots with offset for many channels
                n_ch = len(y_data)
                if n_ch > 5:
                    all_vals = [v for yd in y_data for v in to_lst(yd)]
                    offset_step = float((max(all_vals) - min(all_vals)) * 1.2) if all_vals else 1.0
                    for i, (xd, yd) in enumerate(zip(x_data, y_data)):
                        ax.plot(to_lst(xd), np.array(to_lst(yd)) + float(i * offset_step), linewidth=0.5, alpha=0.7, color='dimgray')
                    if n_ch <= 50:
                        for i in range(n_ch):
                            ax.text(1.01, float(i * offset_step), lbl(i), transform=ax.get_yaxis_transform(), fontsize=6, va='center', ha='left')
                    ax.set_ylabel(f'{n_ch} channels (offset)', fontsize=10)
                    ax.set_yticks([])
                else:
                    [ax.plot(to_lst(xd), to_lst(yd), linewidth=1.0, label=lbl(i), alpha=0.85, color=colors[i % len(colors)]) for i, (xd, yd) in enumerate(zip(x_data, y_data))]
                    ax.legend(loc='upper right', fontsize=11, framealpha=0.95, edgecolor='gray')
            elif plot_type == 'scatter':
                [ax.scatter(to_lst(xd), to_lst(yd), s=50, label=lbl(i), alpha=0.7, color=colors[i % len(colors)]) for i, (xd, yd) in enumerate(zip(x_data, y_data))]
                ax.legend(loc='upper right', fontsize=11, framealpha=0.95, edgecolor='gray')
            elif plot_type == 'bar':
                cats = to_lst(x_data[0]) if is_concat_x else x_data
                n_cond, w = len(labels), 0.75 / max(len(cats), 1)
                for j, cat in enumerate(cats):
                    vals = [float(to_lst(y_data[i])[j]) if j < len(to_lst(y_data[i])) else 0.0 for i in range(n_cond)]
                    errs = [float(to_lst(y_var[i])[j]) if y_var and i < len(y_var) and j < len(to_lst(y_var[i])) else 0.0 for i in range(n_cond)]
                    ax.bar([i + (j - len(cats)/2 + 0.5) * w for i in range(n_cond)], vals, width=w, label=str(cat), yerr=safe_yerr(errs) if y_var else None, color=colors[j % len(colors)], alpha=0.85, capsize=4, error_kw={'linewidth': 1.5})
                ax.set_xticks(range(n_cond))
                ax.set_xticklabels([str(lbl) for lbl in labels], rotation=45, ha='right', fontsize=11)
                ax.legend(loc='upper right', fontsize=10, framealpha=0.95, edgecolor='gray')
        else:
            # Non-concatenated
            if plot_type == 'line':
                ax.plot(x_data, y_data, linewidth=2.5, alpha=0.85, color='dimgray')
            elif plot_type == 'scatter':
                ax.scatter(x_data, y_data, s=50, alpha=0.7, color='dimgray')
            else:  # bar
                ax.bar(range(len(y_data)), [float(v) for v in y_data], yerr=safe_yerr(y_var) if y_var else None, color='dimgray', alpha=0.85, capsize=4, error_kw={'linewidth': 1.5})
                ax.set_xticks(range(len(x_data)))
                ax.set_xticklabels([truncate(str(x)) for x in x_data], rotation=45, ha='right', fontsize=10)
        
        style_axis(ax, row.get('x_axis') or row.get('x_label', ''), row.get('y_axis') or row.get('y_label', ''))
        apply_y_limits(ax, row)
        fig.tight_layout(rect=(0, 0.05, 1, 1))
    
    pdf.savefig(fig, bbox_inches='tight', dpi=300)
    plt.close(fig)
    pdf.close()
    print(f"[plotter] Plotting finished: {pdf_path}")
    return pdf_path

def run(inp, out_dir, pre):
    """Run plotter to create final result PDFs with embedded data.
    
    This plotter creates publication-ready PDFs with embedded parquet data.
    Procedure/QC plots are handled by interactive_plotter.py.
    
    Args:
        inp: Input parquet file
        out_dir: Output directory (participant folder)
        pre: Prefix for output files
    """
    print(f"[plotter] Input: {inp}, Output dir: {out_dir}, Prefix: {pre}")
    df = pl.read_parquet(inp)

    # Always output flat in the provided directory (final results)
    os.makedirs(out_dir, exist_ok=True)
    tf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    tf_path = tf.name
    tf.close()
    
    # Create plot - returns None if failed
    plot_result = plot(df, tf_path)
    
    # Check if plot was created successfully (file size > 0)
    if not os.path.exists(tf_path) or os.path.getsize(tf_path) == 0:
        print(f"[plotter] ERROR: Plot creation failed, no valid PDF generated")
        if os.path.exists(tf_path):
            os.remove(tf_path)
        sys.exit(1)
    
    out_pdf = os.path.join(out_dir, f"{pre}.pdf")
    
    # Attach data to PDF for final results (plotter always creates full PDFs with embedded data)
    comb_pq = os.path.join(os.getcwd(), f"{sanitize(pre)}_data.parquet")
    df.write_parquet(comb_pq)
    # Attach data to PDF if pypdf available, then clean up temp files
    if __import__('importlib').util.find_spec('pypdf'):
        attach(tf_path, out_pdf, comb_pq)
        os.remove(comb_pq)  # Remove temp data file after embedding in PDF
    else:
        shutil.copy2(tf_path, out_pdf)
        os.remove(tf_path)
    # Write signal file in workspace root for nextflow
    sig = f"{sanitize(pre)}_plot.parquet"
    pl.DataFrame({'signal': [1], 'source_parquet': [os.path.basename(inp)], 'output_prefix': [pre], 'pdf_path': [out_pdf]}).write_parquet(sig + '.tmp')
    os.replace(sig + '.tmp', sig)
    print(f"[plotter] Output PDF: {out_pdf}")
    print(f"[plotter] Signal file: {sig}")
    print(sig)
    return out_pdf

if __name__ == '__main__': (lambda a: run(a[1], a[2], a[3] if len(a) > 3 else "plot") if len(a) >= 3 else (print("Usage: python plotter.py <input.parquet> <output_dir> [prefix]"), sys.exit(1)))(sys.argv)
