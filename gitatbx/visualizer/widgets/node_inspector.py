"""
Node Inspector - Right panel for inspecting pipeline nodes
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTabWidget, QLabel,
                            QTreeWidget, QTreeWidgetItem, QFormLayout,
                            QLineEdit, QTextEdit, QComboBox)
from PyQt6.QtCore import Qt
from typing import Optional

from ..models.pipeline_model import PipelineNode, NodeStatus
from ..utils.theme import ATBXTheme


class NodeInspector(QWidget):
    """Inspector widget showing details of selected node."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.node: Optional[PipelineNode] = None
        
        self._setup_ui()
        self._apply_theme()
        self._update_empty_state()
    
    def _setup_ui(self):
        """Set up user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Tab widget
        self.tab_widget = QTabWidget()
        
        # Overview tab
        self.overview_tab = QWidget()
        self._setup_overview_tab()
        
        # Participants tab
        self.participants_tab = QWidget()
        self._setup_participants_tab()
        
        # Parameters tab
        self.parameters_tab = QWidget()
        self._setup_parameters_tab()
        
        # I/O tab
        self.io_tab = QWidget()
        self._setup_io_tab()
        
        # Errors tab
        self.errors_tab = QWidget()
        self._setup_errors_tab()
        
        self.tab_widget.addTab(self.overview_tab, "Overview")
        self.tab_widget.addTab(self.participants_tab, "Participants")
        self.tab_widget.addTab(self.parameters_tab, "Parameters")
        self.tab_widget.addTab(self.io_tab, "I/O")
        self.tab_widget.addTab(self.errors_tab, "Errors")
        
        layout.addWidget(self.tab_widget)
    
    def _setup_overview_tab(self):
        """Set up overview tab."""
        layout = QVBoxLayout(self.overview_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Header
        self.name_label = QLabel("Select a node to inspect")
        self.name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.name_label)
        
        # Status
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        
        # Description
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        
        # Type
        self.type_label = QLabel()
        layout.addWidget(self.type_label)
        
        # Script path
        self.script_label = QLabel()
        self.script_label.setWordWrap(True)
        layout.addWidget(self.script_label)
        
        layout.addStretch()
    
    def _setup_participants_tab(self):
        """Set up participants tab."""
        layout = QVBoxLayout(self.participants_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.participants_tree = QTreeWidget()
        self.participants_tree.setHeaderLabels(["Participant", "Status", "Error"])
        self.participants_tree.setColumnCount(3)
        layout.addWidget(self.participants_tree)
    
    def _setup_parameters_tab(self):
        """Set up parameters tab."""
        self.form_layout = QFormLayout(self.parameters_tab)
        self.form_layout.setContentsMargins(8, 8, 8, 8)
        self.form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    
    def _setup_io_tab(self):
        """Set up I/O tab."""
        layout = QVBoxLayout(self.io_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Input
        self.input_label = QLabel("Input Files:")
        layout.addWidget(self.input_label)
        self.input_tree = QTreeWidget()
        layout.addWidget(self.input_tree)
        
        # Output
        self.output_label = QLabel("Output Files:")
        layout.addWidget(self.output_label)
        self.output_tree = QTreeWidget()
        layout.addWidget(self.output_tree)
    
    def _setup_errors_tab(self):
        """Set up errors tab."""
        layout = QVBoxLayout(self.errors_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        
        self.error_text = QTextEdit()
        self.error_text.setReadOnly(True)
        layout.addWidget(self.error_text)
    
    def _apply_theme(self):
        """Apply ATBX theme."""
        colors = ATBXTheme.COLORS
        self.setStyleSheet(f"""
        NodeInspector {{
            background-color: {colors['bg_secondary']};
            color: {colors['text_primary']};
        }}
        QLabel {{
            color: {colors['text_primary']};
        }}
        QTreeWidget {{
            background-color: {colors['bg_secondary']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
        }}
        QTextEdit {{
            background-color: {colors['bg_secondary']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
        }}
        QTabWidget::pane {{
            border: 1px solid {colors['border_primary']};
        }}
        """)
    
    def set_node(self, node: PipelineNode):
        """Update inspector with node data."""
        self.node = node
        self._update_overview_tab()
        self._update_participants_tab()
        self._update_parameters_tab()
        self._update_io_tab()
        self._update_errors_tab()
    
    def _update_empty_state(self):
        """Update to show empty state."""
        self.name_label.setText("Select a node to inspect")
        self.status_label.setText("")
        self.description_label.setText("")
        self.type_label.setText("")
        self.script_label.setText("")
        self.participants_tree.clear()
        self._clear_form()
        self.input_tree.clear()
        self.output_tree.clear()
        self.error_text.clear()
    
    def _update_overview_tab(self):
        """Update overview tab with node data."""
        if not self.node:
            self._update_empty_state()
            return
        
        self.name_label.setText(self.node.module_info.name)
        
        # Status with color
        status_text = self.node.status.value.replace('_', ' ').title()
        status_color = ATBXTheme.get_status_color(self.node.status.value).name()
        self.status_label.setText(f'<span style="color: {status_color};">{status_text}</span>')
        
        self.description_label.setText(self.node.module_info.description)
        self.type_label.setText(f"Type: {self.node.module_info.module_type.value}")
        self.script_label.setText(f"Script: {self.node.module_info.script_path}")
    
    def _update_participants_tab(self):
        """Update participants tab."""
        self.participants_tree.clear()
        
        if not self.node:
            return
        
        for pid, pdata in self.node.participants.items():
            item = QTreeWidgetItem()
            item.setText(0, pid)
            item.setText(1, pdata.status.value.replace('_', ' ').title())
            item.setText(2, pdata.error_message or "")
            self.participants_tree.addTopLevelItem(item)
    
    def _update_parameters_tab(self):
        """Update parameters tab."""
        self._clear_form()
        
        if not self.node:
            return
        
        # Add node-level parameters
        for name, value in self.node.parameters.items():
            self._add_form_row(name, str(value))
        
        # Add module default parameters
        for name, param in self.node.module_info.parameters.items():
            if name not in self.node.parameters:
                self._add_form_row(name, str(param.default) if param.default else "")
    
    def _update_io_tab(self):
        """Update I/O tab."""
        self.input_tree.clear()
        self.output_tree.clear()
        
        if not self.node:
            return
        
        # Inputs
        for input_channel in self.node.module_info.inputs:
            item = QTreeWidgetItem([input_channel.name, input_channel.type])
            self.input_tree.addTopLevelItem(item)
        
        # Outputs
        for output_channel in self.node.module_info.outputs:
            item = QTreeWidgetItem([output_channel.name, output_channel.type])
            self.output_tree.addTopLevelItem(item)
    
    def _update_errors_tab(self):
        """Update errors tab."""
        self.error_text.clear()
        
        if not self.node:
            return
        
        errors = []
        for pid, pdata in self.node.participants.items():
            if pdata.error_message:
                errors.append(f"[{pid}] Error {pdata.error_code}: {pdata.error_message}")
        
        if errors:
            self.error_text.setPlainText("\n".join(errors))
        else:
            self.error_text.setPlainText("No errors")
    
    def _clear_form(self):
        """Clear form layout."""
        while self.form_layout.count():
            child = self.form_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()
    
    def _add_form_row(self, name: str, value: str):
        """Add a row to the form."""
        label = QLabel(f"{name}:")
        edit = QLineEdit(value)
        edit.setReadOnly(True)
        self.form_layout.addRow(label, edit)
