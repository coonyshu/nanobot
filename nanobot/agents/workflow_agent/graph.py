"""
Workflow Graph - Data structures for workflow configuration.

Builds an executable graph from workflow_definitions JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class NodeType(Enum):
    """Types of workflow nodes."""
    START = "start"           # Entry point
    PHOTO = "photo"           # Photo capture node
    FORM = "form"             # Form input node
    INSPECTION = "inspection" # Physical inspection
    CONDITION = "condition"   # Conditional branch
    END = "end"               # Terminal node


@dataclass
class ConditionalRoute:
    """A conditional edge in the workflow graph."""
    field: Optional[str] = None
    condition: Optional[str] = None  # equals, in, equals_true, not_empty, always
    value: Any = None
    next_node: str = ""
    label: str = ""


@dataclass
class WorkflowNode:
    """A node in the workflow graph."""
    id: str
    name: str
    type: NodeType
    description: str = ""
    required_fields: List[str] = field(default_factory=list)
    optional_fields: List[str] = field(default_factory=list)
    next_node: Union[str, List[ConditionalRoute], None] = None
    can_skip: bool = False
    guide_text: str = ""
    ai_recognition: List[str] = field(default_factory=list)
    estimated_duration: int = 60  # seconds
    tags: List[str] = field(default_factory=list)
    # field_key → {label, type, options, ...}
    field_definitions: Dict[str, Dict] = field(default_factory=dict)

    def get_field_label(self, field_key: str) -> str:
        """Return the human-readable label for a field key, or the key itself."""
        return self.field_definitions.get(field_key, {}).get("label", field_key)

    def keys_to_labels(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a {field_key: value} dict to {label: value} for frontend display."""
        return {self.get_field_label(k): v for k, v in data.items()}

    def get_next_node(self, collected_data: Dict[str, Any]) -> Optional[str]:
        """Determine next node based on collected data (same logic as backend)."""
        if self.next_node is None:
            return None
        
        if isinstance(self.next_node, str):
            return self.next_node
        
        # Conditional routing
        for route in self.next_node:
            if route.field is None or route.condition == "always":
                return route.next_node
            
            field_value = collected_data.get(route.field)
            
            if route.condition == "equals" and field_value == route.value:
                return route.next_node
            elif route.condition == "equals_true" and bool(field_value):
                return route.next_node
            elif route.condition == "in" and field_value in (route.value or []):
                return route.next_node
            elif route.condition == "not_empty" and field_value:
                return route.next_node
        
        return None


@dataclass
class WorkflowGraph:
    """Complete workflow graph built from definitions."""
    workflow_id: str
    workflow_name: str
    description: str
    nodes: Dict[str, WorkflowNode] = field(default_factory=dict)
    start_node_id: str = ""
    version: str = "1.0"
    work_type: str = "work"  # Display name for the work type (e.g., "安检", "维修")
    
    @classmethod
    def from_definitions(cls, definitions: Dict[str, Any]) -> WorkflowGraph:
        """Build graph from workflow_definitions JSON."""
        graph = cls(
            workflow_id=definitions.get("workflow_id", "default"),
            workflow_name=definitions.get("workflow_name", "Workflow"),
            description=definitions.get("description", ""),
            version=definitions.get("version", "1.0"),
            work_type=definitions.get("work_type", "work")
        )
        
        # Build nodes
        for node_def in definitions.get("nodes", []):
            node = WorkflowNode(
                id=node_def["id"],
                name=node_def["name"],
                type=NodeType(node_def.get("type", "form")),
                description=node_def.get("node_description", ""),
                required_fields=node_def.get("required_fields", []),
                optional_fields=node_def.get("optional_fields", []),
                can_skip=node_def.get("can_skip", False),
                guide_text=node_def.get("guide_text", ""),
                ai_recognition=node_def.get("ai_recognition", []),
                estimated_duration=node_def.get("estimated_duration_seconds", 60),
                tags=node_def.get("tags", []),
                field_definitions=node_def.get("field_definitions", {})
            )
            
            # Parse next_node (string or conditional array)
            next_def = node_def.get("next_node")
            if isinstance(next_def, str):
                node.next_node = next_def
            elif isinstance(next_def, list):
                node.next_node = [
                    ConditionalRoute(
                        field=r.get("field"),
                        condition=r.get("condition"),
                        value=r.get("value"),
                        next_node=r.get("next", ""),
                        label=r.get("label", "")
                    )
                    for r in next_def
                ]
            
            graph.nodes[node.id] = node
        
        # Find start node (lowest order or first in list)
        if graph.nodes:
            graph.start_node_id = min(graph.nodes.keys(), 
                                      key=lambda k: definitions.get("nodes", []).index(
                                          next(n for n in definitions["nodes"] if n["id"] == k)
                                      ))
        
        return graph
    
    def get_node(self, node_id: str) -> Optional[WorkflowNode]:
        """Get node by ID."""
        return self.nodes.get(node_id)
    
    def get_execution_path(self) -> List[str]:
        """Get linear execution path (for simple workflows)."""
        path = []
        current = self.start_node_id
        visited = set()
        
        while current and current not in visited:
            visited.add(current)
            path.append(current)
            node = self.nodes.get(current)
            if node and isinstance(node.next_node, str):
                current = node.next_node
            else:
                break
        
        return path
