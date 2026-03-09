"""
Tool Executor - Executes MCP and frontend tools.

This is the bridge between WorkflowRunner and actual tool calls.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class ToolType(Enum):
    """Types of tools."""
    MCP = "mcp"           # Backend MCP tools
    FRONTEND = "frontend" # Frontend UI tools
    INPUT = "input"       # User input tools (camera, etc.)


@dataclass
class ToolCall:
    """A tool call specification."""
    tool_type: ToolType
    tool_name: str
    params: Dict[str, Any]
    description: str = ""
    required: bool = True


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    data: Any
    error: Optional[str] = None
    next_action: Optional[str] = None  # Hint for next step


class ToolExecutor:
    """Executes tools and manages their results."""
    
    def __init__(self, mcp_caller: Optional[Callable] = None, 
                 frontend_caller: Optional[Callable] = None):
        """
        Initialize executor.
        
        Args:
            mcp_caller: Function to call MCP tools (name, params) -> result
            frontend_caller: Function to call frontend tools (name, params) -> result
        """
        self.mcp_caller = mcp_caller
        self.frontend_caller = frontend_caller
        self.execution_log: List[Dict] = []
    
    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call."""
        self.execution_log.append({
            "tool": call.tool_name,
            "type": call.tool_type.value,
            "params": call.params
        })
        
        try:
            if call.tool_type == ToolType.MCP and self.mcp_caller:
                result = await self.mcp_caller(call.tool_name, call.params)
                return self._parse_result(result)
            
            elif call.tool_type == ToolType.FRONTEND and self.frontend_caller:
                result = await self.frontend_caller(call.tool_name, call.params)
                return self._parse_result(result)
            
            else:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"No caller available for tool type: {call.tool_type}"
                )
        
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=str(e)
            )
    
    def _parse_result(self, result: Any) -> ToolResult:
        """Parse tool result into standard format."""
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                return ToolResult(success=True, data=result)
        
        if isinstance(result, dict):
            success = result.get("success", True)
            error = result.get("error") or result.get("message") if not success else None
            next_action = result.get("_required_frontend_actions") or result.get("_next_action_hint")
            
            return ToolResult(
                success=success,
                data=result,
                error=error,
                next_action=str(next_action) if next_action else None
            )
        
        return ToolResult(success=True, data=result)
    
    async def execute_sequence(self, calls: List[ToolCall]) -> List[ToolResult]:
        """Execute a sequence of tool calls, stopping on first required failure."""
        results = []
        
        for call in calls:
            result = await self.execute(call)
            results.append(result)
            
            if not result.success and call.required:
                break
        
        return results
    
    # Predefined tool call builders
    
    @staticmethod
    def mcp_get_workflow_definitions() -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_get_workflow_definitions",
            params={},
            description="Get workflow configuration from backend"
        )
    
    @staticmethod
    def mcp_load_workflow_task(user_id: str, address: Optional[str] = None) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_load_workflow_task",
            params={"user_id": user_id, "address": address},
            description="Load or create workflow task"
        )
    
    @staticmethod
    def mcp_get_workflow_status(task_id: str) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_get_workflow_status",
            params={"task_id": task_id},
            description="Get current workflow status"
        )
    
    @staticmethod
    def mcp_update_node_data(task_id: str, node_id: str, data: Dict) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_update_node_data",
            params={"task_id": task_id, "node_id": node_id, "data": data},
            description="Update node data in backend"
        )
    
    @staticmethod
    def mcp_check_node_completion(task_id: str, node_id: str) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_check_node_completion",
            params={"task_id": task_id, "node_id": node_id},
            description="Check if node is complete"
        )
    
    @staticmethod
    def mcp_transition_to_next_node(task_id: str, fields: Optional[Dict] = None) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_transition_to_next_node",
            params={"task_id": task_id, "fields": fields or {}},
            description="Transition to next node (backend routing)"
        )
    
    @staticmethod
    def mcp_jump_to_node(task_id: str, target_node_id: str, reason: Optional[str] = None) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_jump_to_node",
            params={"task_id": task_id, "target_node_id": target_node_id, "reason": reason},
            description="Jump to any specified node (manual intervention)"
        )
    
    @staticmethod
    def frontend_get_status() -> ToolCall:
        return ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_get_status",
            params={},
            description="Check if work form is open"
        )
    
    @staticmethod
    def frontend_open_form(user_id: str, work_type: str, address: str, 
                          task_id: str, **kwargs) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_open_form",
            params={
                "userId": user_id,
                "workType": work_type,
                "address": address,
                "task_id": task_id,
                **kwargs
            },
            description="Open work form UI"
        )
    
    @staticmethod
    def frontend_update_node_status(node_id: str, status: str) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_update_node_status",
            params={"node_id": node_id, "status": status},
            description="Update node status in UI"
        )
    
    @staticmethod
    def frontend_update_node_fields(node_id: str, fields: Dict) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_update_node_fields",
            params={"node_id": node_id, "fields": fields},
            description="Update node fields in UI"
        )

    @staticmethod
    def frontend_restore_node_photos(node_id: str, photo_urls: list) -> ToolCall:
        return ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_restore_node_photos",
            params={"node_id": node_id, "photo_urls": photo_urls},
            description="Restore photo thumbnails from backend URLs"
        )
