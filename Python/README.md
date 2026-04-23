# gitref-toolbox

Python CLI tools for Nextflow-based analysis pipelines built with the [GitRef / AnalysisToolbox](https://github.com/your-org/AnalysisToolbox) workflow framework.

## Installation

```bash
pip install gitref-toolbox
# or editable from repo:
pip install -e /path/to/AnalysisToolbox/Python
```

After installation the `gitref` command is available. On Windows make sure
`%APPDATA%\..\Local\Programs\Python\Python31X\Scripts` is on your PATH:

```powershell
# PowerShell — one-time PATH fix
$scripts = "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts"
[Environment]::SetEnvironmentVariable("PATH", "$env:PATH;$scripts", "User")
```

On Linux/macOS (SMB mount / WSL) the command will be on PATH automatically via the venv or `~/.local/bin`.

## Commands

### `gitref reinject`

Re-inject a corrected output file for one participant without restarting the whole pipeline.

```
gitref reinject <PID> <corrected_file> <script_name> [--pipeline-dir DIR]
```

- `PID` — participant ID, e.g. `EV_002`
- `corrected_file` — path to the corrected `.parquet` file
- `script_name` — the pipeline script whose output is being replaced, e.g. `eda_processor`
- `--pipeline-dir` — directory containing `nextflow.config` (default: current directory)

**How it works**

1. Copies `corrected_file` to `<pipeline_dir>/<PID>/corrections/<script_name>/`
2. Places a `.reinject` marker in `<pipeline_dir>/<PID>/`
3. Invalidates the relevant `work/` cache entries for `PID`
4. Runs `nextflow run ... -resume --participant_pattern <PID>`

The IOInterface correction gate in `workflow_wrapper.nf` detects the override at runtime and feeds the corrected parquet downstream instead of re-running the script.

### `gitref inspect`

Pretty-print schema and preview rows from a `.parquet` file.

```
gitref inspect <file.parquet> [--rows N]
```

### `gitref serve`

Serve the results HTML directory locally for browser inspection.

```
gitref serve [--dir DIR] [--port PORT]
```

### `gitref init`

Scaffold a new analysis pipeline from the GitRef template.

```
gitref init [--target DIR]
```

## Development

```bash
git clone ...
pip install -e AnalysisToolbox/Python
pytest AnalysisToolbox/Python/tests/
```

## License

MIT
