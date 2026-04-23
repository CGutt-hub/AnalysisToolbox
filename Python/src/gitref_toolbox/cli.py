import argparse
from .reinject import run_reinject
from .inspect import run_inspect
from .serve import run_serve
from .init import run_init


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gitref", description="GitRef toolbox CLI")
    sub = parser.add_subparsers(dest="command", required=True)

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

    p_inspect = sub.add_parser("inspect", help="Show quick parquet schema and stats")
    p_inspect.add_argument("parquet", help="Path to parquet file")
    p_inspect.add_argument("--rows", type=int, default=5, help="Number of preview rows")
    p_inspect.set_defaults(func=run_inspect)

    p_serve = sub.add_parser("serve", help="Start results HTML server")
    p_serve.add_argument("--dir", default=".", help="Directory to serve")
    p_serve.add_argument("--port", type=int, default=8080, help="Server port")
    p_serve.set_defaults(func=run_serve)

    p_init = sub.add_parser("init", help="Scaffold a new pipeline from template")
    p_init.add_argument("target", help="Target directory")
    p_init.set_defaults(func=run_init)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
