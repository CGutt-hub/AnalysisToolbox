from pathlib import Path
import shutil


def run_init(args) -> int:
    target = Path(args.target)
    target.mkdir(parents=True, exist_ok=True)

    toolbox_root = Path(__file__).resolve().parents[2]
    template = toolbox_root / "utils" / "template.nf"
    nextflow_cfg = toolbox_root / "utils" / "nextflow.config"

    if template.exists():
        shutil.copy2(template, target / "pipeline.nf")
    if nextflow_cfg.exists():
        shutil.copy2(nextflow_cfg, target / "nextflow.config")

    print(f"Initialized pipeline scaffold in {target}")
    return 0
