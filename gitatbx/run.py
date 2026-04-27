"""Launch the Nextflow pipeline — finds the project anywhere on the system."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import get as cfg_get, set as cfg_set

_MAX_DEPTH = 6  # how deep to recurse when scanning roots


def _search_roots() -> list[Path]:
    """Return directories to search from: home + all accessible drive roots (Windows) or / (Unix)."""
    roots = [Path.home()]
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists() and drive not in roots:
                roots.append(drive)
    else:
        root = Path("/")
        if root not in roots:
            roots.append(root)
    return roots


def _find_analysis_dir(pattern: str) -> Path | None:
    """
    Walk search roots looking for a directory named <pattern>*_analysis that
    contains a *_pipeline.nf. Returns the analysis dir, or None if not found.
    """
    pat = pattern.lower()

    def _walk(path: Path, depth: int) -> Path | None:
        if depth == 0:
            return None
        try:
            for child in sorted(path.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                name_lower = child.name.lower()
                # Direct hit: matches pattern and is an _analysis dir with a pipeline
                if pat in name_lower and "_analysis" in name_lower:
                    if any(child.glob("*_pipeline.nf")):
                        return child
                # Could be a project root containing an _analysis subdir
                if pat in name_lower:
                    for sub in sorted(child.glob("*_analysis")):
                        if sub.is_dir() and any(sub.glob("*_pipeline.nf")):
                            return sub
                # Recurse
                found = _walk(child, depth - 1)
                if found:
                    return found
        except PermissionError:
            pass
        return None

    for root in _search_roots():
        found = _walk(root, _MAX_DEPTH)
        if found:
            return found
    return None


def _find_pipeline_files(directory: Path) -> tuple[Path, Path] | None:
    pipelines = sorted(directory.glob("*_pipeline.nf"))
    configs   = sorted(directory.glob("*_parameters.config"))
    if not pipelines or not configs:
        return None
    pipeline = pipelines[0]
    prefix = pipeline.stem.replace("_pipeline", "")
    config = next((c for c in configs if c.stem.startswith(prefix)), configs[0])
    return pipeline, config


def run_pipeline(args) -> int:
    pattern = args.project.strip() if args.project else ""

    if not pattern:
        # No pattern — run from cwd or its *_analysis subdir
        directory = Path(".").resolve()
        if not any(directory.glob("*_pipeline.nf")):
            subs = sorted(directory.glob("*_analysis"))
            if subs:
                directory = subs[0]
    else:
        # Check config cache first
        cache: dict = cfg_get("project_paths") or {}
        cached = cache.get(pattern.lower())
        if cached and Path(cached).exists() and any(Path(cached).glob("*_pipeline.nf")):
            directory = Path(cached)
            print(f"Found (cached): {directory}")
        else:
            print(f"Searching for project matching '{pattern}'...")
            directory = _find_analysis_dir(pattern)
            if directory is None:
                print(f"Error: no project matching '{pattern}' found.", file=sys.stderr)
                return 1
            # Cache for next time
            cache[pattern.lower()] = str(directory)
            cfg_set("project_paths", cache)
            print(f"Found: {directory}")

    found = _find_pipeline_files(directory)
    if found is None:
        print(f"Error: could not find pipeline + config in {directory}", file=sys.stderr)
        return 1

    pipeline, config = found
    cmd = ["nextflow", "run", pipeline.name, "-c", config.name]
    if args.resume:
        cmd.append("-resume")

    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(directory))
    return result.returncode
