# AnalysisToolbox

A modular framework for automated data processing and statistical analysis pipelines. Built on **Nextflow** for scalable, reproducible workflows with automatic result synchronization.

## Overview

The AnalysisToolbox provides infrastructure for building data processing pipelines that:

- **Process multiple datasets** in parallel with automatic discovery of new data
- **Handle diverse data types** through a generic reader/processor/analyzer architecture
- **Track progress** via per-dataset logging and automatic git synchronization of results
- **Recover gracefully** from failures without losing completed work

The framework is domain-agnostic—modules follow simple input/output conventions (Parquet files) and can implement any processing logic. Some specialized modules exist for specific data types (e.g., fNIRS preprocessing) where domain knowledge is required.

## Prerequisites

### 1. WSL (Windows Subsystem for Linux)

The pipeline runs in Linux. On Windows, install WSL:

```powershell
wsl --install -d Ubuntu
```

### 2. Java Runtime (required by Nextflow)

```bash
sudo apt update
sudo apt install default-jre
java -version  # Verify installation
```

### 3. Nextflow

```bash
curl -s https://get.nextflow.io | bash
sudo mv nextflow /usr/local/bin/
nextflow -version  # Verify installation
```

### 4. Python Environment

Create a virtual environment with required packages:

```bash
python3 -m venv ~/analysis_venv
source ~/analysis_venv/bin/activate
pip install numpy pandas polars scipy matplotlib
# Add domain-specific packages as needed (e.g., mne for neuroimaging)
```

### 5. Git SSH Setup (for automatic result sync)

Generate an SSH key (press Enter for no passphrase):

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
cat ~/.ssh/id_ed25519.pub
```

Add the public key to GitHub: https://github.com/settings/keys → "New SSH key"

Test the connection:

```bash
ssh -T git@github.com
```

## Project Structure

```
AnalysisToolbox/
├── Python/
│   ├── analyzers/       # Analysis modules (statistics, feature extraction)
│   ├── processors/      # Data transformation modules (filtering, epoching)
│   ├── readers/         # File format readers (XDF, TXT, etc.)
│   └── utils/           # Infrastructure (Nextflow wrapper, plotting)
```

## Core Functionality

This repository provides one integrated toolbox with these core functions:

- Environment generation for pipeline projects, including folder and workflow setup
	(for example project structures such as `EV_analysis` and `EV_results`)
- Participant-level correction and replay via reinjection ("cherry-picking" one
	participant or one corrected output without restarting everything)
- Web GUI/bootstrap helpers for local result inspection and sharing
- Python processing, analysis, and statistical math scripts (reader/processor/analyzer modules)

## Usage

### Install

```bash
pip install atbx
```

Installs all Python dependencies and the `atbx` command. Nextflow must be installed separately (see Prerequisites). On first run, `atbx` asks where to mirror the modules folder.

To uninstall: `pip uninstall atbx`

### Commands

| Command | What it does |
|---|---|
| `atbx init <dir>` | Scaffold a new analysis project |
| `atbx serve [--dir DIR] [--port PORT]` | Start web UI to browse results and plots |
| `atbx reinject <PID> --corrected-file <file> --script-name <name> [--pipeline-dir DIR]` | Reinject a corrected parquet for one participant |

`atbx init <dir>` prompts for project name, raw data path, Python executable, and toolbox location, then creates:

```
<dir>/
├── {name}_analysis/
│   ├── {name}_pipeline.nf          ← workflow definition
│   ├── {name}_modules.nf           ← add your process includes here
│   └── {name}_parameters.config    ← pre-filled with your answers
└── {name}_results/
```

`atbx reinject` places a corrected parquet into `corrections/<script_name>/`, marks the participant for replay, invalidates relevant cache entries, and resumes only that participant.

### Creating a Pipeline

Pipelines are defined in Nextflow DSL2. A typical pipeline:

1. **Discovers participants** via `workflow_wrapper` (supports continuous monitoring)
2. **Chains processing steps** using `IOInterface` (generic Python script runner)
3. **Tracks completion** via watchdog threads that monitor terminal processes
4. **Syncs results** to git when each participant completes

### Configuration

Pipelines use a `parameters.config` file to define:

- Input/output directories
- Python environment path
- Script paths
- Processing parameters

### Running

```bash
cd /path/to/your/pipeline
nextflow run pipeline.nf -c parameters.config -with-trace
```

The `-with-trace` flag is required for the watchdog to monitor completion.

## Key Components

### `workflow_wrapper`

Discovers participant directories, creates output folders, and starts per-participant watchdog threads.

### `IOInterface`

Generic process that runs any Python script with automatic logging to `{id}_pipeline.log`.

### Watchdog

Background thread per participant that:
- Monitors the trace file for terminal process completion
- Appends completion summary to the log
- Triggers git commit/push with results

## Built With

- **Nextflow** - Workflow orchestration
- **Python** - Processing and analysis modules
- **Polars/Pandas** - Data manipulation
- **NumPy/SciPy** - Numerical computing
- **Matplotlib** - Visualization

## Authors

- **Cagatay Özcan Jagiello Gutt** - *Lead Developer*
*ORCID: https://orcid.org/0000-0002-1774-532X*