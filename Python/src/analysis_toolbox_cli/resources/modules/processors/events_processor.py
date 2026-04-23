import polars as pl, fnmatch, sys, os, ast

# Logging helpers
def log_info(msg): print(f"[events] INFO: {msg}")
def log_warning(msg): print(f"[events] WARNING: {msg}")
def log_error(msg): print(f"[events] ERROR: {msg}")

safe_str = lambda x: str(x) if x is not None else ''
trig_to_str = lambda val: str(int(float(val)))  # Convert float triggers (e.g., 1.0) to string integers (e.g., '1')

def walk(node, parent=None):
    if isinstance(node, dict):
        if 'entry' in node and 'value' in node: yield (node, parent)
        if 'children' in node:
            for child in node['children']: yield from walk(child, node)
    elif isinstance(node, list):
        for item in node: yield from walk(item, parent)



# Unit normalization: detect if tree times are much larger than recording times (ms vs s)
normalize_rec_times = lambda rec_times, tree_time_sample: (
    [t * 1000 for t in rec_times] if tree_time_sample / max(rec_times + [1]) > 100 else rec_times
)

build_rec_map = lambda df, triggers, tree_time_ref: {
    trig: normalize_rec_times(
        [float(r[df.columns[0]]) for r in df.select([df.columns[0], df.columns[1] if len(df.columns) > 1 else df.columns[0]]).to_dicts() 
         if trig_to_str(r[df.columns[1] if len(df.columns) > 1 else df.columns[0]]) == str(trig)],
        tree_time_ref
    )
    for trig in triggers
}

global_offset = lambda pairs, rec_map: next((on - rec_map[trig][0] for _, trig, on, _, _ in pairs if trig in rec_map and rec_map[trig]), 0.0)

def find_neighbor(target_onset, offset, pairs, rec_map):
    estimated_time = target_onset - offset
    all_rec_times = sorted([(t, trig) for trig, times in rec_map.items() for t in times])
    if not all_rec_times: raise ValueError("[events] ERROR: No triggers in recording for neighbor search")
    neighbor_time, neighbor_trig = min(all_rec_times, key=lambda x: abs(x[0] - estimated_time))
    tree_onset = next((on for _, t, on, _, _ in pairs if t == neighbor_trig), None) or next((on for _, _, _, t, on in pairs if t == neighbor_trig), None)
    if tree_onset is None: raise ValueError(f"[events] ERROR: Neighbor trigger {neighbor_trig} not found in tree")
    local_offset = tree_onset - neighbor_time
    return target_onset - local_offset

def align_times_with_neighbor(pairs, rec_map, offset, _):
    result, used = [], set()
    if not rec_map or not any(rec_map.values()): 
        print("[events] Error: No triggers found in recording")
        print(f"[events] DEBUG: rec_map keys: {list(rec_map.keys()) if rec_map else 'None'}")
        raise ValueError("[events] Error: No triggers found in recording")
    for cond, st, st_on, sp, sp_on in pairs:
        st_cand = [t for t in rec_map.get(st, []) if t not in used]
        st_time = min(st_cand, key=lambda t: abs(t - (st_on - offset)), default=None) if st_cand else None
        if st_time is None: 
            try:
                st_time = find_neighbor(st_on, offset, pairs, rec_map)
            except Exception as e:
                print(f"[events] ERROR: Failed to find neighbor for start trigger {st} in {cond}: {e}")
                raise
        sp_cand = [t for t in rec_map.get(sp, []) if t not in used]
        sp_time = min(sp_cand, key=lambda t: abs(t - (sp_on - offset)), default=None) if sp_cand else None
        if sp_time is None: 
            try:
                sp_time = find_neighbor(sp_on, offset, pairs, rec_map)
            except Exception as e:
                print(f"[events] ERROR: Failed to find neighbor for stop trigger {sp} in {cond}: {e}")
                raise
        if not st_time or not sp_time: 
            print(f"[events] ERROR: Alignment failed for {cond} epoch (start_trig={st}, stop_trig={sp})")
            print(f"[events] DEBUG: st_time={st_time}, sp_time={sp_time}")
            raise ValueError(f"[events] Error: Alignment failed for {cond} epoch (start_trig={st}, stop_trig={sp})")
        if st_time in used or sp_time in used: 
            print(f"[events] ERROR: Timestamp collision for {cond} epoch (start_trig={st}, stop_trig={sp})")
            print(f"[events] DEBUG: st_time={st_time} in used: {st_time in used}, sp_time={sp_time} in used: {sp_time in used}")
            raise ValueError(f"[events] Error: Timestamp collision for {cond} epoch (start_trig={st}, stop_trig={sp})")
        used.update([st_time, sp_time])
        result.append((cond, st_time, sp_time))
    return result

