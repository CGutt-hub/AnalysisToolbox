from __future__ import annotations

import json
import os
from pathlib import Path


_CONFIG_FILE = Path.home() / ".gitatbx_config"


def _load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get(key: str, default=None):
    return _load().get(key, default)


def set(key: str, value) -> None:
    cfg = _load()
    cfg[key] = value
    _save(cfg)


def first_run_prompt() -> None:
    """On first atbx invocation deploy the modules mirror to the default Documents location."""
    cfg = _load()
    if "modules_dir" in cfg:
        return

    chosen = _default_modules_dir().expanduser().resolve()
    cfg["modules_dir"] = str(chosen)
    _save(cfg)

    from .modules import deploy_modules
    deploy_modules(target=chosen)
    print(f"gitatbx: modules symlinked at {chosen}")
    print("  To move it: gitatbx move <new-path>")


def move_modules_dir(dest: str) -> None:
    """Move the GitAtbxModules symlink to a new location and update config."""
    cfg = _load()
    current = cfg.get("modules_dir")
    if not current:
        raise FileNotFoundError("No modules_dir configured. Run gitatbx once first.")
    src = Path(current)
    dst = Path(dest).expanduser().resolve()
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"Destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    from .modules import _pkg_path
    pkg_modules = _pkg_path() / "modules"
    import os
    os.symlink(pkg_modules, dst)
    if src.is_symlink():
        src.unlink()
    cfg["modules_dir"] = str(dst)
    _save(cfg)
    print(f"gitatbx: modules symlink moved {src} → {dst}")


def run_config(args) -> int:
    if args.config_command == "show":
        cfg = _load()
        if cfg:
            for k, v in cfg.items():
                print(f"  {k} = {v}")
        else:
            print("  (no config found — run gitatbx once to initialise)")
    return 0


def _default_modules_dir() -> Path:
    home = Path.home()
    if os.name == "nt":
        docs = Path(os.environ.get("USERPROFILE", str(home))) / "Documents"
        return docs / "GitAtbxModules"
    return home / "GitAtbxModules"
