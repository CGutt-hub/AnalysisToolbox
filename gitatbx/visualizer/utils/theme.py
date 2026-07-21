"""
ATBX Pipeline Visualizer - Theme System

Applies the 5ha99y dark color scheme to the PyQt6 application.
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtCore import Qt


class ATBXTheme:
    """
    Dark theme based on 5ha99y website design.
    
    Color Palette:
    - Backgrounds: #0f0f0f (primary), #161616 (secondary), #242424 (elevated)
    - Text: #e8e8e8 (primary), #a0a0a0 (secondary), #6a6a6a (muted)
    - Accent: #c9a227 (gold)
    - Borders: #2a2a2a
    """
    
    # Color definitions
    COLORS = {
        # Backgrounds
        'bg_primary': '#0f0f0f',
        'bg_secondary': '#161616', 
        'bg_tertiary': '#1c1c1c',
        'bg_elevated': '#242424',
        
        # Text
        'text_primary': '#e8e8e8',
        'text_secondary': '#a0a0a0',
        'text_muted': '#6a6a6a',
        'text_disabled': '#4a4a4a',
        
        # Accents
        'accent_primary': '#c9a227',
        'accent_hover': '#ddb52f',
        'accent_pressed': '#b8951d',
        
        # Borders
        'border_primary': '#2a2a2a',
        'border_subtle': '#1f1f1f',
        
        # Status colors for nodes
        'status_fail': '#ff6b6b',        # Red - complete module failure
        'status_partial': '#ffd166',    # Yellow - partial/one dataset failed
        'status_success': '#06d6a0',    # Green - no errors
        'status_inactive': '#118ab2',   # Blue - inactive/pending
        'status_running': '#06b6d4',    # Cyan - currently running
        
        # Code highlighting
        'code_bg': '#0a0a0a',
        'code_text': '#e6b450',
        'code_comment': '#6a737d',
        
        # Shadows (RGBA)
        'shadow_sm': 'rgba(0, 0, 0, 0.3)',
        'shadow_md': 'rgba(0, 0, 0, 0.4)',
        'shadow_lg': 'rgba(0, 0, 0, 0.5)',
    }
    
    @classmethod
    def apply_to_app(cls, app):
        """Apply the dark theme to the QApplication."""
        palette = cls._create_palette()
        app.setPalette(palette)
        app.setStyleSheet(cls.get_stylesheet())
    
    @classmethod
    def _create_palette(cls):
        """Create a QPalette with dark theme colors."""
        palette = QPalette()
        
        # Window colors
        palette.setColor(QPalette.Window, QColor(cls.COLORS['bg_primary']))
        palette.setColor(QPalette.WindowText, QColor(cls.COLORS['text_primary']))
        
        # Button colors
        palette.setColor(QPalette.Button, QColor(cls.COLORS['bg_elevated']))
        palette.setColor(QPalette.ButtonText, QColor(cls.COLORS['text_primary']))
        palette.setColor(QPalette.BrightText, Qt.red)
        
        # Base colors (for text entries)
        palette.setColor(QPalette.Base, QColor(cls.COLORS['bg_secondary']))
        palette.setColor(QPalette.Text, QColor(cls.COLORS['text_primary']))
        
        # Alternate base (for alternate row colors)
        palette.setColor(QPalette.AlternateBase, QColor(cls.COLORS['bg_tertiary']))
        
        # Tool tips
        palette.setColor(QPalette.ToolTipBase, QColor(cls.COLORS['bg_elevated']))
        palette.setColor(QPalette.ToolTipText, QColor(cls.COLORS['text_primary']))
        
        # Disabled states
        palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(cls.COLORS['text_disabled']))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(cls.COLORS['text_disabled']))
        
        # Highlights
        palette.setColor(QPalette.Highlight, QColor(cls.COLORS['accent_primary']))
        palette.setColor(QPalette.HighlightedText, QColor(cls.COLORS['bg_primary']))
        
        # Links
        palette.setColor(QPalette.Link, QColor(cls.COLORS['accent_primary']))
        palette.setColor(QPalette.LinkVisited, QColor(cls.COLORS['accent_hover']))
        
        return palette
    
    @classmethod
    def get_stylesheet(cls):
        """Generate CSS stylesheet for custom widgets."""
        c = cls.COLORS
        return f"""
        /* Main Window */
        QMainWindow {{
            background-color: {c['bg_primary']};
        }}
        
        /* Widgets */
        QWidget {{
            background-color: {c['bg_secondary']};
            color: {c['text_primary']};
            border: none;
        }}
        
        /* List Widget (Module Toolbar) */
        QListWidget {{
            background-color: {c['bg_secondary']};
            border: 1px solid {c['border_primary']};
            padding: 4px;
        }}
        QListWidget::item {{
            padding: 6px 8px;
            border-radius: 3px;
        }}
        QListWidget::item:selected {{
            background-color: {c['bg_elevated']};
            border: 1px solid {c['accent_primary']};
        }}
        QListWidget::item:hover {{
            background-color: {c['bg_tertiary']};
        }}
        
        /* Graphics View (Canvas) */
        QGraphicsView {{
            background-color: {c['bg_primary']};
            border: none;
        }}
        
        /* Buttons */
        QPushButton {{
            background-color: {c['bg_elevated']};
            color: {c['text_primary']};
            border: 1px solid {c['border_primary']};
            border-radius: 4px;
            padding: 6px 12px;
            min-width: 80px;
        }}
        QPushButton:hover {{
            background-color: {c['accent_primary']};
            color: {c['bg_primary']};
        }}
        QPushButton:pressed {{
            background-color: {c['accent_pressed']};
        }}
        QPushButton:disabled {{
            background-color: {c['bg_tertiary']};
            color: {c['text_disabled']};
        }}
        
        /* Line Edits */
        QLineEdit {{
            background-color: {c['bg_elevated']};
            color: {c['text_primary']};
            border: 1px solid {c['border_primary']};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        QLineEdit:focus {{
            border: 1px solid {c['accent_primary']};
        }}
        
        /* Combo Box */
        QComboBox {{
            background-color: {c['bg_elevated']};
            color: {c['text_primary']};
            border: 1px solid {c['border_primary']};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        QComboBox::drop-down {{
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {c['bg_secondary']};
            color: {c['text_primary']};
            border: 1px solid {c['border_primary']};
            selection-background-color: {c['accent_primary']};
        }}
        
        /* Tab Widget */
        QTabWidget::pane {{
            border: 1px solid {c['border_primary']};
            background-color: {c['bg_secondary']};
        }}
        QTabBar::tab {{
            background-color: {c['bg_elevated']};
            color: {c['text_secondary']};
            border: 1px solid {c['border_primary']};
            padding: 6px 12px;
            border-bottom: none;
        }}
        QTabBar::tab:selected {{
            background-color: {c['bg_secondary']};
            color: {c['text_primary']};
            border-bottom: 2px solid {c['accent_primary']};
        }}
        QTabBar::tab:hover:!selected {{
            background-color: {c['bg_tertiary']};
        }}
        
        /* Scrollbars */
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {c['bg_secondary']};
            border: none;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {c['border_primary']};
            min-height: 20px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
            background: {c['accent_primary']};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            background: none;
            border: none;
        }}
        
        /* Group Box */
        QGroupBox {{
            border: 1px solid {c['border_primary']};
            border-radius: 4px;
            margin-top: 10px;
            padding: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            color: {c['accent_primary']};
        }}
        
        /* Checkboxes */
        QCheckBox {{
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
        }}
        QCheckBox::indicator:checked {{
            background-color: {c['accent_primary']};
            border: 1px solid {c['accent_primary']};
        }}
        QCheckBox::indicator:unchecked {{
            border: 1px solid {c['border_primary']};
            background-color: transparent;
        }}
        
        /* Status Node Colors (for canvas items) */
        .node-fail {{
            background-color: {c['status_fail']};
        }}
        .node-partial {{
            background-color: {c['status_partial']};
        }}
        .node-success {{
            background-color: {c['status_success']};
        }}
        .node-inactive {{
            background-color: {c['status_inactive']};
        }}
        """
    
    @classmethod
    def get_color(cls, color_name):
        """Get a QColor by name."""
        if color_name in cls.COLORS:
            return QColor(cls.COLORS[color_name])
        return QColor('#ffffff')  # Fallback
    
    @classmethod
    def get_status_color(cls, status):
        """Get color for a node status."""
        status_colors = {
            'fail': 'status_fail',
            'partial': 'status_partial',
            'success': 'status_success',
            'inactive': 'status_inactive',
            'running': 'status_running',
        }
        return cls.get_color(status_colors.get(status, 'text_muted'))
