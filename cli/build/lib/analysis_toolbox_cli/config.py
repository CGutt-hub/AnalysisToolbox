from __future__ import annotations

import json
import os
from pathlib import Path


_CONFIG_FILE = Path.home() / ".atbx_config"


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


def first_run_prompt() -> None:
    """On first atbx invocation deploy the modules mirror to the default Documents location."""
    cfg = _load()
    if "modules_dir" in cfg:
        return

    chosen = _default_modules_dir().expanduser().resolve()
    cfg["modules_dir"] = str(chosen)
    _save(cfg)
    print(f"atbx: deploying modules to {chosen}")
    print("  To use a different location, move the folder and update ~/.atbx_config")

    from .modules import deploy_modules
    deploy_modules(target=chosen, overwrite=False)


def _default_modules_dir() -> Path:
    home = Path.home()
    if os.name == "nt":
        docs = Path(os.environ.get("USERPROFILE", str(home))) / "Documents"
        return docs / "atbxModules"
    return home / "atbxModules"
