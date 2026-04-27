from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from .config import get as cfg_get


def _pkg_path() -> Path:
    """Return the path to the installed gitatbx package directory."""
    spec = importlib.util.find_spec("gitatbx")
    if spec is None or spec.origin is None:
        raise RuntimeError("Cannot locate installed gitatbx package")
    return Path(spec.origin).parent


def deploy_modules(target: Path) -> Path:
    """Create a symlink at target pointing to the installed atbx/modules/ directory."""
    target = target.expanduser().resolve()

    if target.is_symlink():
        return target  # already set up

    if target.exists():
        raise FileExistsError(
            f"{target} already exists and is not a symlink. "
            "Remove it manually or choose a different path."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    pkg_modules = _pkg_path() / "modules"
    os.symlink(pkg_modules, target)
    return target


def maybe_autodeploy_modules_for_frozen() -> None:
    """Auto-deploy for packaged exe/AppImage builds on first launch."""
    if not getattr(sys, "frozen", False):
        return

    modules_dir = cfg_get("modules_dir")
    if not modules_dir:
        return

    target = Path(modules_dir)
    if target.exists():
        return

    deployed = deploy_modules(target=target)
    print(f"Modules symlinked to: {deployed}")
