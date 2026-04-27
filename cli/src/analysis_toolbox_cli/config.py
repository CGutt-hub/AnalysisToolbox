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
    """On first atbx invocation ask where the user wants the modules mirror."""
    cfg = _load()
    if "modules_dir" in cfg:
        return

    default = _default_modules_dir()
    print("atbx first run — where do you want the modules folder?")
    print(f"  Press Enter for default: {default}")
    try:
        answer = input("  Modules location: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    chosen = Path(answer) if answer else default
    cfg["modules_dir"] = str(chosen.expanduser().resolve())
    _save(cfg)
    print(f"  Saved: {cfg['modules_dir']}")
    print("  To change: edit ~/.atbx_config")


def _default_modules_dir() -> Path:
    home = Path.home()
    if os.name == "nt":
        docs = Path(os.environ.get("USERPROFILE", str(home))) / "Documents"
        return docs / "atbxModules"
    return home / "atbxModules"
