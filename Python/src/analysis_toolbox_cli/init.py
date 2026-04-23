from importlib.resources import as_file, files
from pathlib import Path
import shutil
import sys


def _res(*parts: str):
    return files("analysis_toolbox_cli").joinpath(*parts)


def _copy_resource(res, dest: Path) -> None:
    with as_file(res) as src:
        shutil.copy2(src, dest)


def _copy_resource_tree(res, dest: Path) -> None:
    with as_file(res) as src:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)


def _ask(prompt: str, default: str) -> str:
    try:
        answer = input(f"  {prompt} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return answer if answer else default


def _stamp_params(config_path: Path, replacements: dict[str, str]) -> None:
    """Write resolved param values into the config, replacing placeholder lines."""
    import re
    text = config_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        pattern = re.compile(rf"^(params\.{re.escape(key)}\s*=\s*).*$", re.MULTILINE)
        # Use a lambda to avoid regex interpretation of the replacement string
        new_line = f"params.{key} = '{value}'"
        text = pattern.sub(lambda _: new_line, text)
        if not pattern.search(config_path.read_text(encoding="utf-8")):
            text += f"\n{new_line}\n"
    config_path.write_text(text, encoding="utf-8")


def run_init(args) -> int:
    root = Path(args.target).expanduser().resolve()
    project_name = root.name

    print(f"\nInitialising project: {project_name}")
    print("Press Enter to accept defaults.\n")

    # ── project-fixed parameters ───────────────────────────────────────────────
    confirmed_name = _ask("Project name",       project_name)
    raw_data_dir   = _ask("Raw data directory", "../rawData")
    python_exe     = _ask("Python executable",  sys.executable)
    toolbox_dir    = _ask("Toolbox directory",  "../AnalysisToolbox/Python")

    analysis_name = f"{confirmed_name}_analysis"
    results_name  = f"{confirmed_name}_results"

    # ── {name}_analysis folder ─────────────────────────────────────────────────
    analysis = root / analysis_name
    analysis.mkdir(parents=True, exist_ok=True)

    modules_filename = f"{confirmed_name}_modules.nf"

    _copy_resource(_res("resources", "templates", "template.nf"),        analysis / f"{confirmed_name}_pipeline.nf")
    _copy_resource(_res("resources", "templates", "nextflow.config"),     analysis / f"{confirmed_name}_parameters.config")
    _copy_resource(_res("resources", "templates", "modules_template.nf"), analysis / modules_filename)

    # Stamp __MODULES_FILE__ in pipeline
    pipeline_file = analysis / f"{confirmed_name}_pipeline.nf"
    pipeline_file.write_text(
        pipeline_file.read_text(encoding="utf-8").replace("__MODULES_FILE__", modules_filename),
        encoding="utf-8",
    )

    # Stamp __TOOLBOX_DIR__ in modules file
    modules_file = analysis / modules_filename
    modules_file.write_text(
        modules_file.read_text(encoding="utf-8").replace("__TOOLBOX_DIR__", toolbox_dir),
        encoding="utf-8",
    )

    # Stamp resolved values into parameters.config
    _stamp_params(analysis / f"{confirmed_name}_parameters.config", {
        "project_name": confirmed_name,
        "input_dir":    raw_data_dir,
        "output_dir":   f"../{results_name}",
        "python_exe":   python_exe,
        "toolbox_dir":  toolbox_dir,
    })

    # ── {name}_results folder ──────────────────────────────────────────────────
    results = root / results_name
    results.mkdir(exist_ok=True)
    (results / ".gitkeep").touch()

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\nProject ready in: {root}")
    print(f"  {analysis_name}/")
    print(f"    {confirmed_name}_pipeline.nf        ← workflow")
    print(f"    {confirmed_name}_modules.nf         ← add your process includes here")
    print(f"    {confirmed_name}_parameters.config  ← pre-filled")
    print(f"  {results_name}/")
    return 0
