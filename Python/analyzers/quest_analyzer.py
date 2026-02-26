import sys, ast, os, re, fnmatch, polars as pl, statistics, math

# Parsing helpers
parse_param = lambda s: s if not isinstance(s, str) else (ast.literal_eval(open(s[1:], 'r', encoding='utf-8').read()) if s.startswith('@') else ast.literal_eval(s)) if s.startswith(('@', '[', '{')) else ast.literal_eval(re.sub(r"(?P<pre>(?:\A|\[|,)\s*)(?P<tok>[A-Za-z0-9_\-]+:\s*[^,\]\}]+)", lambda m: f"{m.group('pre')}'{m.group('tok')}'", s))
flatten = lambda p: [x for el in p for x in (flatten(el) if isinstance(el, list) else [el])]

# Tree navigation
def get_prop(n, k):
    """Get property k from node n (checks entries, then recurses into structural children)."""
    for c in n.get('children', []):
        if c.get('entry') == k: return c.get('value')
    for c in n.get('children', []):
        if not c.get('entry') and c.get('children') and (v := get_prop(c, k)) is not None: return v
    return None

get_structural = lambda n: [c for c in n.get('children', []) if not c.get('entry') and c.get('children')]
collect_all = lambda nodes: [x for n in nodes for x in [n] + collect_all(get_structural(n))]
get_branches = lambda nodes, k, pat=None: [n for n in nodes if not n.get('entry') and n.get('children') and (v := get_prop(n, k)) is not None and (pat is None or fnmatch.fnmatch(str(v), pat))]

# Stats helpers
to_float = lambda v: float('nan') if v == '' else float(v)
valid = lambda vals: [v for v in vals if not math.isnan(v)]
stats = lambda vals: (statistics.mean(vals) if vals else None, statistics.stdev(vals) if len(vals) > 1 else None)

def make_y_ticks(y_labels, y_max, y_extracted=None):
    """Build y_ticks - keep as simple types for parquet compatibility."""
    if y_max: return y_max  # Just pass the max, plotter handles endpoint labels from y_labels
    if isinstance(y_labels, list): return y_labels
    return y_extracted

def aggregate(conds, labels=None):
    """Aggregate values per category, return (x_data, y_data, y_var, counts)."""
    keys = list(range(1, len(labels)+1)) if labels else sorted(conds.keys(), key=lambda x: (int(x) if str(x).isdigit() else x, x))
    x_data, y_data, y_var, counts = [], [], [], []
    for k in keys:
        vals = valid(conds.get(k, []))
        x_data.append(labels[k-1] if labels else k)
        m, s = stats(vals)
        y_data.append(m); y_var.append(s); counts.append(len(conds.get(k, [])))
    return x_data, y_data, y_var, counts

def get_pos(n, x_pos_key, fallback_keys):
    """Extract position index from node using explicit key or fallbacks."""
    keys = [x_pos_key] if x_pos_key else fallback_keys
    for k in keys:
        if k and (v := get_prop(n, k)) is not None:
            try: return int(v)
            except: pass
    return None

