"""
ATBX Pipeline Visualizer

A PyQt6-based GUI for designing, visualizing, and debugging Nextflow pipelines
in the AnalysisToolbox framework.
"""

from .main import main
from .pipeline_visualizer import PipelineVisualizer

__all__ = ['main', 'PipelineVisualizer']
__version__ = '0.1.0'
