"""
Pipeline Canvas - Central panel for workflow visualization
"""
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QPainterPath
from typing import Optional, Dict

from ..models.pipeline_model import Pipeline, PipelineNode
from ..utils.theme import ATBXTheme


class Canvas(QGraphicsView):
    """Canvas for displaying and editing pipeline workflow."""
    
    node_clicked = pyqtSignal(PipelineNode)
    node_double_clicked = pyqtSignal(PipelineNode)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.pipeline: Optional[Pipeline] = None
        self._setup_scene()
        self._setup_view()
        self._apply_theme()
    
    def _setup_scene(self):
        """Set up graphics scene."""
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
    
    def _setup_view(self):
        """Configure view settings."""
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    
    def _apply_theme(self):
        """Apply dark theme colors."""
        colors = ATBXTheme.COLORS
        bg_color = QColor(colors['bg_primary'])
        self.setBackgroundBrush(QBrush(bg_color))
        self.scene.setBackgroundBrush(QBrush(bg_color))
    
    def set_pipeline(self, pipeline: Pipeline):
        """Set the pipeline to display."""
        self.pipeline = pipeline
        self._redraw()
    
    def _redraw(self):
        """Redraw all nodes and connections."""
        self.scene.clear()
        
        if not self.pipeline:
            return
        
        # Draw connections first (so they appear behind nodes)
        self._draw_connections()
        
        # Draw nodes
        self._draw_nodes()
    
    def _draw_nodes(self):
        """Draw all nodes in the pipeline."""
        if not self.pipeline:
            return
        
        for node in self.pipeline.nodes.values():
            self._draw_node(node)
    
    def _draw_node(self, node: PipelineNode):
        """Draw a single node."""
        # Create node item
        node_item = NodeItem(node)
        node_item.setPos(node.position[0], node.position[1])
        node_item.node_clicked.connect(self._on_node_clicked)
        node_item.node_double_clicked.connect(self._on_node_double_clicked)
        self.scene.addItem(node_item)
    
    def _draw_connections(self):
        """Draw all connections."""
        if not self.pipeline:
            return
        
        for conn in self.pipeline.connections.values():
            self._draw_connection(conn)
    
    def _draw_connection(self, connection):
        """Draw a connection between two nodes."""
        source_node = self.pipeline.get_node_by_id(connection.source_node_id)
        target_node = self.pipeline.get_node_by_id(connection.target_node_id)
        
        if source_node and target_node:
            conn_item = ConnectionItem(
                source_node, target_node,
                connection.source_channel, connection.target_channel
            )
            self.scene.addItem(conn_item)
            conn_item.setZValue(-1)  # Draw behind nodes
    
    def _on_node_clicked(self, node: PipelineNode):
        """Handle node click."""
        self.node_clicked.emit(node)
    
    def _on_node_double_clicked(self, node: PipelineNode):
        """Handle node double click."""
        self.node_double_clicked.emit(node)
    
    def mousePressEvent(self, event):
        """Handle mouse press for potential drag-and-drop."""
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        """Handle mouse movement."""
        super().mouseMoveEvent(event)
    
    def contextMenuEvent(self, event):
        """Handle context menu."""
        super().contextMenuEvent(event)


