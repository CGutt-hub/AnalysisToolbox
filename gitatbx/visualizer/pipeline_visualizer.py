"""
ATBX Pipeline Visualizer - Main Window

Main application window with three panels:
- Left: Module Toolbar (drag-and-drop)
- Center: Pipeline Canvas
- Right: Node Inspector
"""

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QSplitter,
                            QDockWidget, QVBoxLayout, QToolBar, QStatusBar,
                            QLabel, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QAction, QIcon
from pathlib import Path
from typing import Optional, Dict
import uuid

from .models.module_model import ModuleRegistry, create_module_registry
from .models.pipeline_model import Pipeline, PipelineNode
from .widgets.module_toolbar import ModuleToolbar
from .utils.theme import ATBXTheme


class PipelineVisualizer(QMainWindow):
    """
    Main window for ATBX Pipeline Visualizer.
    
    Architecture:
    - Left panel: ModuleToolbar (drag-and-drop source)
    - Center: Canvas (workflow visualization)
    - Right panel: NodeInspector (updates on selection)
    """
    
    # Signals
    node_selected = pyqtSignal(PipelineNode)
    pipeline_changed = pyqtSignal()
    
    def __init__(self, atbx_path: str, parent=None):
        super().__init__(parent)
        
        self.atbx_path = Path(atbx_path)
        self.pipeline: Optional[Pipeline] = None
        self.current_file: Optional[str] = None
        
        # Initialize module registry
        self.module_registry: Optional[ModuleRegistry] = None
        
        self._setup_ui()
        self._create_actions()
        self._setup_toolbar()
        self._setup_statusbar()
        self._apply_theme()
        
        # Load modules
        self._load_modules()
        
        # Create new empty pipeline
        self.new_pipeline()
    
    def _setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle("ATBX Pipeline Visualizer")
        self.setMinimumSize(1024, 768)
        
        # Central widget with horizontal splitter
        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        
        # Create splitter for panels
        self.splitter = QSplitter(Qt.Horizontal)
        central_layout.addWidget(self.splitter)
        
        # Left panel - Module Toolbar
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        
        # Center panel - Canvas
        from .widgets.canvas import Canvas
        self.canvas = Canvas()
        self.canvas.node_clicked.connect(self._on_node_clicked)
        self.canvas.setMinimumSize(400, 300)
        
        # Right panel - Node Inspector (as dock widget)
        from .widgets.node_inspector import NodeInspector
        self.right_dock = QDockWidget("Node Inspector", self)
        self.right_dock.setObjectName("NodeInspectorDock")
        self.node_inspector = NodeInspector()
        self.right_dock.setWidget(self.node_inspector)
        
        # Add panels to splitter
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.canvas)
        
        # Set splitter sizes
        self.splitter.setSizes([200, 600])
        
        # Add dock widget
        self.addDockWidget(Qt.RightDockWidgetArea, self.right_dock)
        
        self.setCentralWidget(central_widget)
    
    def _apply_theme(self):
        """Apply dark theme to window."""
        colors = ATBXTheme.COLORS
        self.setStyleSheet(f"""
        PipelineVisualizer {{
            background-color: {colors['bg_primary']};
            color: {colors['text_primary']};
        }}
        QSplitter::handle {{
            background-color: {colors['border_primary']};
            width: 4px;
        }}
        QDockWidget {{
            background-color: {colors['bg_secondary']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
        }}
        QDockWidget::title {{
            background-color: {colors['bg_elevated']};
            color: {colors['text_primary']};
            padding: 4px 8px;
            border: none;
        }}
        """)
    
    def _create_actions(self):
        """Create menu and toolbar actions."""
        # File menu
        self.new_action = QAction("New Pipeline", self)
        self.new_action.setShortcut("Ctrl+N")
        self.new_action.triggered.connect(self.new_pipeline)
        
        self.open_action = QAction("Open Pipeline...", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_pipeline)
        
        self.save_action = QAction("Save Pipeline", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_pipeline)
        
        self.save_as_action = QAction("Save Pipeline As...", self)
        self.save_as_action.setShortcut("Ctrl+Shift+S")
        self.save_as_action.triggered.connect(self.save_pipeline_as)
        
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        
        # Build menu - will be populated when modules are loaded
        self.build_action = QAction("Build Pipeline", self)
        self.build_action.setShortcut("Ctrl+B")
        self.build_action.triggered.connect(self.build_pipeline)
        self.build_action.setEnabled(False)
    
    def _setup_toolbar(self):
        """Set up the main toolbar."""
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        toolbar.setFixedHeight(36)
        
        toolbar.addAction(self.new_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        toolbar.addSeparator()
        toolbar.addAction(self.build_action)
        
        self.addToolBar(toolbar)
    
    def _setup_statusbar(self):
        """Set up the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label, 1)
        
        self.module_count_label = QLabel("Modules: 0")
        self.status_bar.addPermanentWidget(self.module_count_label)
    
    def _load_modules(self):
        """Load modules from ATBX."""
        try:
            self.module_registry = create_module_registry(str(self.atbx_path))
            self.status_bar.showMessage(
                f"Loaded {len(self.module_registry.modules)} modules", 2000
            )
            self.module_count_label.setText(f"Modules: {len(self.module_registry.modules)}")
            self._update_left_panel()
            self.build_action.setEnabled(True)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Module Loading Error",
                f"Failed to load modules from AnalysisToolbox:\n\n{str(e)}"
            )
    
    def _update_left_panel(self):
        """Update left panel with module toolbar."""
        # Clear existing layout
        for i in reversed(range(self.left_layout.count())):
            item = self.left_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        
        # Create new module toolbar
        if self.module_registry:
            self.module_toolbar = ModuleToolbar(self.module_registry)
            self.module_toolbar.module_dragged.connect(self._on_module_dragged)
            self.left_layout.addWidget(self.module_toolbar)
    
    def new_pipeline(self):
        """Create a new empty pipeline."""
        self.pipeline = Pipeline(
            pipeline_id=str(uuid.uuid4()),
            name="Untitled Pipeline",
            description=""
        )
        self.current_file = None
        self.status_bar.showMessage("New pipeline created", 2000)
        self.pipeline_changed.emit()
    
    def open_pipeline(self):
        """Open a pipeline file."""
        # For now, just create a new pipeline
        # Todo: Implement file dialog and loading
        self.new_pipeline()
        QMessageBox.information(self, "Open", "Opening pipeline from file (TODO)")
    
    def save_pipeline(self):
        """Save current pipeline."""
        if not self.current_file:
            self.save_pipeline_as()
            return
        
        if self.pipeline:
            self._save_pipeline_to_file(self.current_file)
            self.status_bar.showMessage(f"Saved to {self.current_file}", 2000)
    
    def save_pipeline_as(self):
        """Save pipeline to a new file."""
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Pipeline",
            "",
            "Pipeline Files (*.pipeline.json);;All Files (*)",
            options=options
        )
        
        if file_path:
            self.current_file = file_path
            self._save_pipeline_to_file(file_path)
            self.setWindowTitle(f"ATBX Pipeline Visualizer - {file_path}")
            self.status_bar.showMessage(f"Saved to {file_path}", 2000)
    
    def _save_pipeline_to_file(self, file_path: str):
        """Save pipeline to file."""
        if not self.pipeline:
            return
        
        try:
            import json
            with open(file_path, 'w') as f:
                json.dump(self.pipeline.to_dict(), f, indent=2)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Save Error",
                f"Failed to save pipeline:\n\n{str(e)}"
            )
    
    def build_pipeline(self):
        """Build/execute the current pipeline."""
        if not self.pipeline:
            QMessageBox.warning(self, "Build", "No pipeline to build")
            return
        
        QMessageBox.information(
            self,
            "Build",
            "Build functionality (TODO)\n\n"
            "This will export the pipeline to a Nextflow script\n"
            "and execute it using Nextflow."
        )
    
    def _on_module_dragged(self, module_info):
        """Handle module dragged from toolbar."""
        if not self.pipeline:
            return
        
        # Create a new node from the module at center of canvas
        viewport_center = self.canvas.viewport().rect().center()
        scene_pos = self.canvas.mapToScene(viewport_center)
        
        node = self.pipeline.add_node(
            module_info,
            position=(scene_pos.x(), scene_pos.y())
        )
        
        self.status_bar.showMessage(
            f"Added {module_info.name} to pipeline", 2000
        )
        self._update_canvas()
        self.pipeline_changed.emit()
    
    def _on_node_clicked(self, node: PipelineNode):
        """Handle node click in canvas."""
        self.node_inspector.set_node(node)
        self.status_bar.showMessage(
            f"Selected: {node.module_info.name}", 2000
        )
    
    def _update_canvas(self):
        """Update canvas with current pipeline."""
        if self.pipeline:
            self.canvas.set_pipeline(self.pipeline)
    
    def closeEvent(self, event):
        """Handle close event."""
        # Todo: ask to save unsaved changes
        event.accept()


# Add uuid import at the top
import uuid
