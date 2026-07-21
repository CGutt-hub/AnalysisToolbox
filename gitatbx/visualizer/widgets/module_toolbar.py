"""
ATBX Pipeline Visualizer - Module Toolbar Widget

Left panel widget showing available ATBX modules for drag-and-drop.
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
                            QLineEdit, QHBoxLayout, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData, QByteArray
from PyQt6.QtGui import QIcon, QFont, QDrag, QPixmap
from typing import Dict, List, Optional
import json

from ..models.module_model import ModuleRegistry, ModuleInfo, ModuleCategory
from ..utils.theme import ATBXTheme


class ModuleToolbar(QWidget):
    """
    Toolbar widget displaying ATBX modules in a categorized tree.
    Supports drag-and-drop of modules to the canvas.
    """
    
    # Signal emitted when a module is dragged
    module_dragged = pyqtSignal(ModuleInfo)
    
    def __init__(self, module_registry: ModuleRegistry, parent=None):
        super().__init__(parent)
        self.module_registry = module_registry
        self.search_text = ""
        
        self._setup_ui()
        self._populate_modules()
        self._apply_theme()
    
    def _setup_ui(self):
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Search box
        search_layout = QHBoxLayout()
        search_layout.setSpacing(6)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search modules...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.search_edit)
        
        # Clear search button
        clear_btn = QPushButton("×")
        clear_btn.setFixedSize(24, 24)
        clear_btn.clicked.connect(self._clear_search)
        clear_btn.setStyleSheet("QPushButton { background: transparent; border: none; }"
                                  "QPushButton:hover { background: rgba(255,255,255,0.1); }")
        search_layout.addWidget(clear_btn)
        
        layout.addLayout(search_layout)
        
        # Module tree
        self.module_tree = QTreeWidget()
        self.module_tree.setHeaderHidden(True)
        self.module_tree.setIndentation(12)
        self.module_tree.setIconSize(QSize(16, 16))
        self.module_tree.setDragEnabled(True)
        self.module_tree.setDragDropMode(QTreeWidget.DragOnly)
        
        # Enable drag-and-drop
        self.module_tree.setMouseTracking(True)
        self.module_tree.itemPressed.connect(self._on_item_pressed)
        
        layout.addWidget(self.module_tree)
        
        # Set minimum width
        self.setMinimumWidth(200)
    
    def _apply_theme(self):
        """Apply ATBX dark theme."""
        colors = ATBXTheme.COLORS
        
        self.setStyleSheet(f"""
        ModuleToolbar {{
            background-color: {colors['bg_secondary']};
        }}
        QTreeWidget {{
            background-color: {colors['bg_secondary']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
            border-radius: 4px;
        }}
        QLineEdit {{
            background-color: {colors['bg_elevated']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        QPushButton {{
            background-color: {colors['bg_elevated']};
            color: {colors['text_primary']};
            border: 1px solid {colors['border_primary']};
            border-radius: 4px;
        }}
        QTreeWidget::item {{
            padding: 4px 8px;
            border-radius: 3px;
        }}
        QTreeWidget::item:selected {{
            background-color: {colors['bg_elevated']};
            border: 1px solid {colors['accent_primary']};
        }}
        QTreeWidget::item:hover {{
            background-color: {colors['bg_tertiary']};
        }}
        """)
    
    def _populate_modules(self):
        """Populate the tree with modules from registry."""
        self.module_tree.clear()
        
        # Create category items
        categories = {}
        for category in ModuleCategory:
            cat_item = QTreeWidgetItem(self.module_tree)
            cat_item.setText(0, category.value)
            cat_item.setData(0, Qt.UserRole, f"category:{category.value}")
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsDragEnabled)
            categories[category] = cat_item
        
        # Add modules to their categories
        for module_id, module_info in self.module_registry.modules.items():
            if self.search_text and self.search_text.lower() not in module_info.name.lower():
                continue
                
            category = ModuleCategory(module_info.category)
            if category in categories:
                ModuleTreeItem(categories[category], module_info)
    
    def _on_search_changed(self, text: str):
        """Handle search text changes."""
        self.search_text = text.lower()
        self._populate_modules()
    
    def _clear_search(self):
        """Clear search text."""
        self.search_edit.clear()
    
    def _on_item_pressed(self, item: QTreeWidgetItem, column: int):
        """Handle item press - initiate drag."""
        if not isinstance(item, ModuleTreeItem):
            return
        
        # Emit signal when drag starts
        self.module_dragged.emit(item.module_info)
        
        # Create drag object
        drag = QDrag(self)
        mime_data = QMimeData()
        
        # Serialize module info to JSON
        module_data = {
            'module_id': item.module_info.id,
            'name': item.module_info.name,
            'type': item.module_info.module_type.value,
            'script_path': item.module_info.script_path
        }
        mime_data.setData('application/x-atbx-module', 
                         json.dumps(module_data).encode('utf-8'))
        
        drag.setMimeData(mime_data)
        
        # Set drag pixmap
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        drag.setPixmap(pixmap)
        
        # Set hotspot
        from PyQt6.QtCore import QPoint
        drag.setHotSpot(QPoint(16, 16))
        
        # Start drag
        drag.exec(Qt.CopyAction)


class ModuleTreeItem(QTreeWidgetItem):
    """Tree widget item representing asingle ATBX module."""
    
    def __init__(self, parent: QTreeWidgetItem, module_info: ModuleInfo):
        super().__init__(parent)
        self.module_info = module_info
        
        self.setText(0, module_info.name)
        self.setToolTip(0, module_info.description)
        self.setData(0, Qt.UserRole, module_info.id)
        
        # Set flags
        self.setFlags(self.flags() | Qt.ItemIsDragEnabled)