class NodeItem(QGraphicsItem):
    """Graphics item representing a pipeline node."""
    
    node_clicked = pyqtSignal(PipelineNode)
    node_double_clicked = pyqtSignal(PipelineNode)
    
    WIDTH = 140
    HEIGHT = 100
    CORNER_RADIUS = 8
    
    def __init__(self, node: PipelineNode, parent=None):
        super().__init__(parent)
        self.node = node
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
    
    def boundingRect(self):
        """Return bounding rectangle."""
        return QRectF(0, 0, self.WIDTH, self.HEIGHT)
    
    def paint(self, painter, option, widget=None):
        """Paint the node."""
        colors = ATBXTheme.COLORS
        
        # Get status color
        status_color = ATBXTheme.get_status_color(self.node.status.value)
        
        # Draw background
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background rectangle with rounded corners
        pen = QPen(QColor(colors['border_primary']), 1)
        brush = QBrush(status_color)
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawRoundedRect(0, 0, self.WIDTH, self.HEIGHT, 
                               self.CORNER_RADIUS, self.CORNER_RADIUS)
        
        # Draw node name
        painter.setPen(QColor(colors['text_primary']))
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        
        # Truncate long names
        name = self.node.module_info.name
        if len(name) > 20:
            name = name[:17] + "..."
        
        painter.drawText(QRectF(4, 4, self.WIDTH - 8, 20), 
                        Qt.AlignmentFlag.AlignCenter, name)
        
        # Draw module type
        painter.setPen(QColor(colors['text_secondary']))
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(QRectF(4, 24, self.WIDTH - 8, 16), 
                        Qt.AlignmentFlag.AlignCenter,
                        self.node.module_info.module_type.value.upper())
        
        # Draw participant count
        participant_text = f"{len(self.node.participants)} participants"
        painter.drawText(QRectF(4, self.HEIGHT - 24, self.WIDTH - 8, 16),
                        Qt.AlignmentFlag.AlignCenter,
                        participant_text)
    
    def mousePressEvent(self, event):
        """Handle mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.node_clicked.emit(self.node)
        super().mousePressEvent(event)
    
    def mouseDoubleClickEvent(self, event):
        """Handle double click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.node_double_clicked.emit(self.node)
        super().mouseDoubleClickEvent(event)
    
    def hoverEnterEvent(self, event):
        """Handle hover enter."""
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverEnterEvent(event)
    
    def hoverLeaveEvent(self, event):
        """Handle hover leave."""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(event)


class ConnectionItem(QGraphicsItem):
    """Graphics item representing a connection between nodes."""
    
    def __init__(self, source_node: PipelineNode, target_node: PipelineNode,
                 source_channel: str = "", target_channel: str = "", parent=None):
        super().__init__(parent)
        self.source_node = source_node
        self.target_node = target_node
        self.source_channel = source_channel
        self.target_channel = target_channel
        self.setZValue(-1)
    
    def boundingRect(self):
        """Return bounding rectangle."""
        return self.shape().boundingRect()
    
    def paint(self, painter, option, widget=None):
        """Paint the connection."""
        colors = ATBXTheme.COLORS
        
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(colors['border_primary']), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        
        # Get start and end points
        start_pos = self._get_connection_point(self.source_node, is_source=True)
        end_pos = self._get_connection_point(self.target_node, is_source=False)
        
        # Draw Bezier curve
        control1 = QPointF(start_pos.x() + 80, start_pos.y())
        control2 = QPointF(end_pos.x() - 80, end_pos.y())
        
        path = QPainterPath()
        path.moveTo(start_pos)
        path.cubicTo(control1, control2, end_pos)
        painter.drawPath(path)
        
        # Draw arrowhead
        self._draw_arrowhead(painter, end_pos, path)
    
    def _get_connection_point(self, node: PipelineNode, is_source: bool):
        """Get connection point on node edge."""
        x, y = node.position
        
        if is_source:
            return QPointF(x + NodeItem.WIDTH, y + NodeItem.HEIGHT // 2)
        else:
            return QPointF(x, y + NodeItem.HEIGHT // 2)
    
    def _draw_arrowhead(self, painter, end_point: QPointF, path: QPainterPath):
        """Draw arrowhead at end of connection."""
        colors = ATBXTheme.COLORS
        
        # Get direction vector
        length = path.length()
        if length > 20:
            # Go back 10 pixels from end
            percent = (length - 10) / length
            arrow_base = path.pointAtPercent(percent)
            
            # Arrow points
            arrow_size = 8
            angle = 30  # degrees
            
            # Direction from arrow_base to end_point
            dx = end_point.x() - arrow_base.x()
            dy = end_point.y() - arrow_base.y()
            
            # Normalize
            length = (dx ** 2 + dy ** 2) ** 0.5
            if length > 0:
                dx = dx / length * arrow_size
                dy = dy / length * arrow_size
            
            # Calculate arrow points
            angle_rad = 3.14159 * angle / 180
            
            point1 = QPointF(
                end_point.x(),
                end_point.y()
            )
            point2 = QPointF(
                end_point.x() - dx * 0.8 - dy * 0.6,
                end_point.y() - dy * 0.8 + dx * 0.6
            )
            point3 = QPointF(
                end_point.x() - dx * 0.8 + dy * 0.6,
                end_point.y() - dy * 0.8 - dx * 0.6
            )
            
            # Draw arrow
            brush = QBrush(QColor(colors['border_primary']))
            painter.setBrush(brush)
            painter.drawPolygon([point1, point2, point3])



