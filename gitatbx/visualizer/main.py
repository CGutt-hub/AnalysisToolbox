"""
ATBX Pipeline Visualizer - Main Entry Point

Pure PyQt6 GUI application for visualizing and managing Nextflow pipelines.
No AI components - just a standalone desktop application.

Run with: python -m gitatbx.visualizer
Or: python gitatbx/visualizer/main.py
"""

import sys
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QDir, Qt

from .pipeline_visualizer import PipelineVisualizer
from .utils.theme import ATBXTheme


# Add AnalysisToolbox to Python path if not already there
atbx_parent = Path(__file__).parent.parent.parent
if str(atbx_parent) not in sys.path:
    sys.path.insert(0, str(atbx_parent))


def main():
    """Main entry point for ATBX Pipeline Visualizer."""
    app = QApplication(sys.argv)
    
    # Set application metadata
    app.setApplicationName("ATBX Pipeline Visualizer")
    app.setOrganizationName("AnalysisToolbox")
    app.setOrganizationDomain("gitatbx.io")
    
    # Apply dark theme
    ATBXTheme.apply(app)
    
    # Set style
    app.setStyle("Fusion")
    
    # Create and show main window
    atbx_path = get_atbx_path()
    if not atbx_path:
        QMessageBox.critical(
            None,
            "Error",
            "Could not locate AnalysisToolbox directory.\n\n"
            "Please run from within the AnalysisToolbox directory or set the "
            "ATBX_PATH environment variable."
        )
        return 1
    
    try:
        visualizer = PipelineVisualizer(atbx_path)
        visualizer.showMaximized()
        return app.exec()
    except Exception as e:
        QMessageBox.critical(
            None,
            "Fatal Error",
            f"Failed to start ATBX Pipeline Visualizer:\n\n{str(e)}"
        )
        return 1


def get_atbx_path() -> Optional[str]:
    """
    Determine the AnalysisToolbox path.
    
    Priority:
    1. ATBX_PATH environment variable
    2. Current working directory
    3. Parent directory of this file
    4. Search upwards in directory tree
    """
    # Check environment variable
    atbx_path = os.environ.get('ATBX_PATH')
    if atbx_path and Path(atbx_path).exists():
        return atbx_path
    
    # Check current directory
    cwd = Path.cwd()
    if (cwd / "gitatbx").exists():
        return str(cwd)
    
    # Check parent of this file
    file_dir = Path(__file__).parent.parent.parent
    if (file_dir / "gitatbx").exists():
        return str(file_dir)
    
    # Search upwards in directory tree
    current = Path(__file__).parent
    for _ in range(10):  # Max 10 levels up
        if (current / "gitatbx").exists():
            return str(current)
        current = current.parent
    
    # Try common locations
    common_locations = [
        Path.home() / "AnalysisToolbox",
        Path("d:/repoShaggy/AnalysisToolbox"),
        Path("/mnt/emotiview/repoShaggy/AnalysisToolbox"),
    ]
    for loc in common_locations:
        if loc.exists() and (loc / "gitatbx").exists():
            return str(loc)
    
    return None


if __name__ == "__main__":
    sys.exit(main())
