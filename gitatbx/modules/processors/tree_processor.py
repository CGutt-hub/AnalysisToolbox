import polars as pl, sys, os

class TreeNode:
    def __init__(self, entry=None, value=None): self.entry, self.value, self.children = entry, value, []

tree_to_struct = lambda n: {'entry': n.entry, 'value': n.value, 'children': [tree_to_struct(c) for c in n.children] if n.children else []}
def preprocess_run(ip, entry_delim, depth_key, kv_delim):
    print(f"[tree] Processing: {ip}")
    # Step 1: Detach entries using entry delimiter
    raw = pl.read_parquet(ip).to_series(0).to_list()
    entries = []
    for block in raw:
        entries.extend([e for e in block.split(entry_delim) if e.strip()])
    print(f"[tree] Processing {len(entries)} entries")
    # Step 2: Create flat struct list
    flat_structs = [{'key': (k := entry.split(kv_delim, 1))[0].strip(), 'value': k[1].strip(), 'raw': entry} if kv_delim in entry else {'key': None, 'value': None, 'raw': entry} for entry in entries]
    # Step 3: Build tree using depth delimiter and rules
    root = TreeNode(entry=None)
    anon = TreeNode(entry=None)
    root.children.append(anon)
    stack = [root, anon]
    prev_level = None
    for fs in flat_structs:
        if fs['key'] == depth_key and fs['value'] and fs['value'].isdigit():
            level = int(fs['value'])
            # Create new ANON entity with Level property as first leaf
            entity = TreeNode(entry=None)
            level_prop = TreeNode(entry=fs['key'], value=fs['value'])
            entity.children.append(level_prop)
            
            if prev_level is None:
                stack[-1].children.append(entity)
                stack.append(entity)
            elif level == prev_level + 1:
                stack[-1].children.append(entity)
                stack.append(entity)
            elif level == prev_level:
                stack.pop()
                stack[-1].children.append(entity)
                stack.append(entity)
            elif level < prev_level:
                while len(stack) > 2 and prev_level > level:
                    stack.pop()
                    prev_level -= 1
                stack.pop()
                stack[-1].children.append(entity)
                stack.append(entity)
            prev_level = level
        else:
            prop_node = TreeNode(entry=fs['key'], value=fs['value'])
            stack[-1].children.append(prop_node)
    print(f"[tree] Built tree: root with {len(root.children)} children")
    
    # Ensure first entity has Level: 1 marker if missing
    if root.children:
        # Find first branching child (entity)
        first_entity = next((c for c in root.children if c.children), None)
        if first_entity:
            # Check if it has a Level property leaf
            has_level = any(c.entry == depth_key and not c.children for c in (first_entity.children or []))
            if not has_level:
                # Add Level: 1 property leaf
                level_1_leaf = TreeNode(entry=depth_key, value='1')
                first_entity.children.insert(0, level_1_leaf)
                print(f"[tree] Added Level: 1 property to first entity")
    
    # Temporal reordering with proper parent-child matching
    get_onset = lambda n: next((float(c.value) for c in (n.children or []) if c.entry and ('onset' in c.entry.lower() or 'firstframe' in c.entry.lower()) and 'time' in c.entry.lower() and 'ack' not in c.entry.lower() and 'delay' not in c.entry.lower() and c.value), None) or next((t for c in (n.children or []) if (t := get_onset(c)) != float('inf')), float('inf'))
    
    # Collect ANON entities by their Level property leaf
    def collect_nodes_by_level(node, collected=None):
        if collected is None:
            collected = {}
        
        # Check if this is an ANON entity with Level property leaf
        if node.children and not node.entry:
            # Look for Level property leaf (first child typically)
            for child in node.children:
                if not child.children and child.entry == depth_key and child.value and child.value.isdigit():
                    level = int(child.value)
                    if level not in collected:
                        collected[level] = []
                    collected[level].append(node)
                    break
        
        # Recurse on all branching children
        for child in (node.children or []):
            if child.children:
                collect_nodes_by_level(child, collected)
        
        return collected
    
    nodes_by_level = collect_nodes_by_level(root)
    max_level = max(nodes_by_level.keys()) if nodes_by_level else 0
    print(f"[tree] Found levels: {sorted(nodes_by_level.keys())}, max={max_level}")
    
    # Reorder each level starting from 1
    for level in range(1, max_level + 1):
        if level not in nodes_by_level or not nodes_by_level[level]:
            continue
        
        children = nodes_by_level[level]
        parents = nodes_by_level[level - 1] if level > 1 else [root]
        
        # Skip if children have no timestamps
        if any(get_onset(c) == float('inf') for c in children):
            print(f"[tree] Warning: Skipping Level {level} - {len(children)} children have no timestamps")
            continue
        
        # Check if parents have timestamps
        parents_have_onset = level == 1 or all(get_onset(p) != float('inf') for p in parents)
        
        if not parents_have_onset:
            # Parents have no timestamps: reorder children within their original parent
            print(f"[tree] Level {level}: sibling sort ({len(children)} children)")
            for p in parents:
                p_children, p_leaves = [c for c in (p.children or []) if c in children], [c for c in (p.children or []) if not c.children]
                p.children = p_leaves + [c for _, c in sorted([(get_onset(c), c) for c in p_children], key=lambda x: x[0])]
        else:
            # Parents have timestamps: detach, reorder, reconnect based on temporal sequence
            print(f"[tree] Level {level}: temporal reparenting ({len(children)} children)")
            parent_leaves = {id(p): [c for c in (p.children or []) if not c.children] for p in parents}
            for p in parents: p.children = parent_leaves[id(p)][:]
            sorted_children, sorted_parents = sorted([(get_onset(c), c) for c in children], key=lambda x: x[0]), sorted([(get_onset(p), p) for p in parents], key=lambda x: x[0])
            pidx = 0
            for co, ch in sorted_children:
                while pidx < len(sorted_parents) - 1 and co >= sorted_parents[pidx + 1][0]: pidx += 1
                sorted_parents[pidx][1].children.append(ch)
    
    print(f"[tree] Globally reordered {max_level} levels by temporal onset")
    
    base = os.path.splitext(os.path.basename(ip))[0]
    out = f"{base}_tree.parquet"
    pl.DataFrame({'data': [tree_to_struct(root)]}).write_parquet(out, compression='gzip')
    print(f"[tree] Output: {out}")
    return out
if __name__ == '__main__': (lambda a: preprocess_run(a[1], a[2], a[3], a[4]) if len(a) == 5 else (print("[tree] Parse hierarchical tree from text, reorder by temporal onset.\nUsage: tree_processor.py <input.parquet> <entry_delim> <depth_delim> <kv_delim>"), sys.exit(1)))(sys.argv)