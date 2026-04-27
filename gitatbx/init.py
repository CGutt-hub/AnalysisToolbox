from importlib.resources import as_file, files
from pathlib import Path
import shutil
import subprocess
import sys

from .config import get as cfg_get


def _res(*parts: str):
    return files("gitatbx").joinpath(*parts)


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


def _git_global(key: str) -> str:
    """Read a global git config value, return empty string if not set."""
    try:
        r = subprocess.run(["git", "config", "--global", key],
                           capture_output=True, text=True, check=False)
        return r.stdout.strip()
    except FileNotFoundError:
        return ""


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd,
                          capture_output=True, text=True, check=False)


def run_init(args) -> int:
    root = Path(args.target).expanduser().resolve()
    project_name = root.name

    print(f"\nInitialising project: {project_name}")
    print("Press Enter to accept defaults.\n")

    # ── project parameters ─────────────────────────────────────────────────────
    confirmed_name = _ask("Project name",       project_name)
    raw_data_dir   = _ask("Raw data directory", "../rawData")
    python_exe     = _ask("Python executable",  sys.executable)
    default_toolbox = cfg_get("modules_dir") or "../AnalysisToolbox"
    toolbox_dir    = _ask("Toolbox directory",  default_toolbox)

    # ── git identity ───────────────────────────────────────────────────────────
    print()
    global_name  = _git_global("user.name")
    global_email = _git_global("user.email")
    default_git_name  = global_name  or f"{confirmed_name} Pipeline"
    default_git_email = global_email or f"{confirmed_name.lower()}-pipeline@automated.local"
    git_user_name  = _ask("Git commit author name",  default_git_name)
    git_user_email = _ask("Git commit author email", default_git_email)

    # ── GitHub remote ──────────────────────────────────────────────────────────
    remote_url = _ask("GitHub remote URL for results (blank to skip)", "")

    analysis_name = f"{confirmed_name}_analysis"
    results_name  = f"{confirmed_name}_results"

    # ── {name}_analysis folder ─────────────────────────────────────────────────
    analysis = root / analysis_name
    analysis.mkdir(parents=True, exist_ok=True)

    modules_filename = f"{confirmed_name}_modules.nf"

    _copy_resource(_res("templates", "workflow_template.nf"),       analysis / f"{confirmed_name}_pipeline.nf")
    _copy_resource(_res("templates", "parameters_template.config"), analysis / f"{confirmed_name}_parameters.config")
    _copy_resource(_res("templates", "modules_template.nf"),        analysis / modules_filename)

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

    # Stamp parameters.config
    params_file = analysis / f"{confirmed_name}_parameters.config"
    params_text = params_file.read_text(encoding="utf-8")
    for placeholder, value in [
        ("__PROJECT_NAME__",  confirmed_name),
        ("__INPUT_DIR__",     raw_data_dir),
        ("__OUTPUT_DIR__",    f"../{results_name}"),
        ("__PYTHON_EXE__",    python_exe),
        ("__TOOLBOX_DIR__",   toolbox_dir),
        ("__GIT_USER_NAME__", git_user_name),
        ("__GIT_USER_EMAIL__", git_user_email),
    ]:
        params_text = params_text.replace(placeholder, value)
    params_file.write_text(params_text, encoding="utf-8")

    # ── {name}_results folder + git setup ─────────────────────────────────────
    results = root / results_name
    results.mkdir(exist_ok=True)
    (results / ".gitkeep").touch()

    if remote_url:
        cwd = str(results)
        _git(["init"], cwd)
        _git(["remote", "add", "origin", remote_url], cwd)
        # Verify authentication/connectivity
        check = _git(["ls-remote", "--exit-code", "origin"], cwd)
        if check.returncode != 0:
            print(f"\n  [!] Could not reach {remote_url}")
            print("      Git pushes will fail until SSH keys or credentials are configured.")
            print("      See: https://docs.github.com/en/authentication/connecting-to-github-with-ssh")
        else:
            print(f"\n  Git remote verified: {remote_url}")
    else:
        print("\n  No remote configured — git push will be skipped by the pipeline.")

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\nProject ready in: {root}")
    print(f"  {analysis_name}/")
    print(f"    {confirmed_name}_pipeline.nf        ← workflow")
    print(f"    {confirmed_name}_modules.nf         ← add your process includes here")
    print(f"    {confirmed_name}_parameters.config  ← pre-filled")
    print(f"  {results_name}/")
    if remote_url:
        print(f"    .git/  (remote: {remote_url})")
    return 0
