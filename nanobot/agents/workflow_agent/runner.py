"""
Workflow Runner - Core execution engine.

Orchestrates workflow execution by:
1. Loading workflow configuration
2. Managing execution state
3. Executing node templates
4. Handling transitions
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

from loguru import logger

from .graph import WorkflowGraph, WorkflowNode, NodeType
from .executor import ToolExecutor, ToolCall, ToolResult, ToolType


class RunnerState(Enum):
    """States of the workflow runner."""
    IDLE = "idle"
    LOADING = "loading"
    EXECUTING_NODE = "executing_node"
    WAITING_INPUT = "waiting_input"
    TRANSITIONING = "transitioning"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class NodeExecutionContext:
    """Context for executing a node."""
    node_id: str
    task_id: str
    user_id: str
    collected_data: Dict[str, Any] = field(default_factory=dict)
    photos: List[str] = field(default_factory=list)
    is_resuming: bool = False


@dataclass
class ExecutionResult:
    """Result of a workflow execution step."""
    state: RunnerState
    message: str
    requires_user_input: bool = False
    next_actions: List[ToolCall] = field(default_factory=list)
    error: Optional[str] = None


class WorkflowRunner:
    """
    Workflow execution runner - the brain of the workflow agent.
    
    This class implements the execution logic that was previously
    scattered in SKILL.md rules. It provides explicit control flow.
    """
    
    def __init__(self, executor: ToolExecutor):
        """Initialize runner with tool executor."""
        self.executor = executor
        self.graph: Optional[WorkflowGraph] = None
        self.state = RunnerState.IDLE
        self.current_task_id: Optional[str] = None
        self.current_node_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self.execution_history: List[Dict] = []
    
    async def initialize(self, definitions: Optional[Dict] = None) -> ExecutionResult:
        """
        Initialize workflow by loading definitions.
        
        If definitions not provided, fetches from backend.
        """
        self.state = RunnerState.LOADING
        
        if definitions is None:
            # Fetch from backend
            result = await self.executor.execute(
                ToolExecutor.mcp_get_workflow_definitions()
            )
            if not result.success:
                self.state = RunnerState.ERROR
                return ExecutionResult(
                    state=RunnerState.ERROR,
                    message="Failed to load workflow definitions",
                    error=result.error
                )
            definitions = result.data
            # Unwrap: backend returns {"success": true, "data": {...defs...}}
            if isinstance(definitions, dict) and 'data' in definitions and 'nodes' not in definitions:
                definitions = definitions.get('data', definitions)

        self.graph = WorkflowGraph.from_definitions(definitions)
        self.state = RunnerState.IDLE
        
        return ExecutionResult(
            state=RunnerState.IDLE,
            message=f"Workflow '{self.graph.workflow_name}' loaded with {len(self.graph.nodes)} nodes"
        )
    
    async def start_task(self, user_id: str, address: Optional[str] = None) -> ExecutionResult:
        """
        Start or resume a workflow task (Dify-style: auto-execute all tools).
        
        Template 1 from SKILL.md: Task Startup
        """
        self.user_id = user_id
        
        # Step 0: Check if form is already open
        status_result = await self.executor.execute(
            ToolExecutor.frontend_get_status()
        )
        
        if status_result.success and status_result.data.get("is_open"):
            # Form already open, get current state from frontend
            self.current_task_id = status_result.data.get("task_id")
            self.current_node_id = status_result.data.get("current_node")
            
            # Always load from backend to restore collected_data and photo status
            logger.info("[start_task] Form already open, loading from backend to restore state...")
            load_result = await self.executor.execute(
                ToolExecutor.mcp_load_workflow_task(user_id, address)
            )
            if load_result.success and load_result.data.get("current_task"):
                task_data = load_result.data["current_task"]
                self.current_task_id = task_data.get("task_id")
                self.current_node_id = task_data.get("current_node")
                # Restore collected_data to runner state
                self.collected_data = task_data.get("collected_data", {})
                logger.info(f"[start_task] Loaded task_id={self.current_task_id}, collected_data={self.collected_data}")
                # Update frontend with correct task_id if not set
                if not status_result.data.get("task_id"):
                    await self.executor.execute(
                        ToolExecutor.frontend_open_form(
                            user_id=user_id,
                            work_type=self.graph.work_type if self.graph else "work",
                            address=address or "",
                            task_id=self.current_task_id
                        )
                    )
                # Restore photos to frontend
                await self._restore_photos_to_frontend(self.collected_data)
            
            # Get current node guide
            guide = self._get_node_guide(self.current_node_id)
            
            node_name = ""
            if self.graph and self.current_node_id:
                node = self.graph.get_node(self.current_node_id)
                node_name = node.name if node else self.current_node_id
            
            # Check photo status for current node
            current_node_data = self.collected_data.get(self.current_node_id, {})
            photo_status = "✓ uploaded" if current_node_data.get("photo") == "uploaded" else "⚠️ pending"
            
            return ExecutionResult(
                state=RunnerState.EXECUTING_NODE,
                message=f"Work form already on screen, currently at [{node_name}] node. Photo status: {photo_status}. {guide}",
                requires_user_input=True,
                next_actions=[]
            )
        
        # Step 3: Load workflow task
        load_result = await self.executor.execute(
            ToolExecutor.mcp_load_workflow_task(user_id, address)
        )
        
        if not load_result.success:
            error_code = load_result.data.get("error") if isinstance(load_result.data, dict) else None
            if error_code == "already_in_progress":
                return await self._resume_task(load_result.data)
            if error_code == "task_not_found":
                return await self._show_address_alternatives(address)
            return ExecutionResult(
                state=RunnerState.ERROR,
                message="Failed to load task",
                error=load_result.error
            )
        
        # New task
        self.current_task_id = load_result.data.get("task_id")
        self.current_node_id = self.graph.start_node_id if self.graph else None
        
        # Dify-style: Auto-execute frontend tools internally
        # Open form
        await self.executor.execute(
            ToolExecutor.frontend_open_form(
                user_id=user_id,
                work_type=self.graph.work_type if self.graph else "work",
                address=address or "",
                task_id=self.current_task_id
            )
        )
        
        # Activate first node
        await self.executor.execute(
            ToolExecutor.frontend_update_node_status(
                node_id=self.current_node_id,
                status="active"
            )
        )
        
        self.state = RunnerState.EXECUTING_NODE
        
        # Get guide for first node
        guide = self._get_node_guide(self.current_node_id)
        
        return ExecutionResult(
            state=RunnerState.EXECUTING_NODE,
            message=f"Task started: {self.current_task_id}. {guide}",
            requires_user_input=True,
            next_actions=[]
        )
    
    async def _show_address_alternatives(self, address: Optional[str]) -> ExecutionResult:
        """When task not found, search for similar addresses and show task list in frontend."""
        # Extract a short keyword from the address (neighborhood name preferred)
        keyword = ""
        if address:
            if "小区" in address:
                idx = address.index("小区")
                start = max(0, idx - 4)
                keyword = address[start:idx + 2]  # e.g. "滨江小区"
            else:
                keyword = address[:6]

        search_result = await self.executor.execute(ToolCall(
            tool_type=ToolType.MCP,
            tool_name="mcp_workflow-engine_get_workflow_task_list",
            params={"address_keyword": keyword} if keyword else {},
            description="Search tasks by address keyword"
        ))

        tasks = []
        filter_summary = f"地址搜索: {address or '未知'}"
        total = 0
        if search_result.success and isinstance(search_result.data, dict):
            tasks = search_result.data.get("tasks", [])
            filter_summary = search_result.data.get("filter_summary", filter_summary)
            total = search_result.data.get("total", len(tasks))

        # Show task list in frontend for user to select
        await self.executor.execute(ToolCall(
            tool_type=ToolType.FRONTEND,
            tool_name="work_form_show_task_list",
            params={"tasks": tasks, "filter_summary": filter_summary, "total": total},
            description="Show address search results for user selection"
        ))

        if tasks:
            return ExecutionResult(
                state=RunnerState.IDLE,
                message=(
                    f"No exact match found for address '{address}', displaying similar address list ({len(tasks)} items) in frontend."
                    "Please ask user to select correct address from list, then restart workflow."
                ),
                requires_user_input=True
            )
        return ExecutionResult(
            state=RunnerState.ERROR,
            message=f"No workflow user found for address '{address}', please confirm address and retry.",
            error="task_not_found"
        )

    async def _resume_task(self, data: Dict) -> ExecutionResult:
        """Resume an existing task (Dify-style: auto-execute all tools)."""
        current_task = data.get("current_task", {})
        self.current_task_id = current_task.get("task_id")
        self.current_node_id = current_task.get("current_node")
        logger.info(f"[_resume_task] data={data}, current_task={current_task}, task_id={self.current_task_id}")
        completed_nodes = current_task.get("completed_nodes", [])
        collected_data = current_task.get("collected_data", {})
        
        # Dify-style: Auto-execute frontend tools internally
        # Open form
        try:
            logger.info("[_resume_task] Calling frontend_open_form...")
            result = await self.executor.execute(
                ToolExecutor.frontend_open_form(
                    user_id=self.user_id,
                    work_type=self.graph.work_type if self.graph else "work",
                    address=current_task.get("user_info", {}).get("address", ""),
                    task_id=self.current_task_id,
                    warnings=current_task.get("warnings", []),
                    meterInfo=current_task.get("meter_info", {})
                )
            )
            logger.info(f"[_resume_task] frontend_open_form result={result}")
        except Exception as e:
            logger.error(f"[_resume_task] frontend_open_form failed: {e}")
            raise
        
        # Restore completed nodes
        for node_id in completed_nodes:
            await self.executor.execute(
                ToolExecutor.frontend_update_node_status(node_id, "completed")
            )
        
        # Activate current node
        await self.executor.execute(
            ToolExecutor.frontend_update_node_status(self.current_node_id, "active")
        )

        # Restore collected field data to frontend UI
        # Iterate all nodes that have collected data (completed + current)
        all_nodes_with_data = list(set(completed_nodes) | {self.current_node_id})
        for node_id in all_nodes_with_data:
            node_data = collected_data.get(node_id, {})
            if not node_data:
                continue
            # Filter out internal metadata fields
            display_data = {k: v for k, v in node_data.items() if not k.startswith("_")}
            if not display_data:
                continue
            # Convert field keys to labels for frontend display
            if self.graph:
                node = self.graph.get_node(node_id)
                if node:
                    label_fields = node.keys_to_labels(display_data)
                    await self.executor.execute(
                        ToolExecutor.frontend_update_node_fields(
                            node_id=node_id,
                            fields=label_fields
                        )
                    )

            # Restore photos if photo_urls exist
            photo_urls = node_data.get("photo_urls", [])
            if photo_urls:
                await self.executor.execute(
                    ToolExecutor.frontend_restore_node_photos(
                        node_id=node_id,
                        photo_urls=photo_urls
                    )
                )
        
        self.state = RunnerState.EXECUTING_NODE
        
        # Get guide for current node
        guide = self._get_node_guide(self.current_node_id)
        
        # Get node name for friendly message
        node_name = ""
        if self.graph and self.current_node_id:
            node = self.graph.get_node(self.current_node_id)
            node_name = node.name if node else self.current_node_id
        
        address = current_task.get("user_info", {}).get("address", "")
        
        # Check if current node has photo uploaded
        current_node_data = collected_data.get(self.current_node_id, {})
        photo_status = "✓ uploaded" if current_node_data.get("photo") == "uploaded" else "⚠️ pending"
        
        return ExecutionResult(
            state=RunnerState.EXECUTING_NODE,
            message=f"Work form resumed ({address}), currently at [{node_name}] node. Photo status: {photo_status}. {guide}",
            requires_user_input=True,
            next_actions=[]
        )
    
    async def execute_node_step(self, context: NodeExecutionContext) -> ExecutionResult:
        """
        Execute a step within the current node.
        
        This handles field collection but NOT node completion.
        """
        if not self.graph:
            return ExecutionResult(
                state=RunnerState.ERROR,
                message="Workflow not initialized",
                error="Call initialize() first"
            )
        
        node = self.graph.get_node(context.node_id)
        if not node:
            return ExecutionResult(
                state=RunnerState.ERROR,
                message=f"Node not found: {context.node_id}",
                error="Invalid node ID"
            )
        
        # Update backend with collected data
        if context.collected_data:
            await self.executor.execute(
                ToolExecutor.mcp_update_node_data(
                    task_id=context.task_id,
                    node_id=context.node_id,
                    data=context.collected_data
                )
            )

            # Update frontend — convert field keys to human-readable labels
            label_fields = node.keys_to_labels(context.collected_data)
            await self.executor.execute(
                ToolExecutor.frontend_update_node_fields(
                    node_id=context.node_id,
                    fields=label_fields
                )
            )
        
        self.state = RunnerState.WAITING_INPUT
        
        return ExecutionResult(
            state=RunnerState.WAITING_INPUT,
            message=f"Node {node.name}: collected {len(context.collected_data)} fields",
            requires_user_input=True
        )
    
    async def _restore_photos_to_frontend(self, collected_data: Dict) -> None:
        """Restore photos from collected_data to frontend."""
        for node_id, node_data in collected_data.items():
            photo_urls = node_data.get("photo_urls", [])
            if photo_urls:
                logger.info(f"[_restore_photos_to_frontend] Restoring {len(photo_urls)} photos for {node_id}")
                await self.executor.execute(
                    ToolExecutor.frontend_restore_node_photos(
                        node_id=node_id,
                        photo_urls=photo_urls
                    )
                )
    
    async def complete_node(self, node_id: str, task_id: str, 
                           fields: Optional[Dict] = None) -> ExecutionResult:
        """
        Complete current node and transition to next.
        
        Template 3 from SKILL.md: Node Completion Transition
        """
        self.state = RunnerState.TRANSITIONING
        
        # Step 1: Check completion
        check_result = await self.executor.execute(
            ToolExecutor.mcp_check_node_completion(task_id, node_id)
        )
        
        if not check_result.success or not check_result.data.get("complete"):
            missing = check_result.data.get("missing_fields", [])
            return ExecutionResult(
                state=RunnerState.EXECUTING_NODE,
                message=f"Node incomplete, missing: {', '.join(missing)}",
                requires_user_input=True
            )
        
        # Step 2: Transition (the ONLY way to move forward)
        transition_result = await self.executor.execute(
            ToolExecutor.mcp_transition_to_next_node(task_id, fields or {})
        )
        
        if not transition_result.success:
            # Handle route_condition_unresolved
            if transition_result.data.get("error") == "route_condition_unresolved":
                required = transition_result.data.get("required_branch_fields", [])
                return ExecutionResult(
                    state=RunnerState.EXECUTING_NODE,
                    message=f"Need routing fields: {', '.join(required)}",
                    requires_user_input=True
                )
            
            return ExecutionResult(
                state=RunnerState.ERROR,
                message="Transition failed",
                error=transition_result.error
            )
        
        # Step 3: Dify-style - Auto-execute frontend actions internally
        completed_node = transition_result.data.get("completed_node")
        next_node = transition_result.data.get("next_node")
        
        await self.executor.execute(
            ToolExecutor.frontend_update_node_status(completed_node, "completed")
        )
        await self.executor.execute(
            ToolExecutor.frontend_update_node_status(next_node, "active")
        )
        
        self.current_node_id = next_node
        self.state = RunnerState.EXECUTING_NODE
        
        # Get guide for next node
        guide = self._get_node_guide(next_node)
        
        return ExecutionResult(
            state=RunnerState.EXECUTING_NODE,
            message=f"Transitioned to: {next_node}. {guide}",
            requires_user_input=True,
            next_actions=[]
        )
    
    def _get_node_guide(self, node_id: str) -> str:
        """Get guide text for a specific node."""
        if not self.graph:
            return ""
        
        node = self.graph.get_node(node_id)
        if not node:
            return ""
        
        guide = node.guide_text or f"请完成 {node.name} 的安检工作"

        # Build field hints from field_definitions
        field_hints = []
        for fkey in node.required_fields:
            label = node.get_field_label(fkey)
            field_hints.append(f"- {label} (required)")
        for fkey in node.optional_fields:
            label = node.get_field_label(fkey)
            field_hints.append(f"- {label} (optional)")

        if field_hints:
            guide += "\nNeed to record:\n" + "\n".join(field_hints)

        return guide
    
    def get_current_node_guide(self) -> Optional[str]:
        """Get guide text for current node."""
        if not self.current_node_id:
            return None
        return self._get_node_guide(self.current_node_id)
    
    def get_execution_summary(self) -> Dict:
        """Get summary of execution history."""
        return {
            "state": self.state.value,
            "task_id": self.current_task_id,
            "current_node": self.current_node_id,
            "history": self.execution_history
        }
