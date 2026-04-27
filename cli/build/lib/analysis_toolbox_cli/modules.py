from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path
import sys
import shutil

from .config import get as cfg_get


def deploy_modules(target: Path, overwrite: bool = False) -> Path:
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    src_root = files("analysis_toolbox_cli").joinpath("resources", "modules")
    for group in ("analyzers", "processors", "readers", "utils"):
        src_group = src_root.joinpath(group)
        dst_group = target / group

        with as_file(src_group) as src_path:
            if dst_group.exists() and overwrite:
                shutil.rmtree(dst_group)
            if not dst_group.exists():
                shutil.copytree(src_path, dst_group)

    marker = target / ".atbx_modules"
    marker.write_text("installed\n", encoding="utf-8")
    return target


def maybe_autodeploy_modules_for_frozen() -> None:
    """Auto-deploy modules for packaged exe/AppImage builds on first launch."""
    if not getattr(sys, "frozen", False):
        return

    modules_dir = cfg_get("modules_dir")
    if not modules_dir:
        return

    target = Path(modules_dir)
    marker = target / ".atbx_modules"
    if marker.exists():
        return

    deployed = deploy_modules(target=target, overwrite=False)
    print(f"Modules installed to: {deployed}")
