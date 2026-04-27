from __future__ import annotations

import argparse

from .config import first_run_prompt, run_config, move_modules_dir
from .init import run_init
from .modules import maybe_autodeploy_modules_for_frozen
from .reinject import run_reinject
from .run import run_pipeline
from .serve import run_serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitatbx", description="GitAtbx CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialise a new analysis project (pipeline, modules, results)")
    p_init.add_argument("target", help="Target directory (will be created if absent)")
    p_init.set_defaults(func=run_init)

    p_run = sub.add_parser("run", help="Find and run a pipeline by project name pattern (searches entire system)")
    p_run.add_argument("project", nargs="?", default="", help="Project name pattern or path (e.g. 'EV')")
    p_run.add_argument("--resume", action="store_true", help="Pass -resume to Nextflow")
    p_run.set_defaults(func=run_pipeline)

    p_reinject = sub.add_parser("reinject", help="Reinject corrected output for one participant")
    p_reinject.add_argument("participant", help="Participant ID, e.g. EV_002")
    p_reinject.add_argument("--pipeline-dir", default=".", help="Pipeline root directory")
    p_reinject.add_argument("--pipeline", default="EV_pipeline.nf", help="Nextflow pipeline file")
    p_reinject.add_argument("--config", default="EV_parameters.config", help="Nextflow config file")
    p_reinject.add_argument("--output-dir", default=None, help="Override params.output_dir")
    p_reinject.add_argument("--project-name", default=None, help="Override params.project_name")
    p_reinject.add_argument("--corrected-file", default=None, help="Corrected parquet to inject")
    p_reinject.add_argument("--script-name", default=None, help="Producer script name without .py")
    p_reinject.add_argument("--no-run", action="store_true", help="Only stage reinjection, do not start Nextflow")
    p_reinject.set_defaults(func=run_reinject)

    p_serve = sub.add_parser("serve", help="Serve results HTML locally in browser")
    p_serve.add_argument("--dir", default=".", help="Results directory to serve")
    p_serve.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    p_serve.set_defaults(func=run_serve)

    p_cfg = sub.add_parser("config", help="View or update atbx configuration")
    cfg_sub = p_cfg.add_subparsers(dest="config_command", required=True)
    cfg_sub.add_parser("show", help="Print current configuration")
    p_cfg.set_defaults(func=run_config)

    p_move = sub.add_parser("move", help="Move the deployed modules folder to a new location")
    p_move.add_argument("dest", help="Destination path (must not already exist)")
    p_move.set_defaults(func=lambda args: _run_move(args))

    return parser


def _run_move(args) -> int:
    try:
        move_modules_dir(args.dest)
    except (FileNotFoundError, FileExistsError) as e:
        print(f"Error: {e}")
        return 1
    return 0


def main() -> int:
    maybe_autodeploy_modules_for_frozen()
    first_run_prompt()
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
