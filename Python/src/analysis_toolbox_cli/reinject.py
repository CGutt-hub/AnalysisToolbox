from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def _read_param(config_path: Path, key: str) -> str | None:
    if not config_path.exists():
        return None
    pat = re.compile(rf"^\s*params\.{re.escape(key)}\s*=\s*['\"]?(.+?)['\"]?\s*$")
    for line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pat.match(line)
        if m:
            return m.group(1)
    return None


def _resolve_pipeline_settings(
    pipeline_dir: Path,
    config_name: str,
    output_dir: str | None,
    project_name: str | None,
) -> tuple[Path, str, str]:
    config_path = pipeline_dir / config_name
    resolved_output = output_dir or _read_param(config_path, "output_dir") or "../results"
    resolved_project = project_name or _read_param(config_path, "project_name") or "project"
    return config_path, resolved_output, resolved_project


def _invalidate_participant_cache(pipeline_dir: Path, pid: str) -> int:
    work = pipeline_dir / "work"
    if not work.exists():
        return 0

    removed = 0
    for hash_dir in work.glob("*/*"):
        if not hash_dir.is_dir():
            continue
        has_pid_artifact = any(hash_dir.glob(f"{pid}*"))
        if has_pid_artifact:
            shutil.rmtree(hash_dir, ignore_errors=True)
            removed += 1
    return removed


def run_reinject(args) -> int:
    pid = args.participant.strip()
    pipeline_dir = Path(args.pipeline_dir).resolve()

    config_path, output_dir, project_name = _resolve_pipeline_settings(
        pipeline_dir,
        args.config,
        args.output_dir,
        args.project_name,
    )

    l1_dir = (pipeline_dir / output_dir / f"{project_name}_l1").resolve()
    pid_dir = l1_dir / pid

    if not pid_dir.exists():
        print(f"Error: participant directory not found: {pid_dir}")
        return 1

    if args.corrected_file:
        if not args.script_name:
            print("Error: --script-name is required when --corrected-file is provided")
            return 1

        corrected = Path(args.corrected_file).resolve()
        if not corrected.exists():
            print(f"Error: corrected file not found: {corrected}")
            return 1

        correction_dir = pid_dir / "corrections" / args.script_name
        correction_dir.mkdir(parents=True, exist_ok=True)
        dest = correction_dir / corrected.name
        shutil.copy2(corrected, dest)
        print(f"Placed correction: {dest}")

    marker = pid_dir / ".reinject"
    marker.touch(exist_ok=True)
    print(f"Marked participant for reinjection: {pid}")

    removed = _invalidate_participant_cache(pipeline_dir, pid)
    print(f"Invalidated {removed} cached task(s) for {pid}")

    if args.no_run:
        print("Staged only. Start pipeline manually with -resume when ready.")
        return 0

    cmd = [
        "nextflow",
        "run",
        args.pipeline,
        "-c",
        str(config_path.name),
        "-resume",
        "--participant_pattern",
        pid,
    ]

    print("Running:", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(pipeline_dir), check=False)
    return int(completed.returncode)
