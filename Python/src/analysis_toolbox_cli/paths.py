from importlib.resources import as_file, files


def run_paths(args) -> int:
    names = [
        "template.nf",
        "nextflow.config",
        "workflow_wrapper.nf",
        "interactive_plotter.py",
        "serve_html.ps1",
        "file_finder.py",
        "log_to_parquet.py",
        "result_collector.py",
        "xdf_inspector.py",
        "reinject.sh",
    ]

    base = files("analysis_toolbox_cli")
    for name in names:
        if name in {"template.nf", "nextflow.config"}:
            res = base.joinpath("resources", "templates", name)
        else:
            res = base.joinpath("resources", "utils", name)
        with as_file(res) as p:
            print(f"{name}: {p}")

    return 0