def analyze(ip, param_str, out_prefix):
    print(f"[quest] Processing: {ip}")
    root = pl.read_parquet(ip)['data'][0]; param = flatten(parse_param(param_str))
    if not isinstance(param[-1], dict): raise RuntimeError('Last param must be dict')
    inner = param[-1]; d_key = inner.get('data')
    if not d_key: raise RuntimeError('Need data key')
    
    # Parse axis config
    x_raw = inner.get('x-labels') or inner.get('x_labels')
    y_labels = inner.get('y-labels') or inner.get('y_labels')
    y_max = inner.get('y-max') or inner.get('y_max')
    
    # Handle tuple format: ('posKey', label_source)
    # label_source can be: list ['Label1', ...] or string 'fieldName' to extract from tree
    x_pos_key, x_label_src = (None, None)
    if isinstance(x_raw, (list, tuple)) and len(x_raw) == 2 and isinstance(x_raw[0], str):
        x_pos_key = x_raw[0]
        x_label_src = x_raw[1]  # Either a list or a field name string
    
    x_labels = x_label_src if isinstance(x_label_src, list) else None
    x_label_field = x_label_src if isinstance(x_label_src, str) else None
    x_key = inner.get('x-axis') or inner.get('x_axis') or (x_raw if isinstance(x_raw, str) else None)
    y_keys = inner.get('y-axis') or inner.get('y_axis')
    x_is_cat = isinstance(x_labels, list)
    x_is_dynamic = x_pos_key is not None and x_label_field is not None  # Labels from tree field
    
    if not x_key and not x_is_cat and not x_is_dynamic: raise RuntimeError('Need x-axis or x-labels')
    
    # Separate condition patterns from selectors
    cond_pats = [el for el in param[:-1] if isinstance(el, str) and ':' not in el]
    selectors = [el for el in param[:-1] if isinstance(el, str) and ':' in el]
    
    # Collect structural nodes from root
    nodes = collect_all(get_structural(root))
    
    # Apply selectors
    for i, sel in enumerate(selectors):
        k, p = [s.strip() for s in sel.split(':', 1)]
        nodes = get_branches(nodes, k, p)
        print(f"[quest] Selector '{sel}' matched {len(nodes)} branches")
        if i < len(selectors) - 1: nodes = [c for n in nodes for c in get_structural(n)]
    
    def extract_data(target_nodes):
        """Extract and aggregate data from nodes. Fails explicitly if structure doesn't match."""
        fallback_keys = ['be7List', 'ea11List', 'samList', 'panasList', 'bisBasList']
        n_labels = len(x_labels) if isinstance(x_labels, list) else 0
        conds: dict = {}
        label_map: dict = {}  # pos -> label (for dynamic labels from tree field)
        skipped_no_pos, skipped_out_of_range = 0, 0
        
        for n in target_nodes:
            dv = get_prop(n, d_key)
            if dv is None: continue
            
            if x_is_cat and n_labels > 0:
                # Categorical with explicit label list
                pos = get_pos(n, x_pos_key, fallback_keys)
                if not pos:
                    skipped_no_pos += 1
                elif pos < 1 or pos > n_labels:
                    skipped_out_of_range += 1
                else:
                    conds.setdefault(pos, []).append(to_float(dv))
            elif x_is_dynamic:
                # Dynamic labels: position from x_pos_key, label from x_label_field
                pos = get_pos(n, x_pos_key, fallback_keys)
                if not pos:
                    skipped_no_pos += 1
                else:
                    conds.setdefault(pos, []).append(to_float(dv))
                    if pos not in label_map and x_label_field:
                        lbl = get_prop(n, x_label_field)
                        if lbl: label_map[pos] = str(lbl)
            else:
                # Numeric x-axis
                xv = get_prop(n, x_key)
                if xv is not None: conds.setdefault(str(xv), []).append(to_float(dv))
        
        # Report issues
        if skipped_no_pos > 0:
            print(f"[quest] Warning: {skipped_no_pos} values skipped - no position key '{x_pos_key}' found")
        if skipped_out_of_range > 0:
            print(f"[quest] Warning: {skipped_out_of_range} values skipped - position out of range [1, {n_labels}]")
        
        # Build ordered labels for dynamic case
        if x_is_dynamic:
            sorted_pos = sorted(label_map.keys())
            # Check for missing labels
            missing_labels = [p for p in conds.keys() if p not in label_map]
            if missing_labels:
                print(f"[quest] Warning: {len(missing_labels)} positions have data but no label: {missing_labels[:5]}...")
            dynamic_labels = [label_map[p] for p in sorted_pos]
            # Remap conds to 1-indexed for aggregate
            conds = {i+1: conds.get(sorted_pos[i], []) for i in range(len(sorted_pos))}
            return aggregate(conds, dynamic_labels)
        
        return aggregate(conds, x_labels if x_is_cat else None)
    
    base = os.path.splitext(os.path.basename(ip))[0]
    
    if cond_pats:
        # Condition-based analysis (grid plots)
        print(f"[quest] Condition patterns: {cond_pats}")
        folder = os.path.join(os.getcwd(), f"{base}_{out_prefix}")
        os.makedirs(folder, exist_ok=True)
        
        count = 0
        for idx, pat in enumerate(cond_pats, 1):
            cond = pat.replace('*', '').upper()
            parents = [n for n in nodes if any(c.get('entry') and fnmatch.fnmatch(str(c.get('value', '')), pat) for c in n.get('children', []) if c.get('entry'))]
            nephews = [c for p in parents for c in get_structural(p)]
            print(f"[quest]   '{pat}' -> {cond}: {len(parents)} parents, {len(nephews)} nephews")
            
            if not nephews: print(f"[quest] Warning: No nephews for '{pat}'"); continue
            
            x_data, y_data, y_var, counts = extract_data(nephews)
            out = {'x_data': x_data, 'y_data': y_data, 'y_var': y_var, 'y_ticks': make_y_ticks(y_labels, y_max), 'y_labels': y_labels if isinstance(y_labels, list) else None, 'plot_type': 'grid', 'counts_per_x': counts, 'count': sum(counts), 'condition': cond}
            path = os.path.join(folder, f"{base}_{out_prefix}{idx}.parquet")
            pl.DataFrame([out]).write_parquet(path)
            print(f"[quest]   Output: {path} | {sum(counts)} values, {len(x_data)} categories | {cond}")
            count += 1
        
        # Create procedure visualization file
        plot_files = [os.path.join(folder, f"{base}_{out_prefix}{idx}.parquet") for idx in range(1, count+1)]
        if all(os.path.exists(f) for f in plot_files):
            try:
                all_plots = [pl.read_parquet(f) for f in plot_files]
                combined = pl.concat(all_plots)
                vis_path = os.path.join(os.getcwd(), f"{base}_{out_prefix}_vis.parquet")
                combined.write_parquet(vis_path)
                print(f"[quest] Created procedure visualization: {vis_path}")
            except Exception as e:
                print(f"[quest] WARNING: Could not create procedure visualization: {e}")
        
        signal = os.path.join(os.getcwd(), f"{base}_{out_prefix}.parquet")
        pl.DataFrame([{'signal': 1, 'source': os.path.basename(ip), 'conditions': count, 'folder_path': os.path.abspath(folder)}]).write_parquet(signal)
        print(f"[quest] Signal: {signal} | {count} conditions"); print(signal)
        return signal
    else:
        # Non-condition analysis (bar plots) - PANAS/BISBAS without conditions
        print(f"[quest] No condition patterns - extracting data")
        all_nodes = collect_all(nodes)
        
        if x_is_cat or x_is_dynamic:
            # Use same logic as condition path - find all nodes with data
            # For non-condition, we search all nodes that have the data key
            target_nodes = [n for n in all_nodes if get_prop(n, d_key) is not None]
            print(f"[quest] Found {len(target_nodes)} nodes with data")
            x_data, y_data, y_var, counts = extract_data(target_nodes)
        else:
            # Legacy path for old x-axis field format
            y_keys_set = set(y_keys) if isinstance(y_keys, (list, tuple)) else {y_keys} if y_keys else set()
            parents = [n for n in all_nodes if any(c.get('entry') == x_key or c.get('entry') in y_keys_set for c in n.get('children', []))]
            print(f"[quest] Found {len(parents)} parent nodes")
            
            conds: dict = {}
            y_extracted = None
            y_keys_list = list(y_keys) if isinstance(y_keys, (list, tuple)) else [y_keys] if y_keys else []
            for p in parents:
                children = {c.get('entry'): c.get('value') for c in p.get('children', []) if c.get('entry')}
                xv = str(children.get(x_key)) if x_key and x_key in children else None
                dv = children.get(d_key)
                if xv and dv is not None: conds.setdefault(xv, []).append(float(dv))
                if y_extracted is None and y_keys_list: y_extracted = [str(children[k]) for k in y_keys_list if k in children and children[k]]
            
            print(f"[quest] Extracted: {len(conds)} x-values, {sum(len(v) for v in conds.values())} points")
            x_data, y_data, y_var, counts = aggregate(conds)
        
        out = {'x_data': x_data, 'y_data': y_data, 'y_var': y_var, 'y_ticks': make_y_ticks(y_labels, y_max), 'y_labels': y_labels if isinstance(y_labels, list) else None, 'plot_type': 'bar', 'counts_per_x': counts, 'count': sum(counts)}
        path = os.path.join(os.getcwd(), f"{base}_{out_prefix}.parquet")
        plot_df = pl.DataFrame([out])
        plot_df.write_parquet(path)
        # Create procedure visualization
        vis_path = path.replace('.parquet', '_vis.parquet')
        plot_df.write_parquet(vis_path)
        print(f"[quest] Output: {path} | {sum(counts)} values, {len(x_data)} categories"); print(path)
        return path

if __name__ == '__main__': (lambda a: analyze(a[1], a[2], a[3]) if len(a) >= 4 else (print("Parse and aggregate questionnaire responses. Plot-ready output.\n[QUEST] Usage: quest_analyzer.py <input.parquet> <param> <prefix>"), sys.exit(1)))(sys.argv)