extract_start_stop_pairs = lambda tree, pats: [
    (cond, st_trig, st_on, sp_trig, sp_on)
    for node, parent in walk(tree)
    for val in [safe_str(node.get('value', ''))]
    if any(fnmatch.fnmatch(val, pat) for pat in pats)
    for siblings in [parent.get('children', []) if parent and 'children' in parent else []]
    for node_idx in [next((i for i, s in enumerate(siblings) if s is node), -1)] if node_idx >= 0
    for has_trig in [any(not ('children' in s and s['children']) and 'trigger' in safe_str(s.get('entry', '')).lower() and safe_str(s.get('value', '')).isdigit() for s in siblings)]
    for has_onset in [any(not ('children' in s and s['children']) and any(fnmatch.fnmatch(safe_str(s.get('entry', '')).lower(), p) for p in ['*onset*', '*firstframe*']) and s.get('value') is not None for s in siblings)]
    if has_trig and has_onset
    for cond in [next((p.replace('*', '').upper() for p in pats if fnmatch.fnmatch(val, p)), val)]
    for st_trig_idx in [next((i for i, s in enumerate(siblings) if i > node_idx and not ('children' in s and s['children']) and 'trigger' in safe_str(s.get('entry', '')).lower() and safe_str(s.get('value', '')).isdigit()), None)] if st_trig_idx is not None
    for st_trig in [safe_str(siblings[st_trig_idx].get('value', ''))]
    for st_on in [next((float(s.get('value')) for s in siblings if not ('children' in s and s['children']) and any(fnmatch.fnmatch(safe_str(s.get('entry', '')).lower(), p) for p in ['*onset*', '*firstframe*']) and not any(x in safe_str(s.get('entry', '')).lower() for x in ['ack', 'delay']) and s.get('value') is not None), None)]
    if st_trig and st_on is not None
    for stop_sib in [next((s for s in siblings[st_trig_idx + 1:] if 'children' in s and s['children'] and any('trigger' in safe_str(n.get('entry', '')).lower() and safe_str(n.get('value', '')).isdigit() for n in s['children'] if 'entry' in n)), None)]
    for sp_trig in [next((safe_str(n.get('value', '')) for n in (stop_sib['children'] if stop_sib else []) if 'entry' in n and 'trigger' in safe_str(n.get('entry', '')).lower() and safe_str(n.get('value', '')).isdigit()), None)]
    for sp_on in [next((float(n.get('value')) for n in (stop_sib['children'] if stop_sib else []) if 'entry' in n and any(fnmatch.fnmatch(safe_str(n.get('entry', '')).lower(), p) for p in ['*onset*', '*firstframe*']) and not any(x in safe_str(n.get('entry', '')).lower() for x in ['ack', 'delay']) and n.get('value') is not None), None)]
    if sp_trig and sp_on is not None
]

if __name__ == '__main__': (lambda a:
    (lambda pats, tree, rec_df: (
        (lambda pairs: (
            (lambda tree_time_ref: (
                (lambda rec_map: (
                    (lambda offset: (
                        (lambda epochs: (
                            print(f"[events] Extracted {len(pairs)} epoch pairs from tree"),
                            [print(f"[events]   {c}: {len([p for p in pairs if p[0] == c])} epochs") for c in sorted(set(p[0] for p in pairs))],
                            print(f"[events] Found {sum(len(v) for v in rec_map.values())} triggers in recording"),
                            print(f"[events] DEBUG: Triggers in recording: {dict((k, len(v)) for k, v in rec_map.items())}"),
                            print(f"[events] Global offset: {offset:.1f} time units"),
                            print(f"[events] Aligned {len(epochs)} epochs: {', '.join(f'{c}({sum(1 for cond,_,_ in epochs if cond==c)})' for c in sorted(set(cond for cond,_,_ in epochs)))}"),
                            (lambda out_dir: (
                                pl.DataFrame({'data': [{c: [(st, sp) for cond, st, sp in epochs if cond == c] for c in sorted(set(cond for cond, _, _ in epochs))}]}).write_parquet(f"{out_dir}/{os.path.splitext(os.path.basename(a[1]))[0]}_events.parquet", compression='snappy'),
                                print(f"[events] Output: {out_dir}/{os.path.splitext(os.path.basename(a[1]))[0]}_events.parquet")
                            ))(os.path.dirname(a[1]) or '.')
                        )[-1])(list(align_times_with_neighbor(pairs, rec_map, offset, [])))
                    ))(global_offset(pairs, rec_map))
                ))(build_rec_map(rec_df, set([t for p in pairs for t in (p[1], p[3])]), tree_time_ref))
            ))(pairs[0][2] if pairs else 0)
        ))(extract_start_stop_pairs(tree, pats)) if extract_start_stop_pairs(tree, pats) else (
            print("[events] Error: No valid epoch pairs extracted"),
            print(f"[events] DEBUG: Patterns: {pats}"),
            print(f"[events] DEBUG: Tree has {sum(1 for _ in walk(tree))} nodes"),
            sys.exit(1)
        )[-1]
    ))(ast.literal_eval(a[3]), pl.read_parquet(a[2])['data'][0], pl.read_parquet(a[1]))
    if len(a) == 4 else (print('[events] Extract event start/stop times from tree structure, align with triggers.\nUsage: events_processor.py <trigger.parquet> <tree.parquet> <patterns_list>'), sys.exit(1))
)(sys.argv)
