"""
Pipeline Model - Data models for pipeline visualization
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import json
from .module_model import ModuleInfo


class NodeStatus(Enum):
    """Status of a pipeline node."""
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(Enum):
    """Overall pipeline status."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class PipelineConnection:
    """Represents a connection between two nodes."""
    connection_id: str
    source_node_id: str
    target_node_id: str
    source_channel: str = ""
    target_channel: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class PipelineNode:
    """Represents a node in the pipeline."""
    node_id: str
    module_info: ModuleInfo
    position: Tuple[float, float] = (0, 0)
    status: NodeStatus = NodeStatus.PENDING
    parameters: Dict[str, Any] = field(default_factory=dict)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    execution_time: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            **asdict(self),
            'module_info': self.module_info.to_dict(),
            'status': self.status.value,
            'position': list(self.position)
        }


@dataclass
class Pipeline:
    """Represents a complete pipeline."""
    pipeline_id: str
    name: str
    description: str = ""
    nodes: Dict[str, PipelineNode] = field(default_factory=dict)
    connections: Dict[str, PipelineConnection] = field(default_factory=dict)
    status: PipelineStatus = PipelineStatus.IDLE
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def add_node(self, module_info: ModuleInfo, position: Tuple[float, float] = (0, 0)) -> PipelineNode:
        """Add a new node to the pipeline."""
        import uuid
        node = PipelineNode(
            node_id=str(uuid.uuid4()),
            module_info=module_info,
            position=position
        )
        self.nodes[node.node_id] = node
        return node
    
    def remove_node(self, node_id: str) -> bool:
        """Remove a node from the pipeline."""
        if node_id in self.nodes:
            # Remove any connections involving this node
            self.connections = {
                conn_id: conn for conn_id, conn in self.connections.items()
                if conn.source_node_id != node_id and conn.target_node_id != node_id
            }
            del self.nodes[node_id]
            return True
        return False
    
    def add_connection(self, source_node_id: str, target_node_id: str, 
                      source_channel: str = "", target_channel: str = "") -> Optional[PipelineConnection]:
        """Add a connection between two nodes."""
        import uuid
        if source_node_id not in self.nodes or target_node_id not in self.nodes:
            return None
        
        connection = PipelineConnection(
            connection_id=str(uuid.uuid4()),
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_channel=source_channel,
            target_channel=target_channel
        )
        self.connections[connection.connection_id] = connection
        return connection
    
    def remove_connection(self, connection_id: str) -> bool:
        """Remove a connection."""
        if connection_id in self.connections:
            del self.connections[connection_id]
            return True
        return False
    
    def get_node_by_id(self, node_id: str) -> Optional[PipelineNode]:
        """Get a node by its ID."""
        return self.nodes.get(node_id)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert pipeline to dictionary."""
        return {
            'pipeline_id': self.pipeline_id,
            'name': self.name,
            'description': self.description,
            'nodes': {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            'connections': {conn_id: conn.to_dict() for conn_id, conn in self.connections.items()},
            'status': self.status.value,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }