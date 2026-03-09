"""
Workflow Agent Tools - Registration for nanobots-ai.

Exposes workflow execution capabilities as nanobot tools.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry

from .runner import WorkflowRunner, RunnerState
from .executor import ToolExecutor, ToolCall, ToolType
from .graph import WorkflowGraph


# Global runner instance (per-tenant should be managed by caller)
_runners: Dict[str, WorkflowRunner] = {}

# Module-level registry reference — wired up by loop.py after tool registration
_tool_registry: Optional[ToolRegistry] = None


def _make_caller(registry: ToolRegistry):
    """Create an async caller that routes through the ToolRegistry (internal path).

    Uses internal=True so WorkflowRunner can call hidden tools (work_form_*,
    mcp_workflow-engine_*) that are not visible to the LLM.
    """
    async def caller(tool_name: str, params: dict):
        return await registry.execute(tool_name, params, internal=True)
    return caller


def set_tool_registry(registry: ToolRegistry) -> None:
    """
    Wire the ToolRegistry into the workflow runner so it can invoke
    MCP and frontend tools. Call this once after MCP tools are registered.
    """
    global _tool_registry
    _tool_registry = registry
    # Update callers on any already-created runners
    caller = _make_caller(registry)
    for runner in _runners.values():
        runner.executor.mcp_caller = caller
        runner.executor.frontend_caller = caller


def _resolve_tenant_id(tenant_id: str | None) -> str:
    """Resolve tenant_id: use provided value, fall back to ContextVar, then 'default'."""
    if tenant_id:
        return tenant_id
    try:
        from nanobot.multi_tenant.agent_pool import current_tenant_id as _ctx
        return _ctx.get()
    except Exception:
        return "default"


def get_runner(tenant_id: str | None = None, mcp_caller=None, frontend_caller=None) -> WorkflowRunner:
    """Get or create runner for tenant."""
    tenant_id = _resolve_tenant_id(tenant_id)
    if tenant_id not in _runners:
        # Prefer explicitly-passed callers, fall back to registry-based callers
        if _tool_registry and not mcp_caller:
            mcp_caller = _make_caller(_tool_registry)
        if not frontend_caller:
            frontend_caller = mcp_caller  # frontend tools are also in the registry
        executor = ToolExecutor(mcp_caller=mcp_caller, frontend_caller=frontend_caller)
        _runners[tenant_id] = WorkflowRunner(executor)
    else:
        # If registry was set after runner creation, wire it up now
        runner = _runners[tenant_id]
        if _tool_registry and not runner.executor.mcp_caller:
            caller = _make_caller(_tool_registry)
            runner.executor.mcp_caller = caller
            runner.executor.frontend_caller = caller
    return _runners[tenant_id]


class WorkflowInitializeTool(Tool):
    """Initialize workflow agent with configuration."""
    
    name = "workflow_initialize"
    description = "Initialize workflow agent by loading workflow definitions from backend"
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }
    
    async def execute(self, **kwargs) -> str:
        runner = get_runner()
        result = await runner.initialize()
        return json.dumps({
            "success": result.state.value != "error",
            "state": result.state.value,
            "message": result.message,
            "error": result.error
        })


class WorkflowStartTool(Tool):
    """Start or resume a workflow task (Dify-style: auto-executes all tools)."""
    
    name = "workflow_start_task"
    description = (
        "[MANDATORY] Start or resume a workflow task. "
        "MUST be called every time user says 'start inspection/task' regardless of whether a task is already in progress. "
        "This tool auto-opens the work form, restores all node states and collected data. "
        "DO NOT call workflow_get_status or reply with text only - always call this tool first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "User ID to start task for"},
            "address": {"type": "string", "description": "Address information"}
        },
        "required": ["user_id"]
    }
    
    async def execute(self, user_id: str, address: str = None, **kwargs) -> str:
        from loguru import logger

        tid = _resolve_tenant_id(None)
        logger.info(f"[WorkflowAgent] workflow_start_task called: user_id={user_id}, address={address}, tenant_id={tid}")

        runner = get_runner(tid)
        
        # Ensure initialized
        if not runner.graph:
            logger.info("[WorkflowAgent] Runner not initialized, calling initialize()")
            init_result = await runner.initialize()
            logger.info(f"[WorkflowAgent] Initialize result: state={init_result.state.value}, message={init_result.message}")
            if init_result.state.value == "error":
                return json.dumps({"success": False, "error": init_result.error})
        
        logger.info(f"[WorkflowAgent] Calling runner.start_task({user_id}, {address})")
        result = await runner.start_task(user_id, address)
        logger.info(f"[WorkflowAgent] start_task result: state={result.state.value}, message={result.message}, requires_user_input={result.requires_user_input}")
        logger.info(f"[WorkflowAgent] runner state: task_id={runner.current_task_id}, node_id={runner.current_node_id}")
        
        # Dify-style: return simple message for AI to speak
        # Explicitly tell AI that all frontend operations are done
        message = result.message
        if result.state.value != "error":
            message += "\n\n[Workflow Agent Active] Please continue using workflow_* tools for subsequent operations, do not call low-level tools directly."
        
        response = {
            "success": result.state.value != "error",
            "message": message,
            "requires_user_input": result.requires_user_input,
            "_workflow_mode": "active",  # Signal to main agent
            "task_id": runner.current_task_id,
            "current_node": runner.current_node_id
        }
        logger.info(f"[WorkflowAgent] === WORKFLOW AGENT TAKEOVER SUCCESS ===")
        logger.info(f"[WorkflowAgent] task_id: {runner.current_task_id}, node: {runner.current_node_id}")
        logger.info(f"[WorkflowAgent] Main AI should continue using workflow_* tools, do not call low-level tools")
        logger.info(f"[WorkflowAgent] Returning response: {response}")
        return json.dumps(response)


class WorkflowCollectFieldsTool(Tool):
    """Collect field data for current node."""
    
    name = "workflow_collect_fields"
    description = (
        "[Workflow Agent] Collect field values for current node. "
        "Call this when user provides any field data. "
        "⚠️ IMPORTANT: After collecting fields, DO NOT automatically call workflow_complete_node. "
        "Wait for the user to explicitly say 'done'/'next step'/'complete' before transitioning."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Current task ID"},
            "node_id": {"type": "string", "description": "Current node ID"},
            "fields": {"type": "object", "description": "Field values to collect (field_key: value)"}
        },
        "required": ["task_id", "node_id", "fields"]
    }
    
    async def execute(self, task_id: str, node_id: str, fields: Dict, **kwargs) -> str:
        from loguru import logger
        logger.info(f"[WorkflowAgent] workflow_collect_fields called: task_id={task_id}, node_id={node_id}, fields={fields}")

        runner = get_runner()
        
        from .runner import NodeExecutionContext
        context = NodeExecutionContext(
            node_id=node_id,
            task_id=task_id,
            user_id=runner.user_id or "",
            collected_data=fields
        )
        
        result = await runner.execute_node_step(context)
        logger.info(f"[WorkflowAgent] collect_fields result: {result.state.value}, message={result.message}")
        
        return json.dumps({
            "success": result.state.value != "error",
            "state": result.state.value,
            "message": result.message,
            "requires_user_input": result.requires_user_input,
            "error": result.error
        })


class WorkflowCompleteNodeTool(Tool):
    """Complete current node and transition to next (Dify-style)."""
    
    name = "workflow_complete_node"
    description = (
        "[Workflow Agent] Complete current node and transition to next. "
        "⚠️ ONLY call this when user EXPLICITLY says 'done'/'next step'/'complete'/'continue'. "
        "DO NOT call automatically even if all fields are filled. "
        "User must actively trigger the transition."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Current task ID"},
            "node_id": {"type": "string", "description": "Current node ID to complete"},
            "fields": {"type": "object", "description": "All current field values for routing"}
        },
        "required": ["task_id", "node_id"]
    }
    
    async def execute(self, task_id: str, node_id: str, fields: Dict = None, **kwargs) -> str:
        from loguru import logger
        logger.info(f"[WorkflowAgent] workflow_complete_node called: task_id={task_id}, node_id={node_id}, fields={fields}")

        # Load latest collected_data from backend to ensure routing uses correct values
        runner = get_runner()
        load_result = await runner.executor.execute(
            ToolExecutor.mcp_load_workflow_task(runner.user_id, None)
        )
        if load_result.success and load_result.data.get("current_task"):
            current_task = load_result.data["current_task"]
            collected_data = current_task.get("collected_data", {})
            node_data = collected_data.get(node_id, {})
            # Merge backend data with provided fields (backend data takes precedence for routing fields)
            merged_fields = {**(fields or {}), **node_data}
            logger.info(f"[WorkflowAgent] Merged fields from backend: {merged_fields}")
            fields = merged_fields

        result = await runner.complete_node(node_id, task_id, fields)
        
        logger.info(f"[WorkflowAgent] complete_node result: {result.state.value}, next_node={runner.current_node_id}")
        
        # Dify-style: return simple message for AI to speak
        return json.dumps({
            "success": result.state.value != "error",
            "message": result.message,
            "requires_user_input": result.requires_user_input,
            "_workflow_mode": "active",
            "task_id": task_id,
            "current_node": runner.current_node_id
        })


class WorkflowJumpToNodeTool(Tool):
    """Jump to any specified node (for manual workflow intervention)."""
    
    name = "workflow_jump_to_node"
    description = (
        "[Manual Intervention] Jump to any specified node. Used when user explicitly requests to skip certain nodes or jump to specific workflow step."
        "⚠️ Note: Jump will mark current node as completed, please confirm user intent before calling."
        "⚠️ Required: After calling this tool, must immediately execute frontend tools specified in _required_frontend_actions in the return result!"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Current task ID"},
            "target_node_id": {"type": "string", "description": "Target node ID to jump to"},
            "reason": {"type": "string", "description": "Reason for jump (optional)"}
        },
        "required": ["task_id", "target_node_id"]
    }
    
    async def execute(self, task_id: str, target_node_id: str, reason: str = None, **kwargs) -> str:
        from loguru import logger
        logger.info(f"[WorkflowAgent] workflow_jump_to_node called: task_id={task_id}, target={target_node_id}, reason={reason}")

        runner = get_runner()
        
        # Step 1: Call backend to jump to target node
        result = await runner.executor.execute(
            ToolExecutor.mcp_jump_to_node(task_id, target_node_id, reason)
        )
        
        logger.info(f"[WorkflowAgent] jump_to_node result: {result}")
        logger.info("[WorkflowAgent] DEBUG: After jump_to_node result log")
        
        # Step 2: Update runner state
        if result.success:
            runner.current_node_id = result.data.get("current_node")
            runner.state = RunnerState.EXECUTING_NODE
        
        logger.info(f"[WorkflowAgent] Step 2 done, current_node_id={runner.current_node_id}")
        logger.info("[WorkflowAgent] DEBUG: After Step 2 done log")
        
        # Step 3: Auto-execute frontend actions (Dify-style)
        logger.info(f"[WorkflowAgent] Step 3 check: success={result.success}, data type={type(result.data)}, data={result.data}")
        has_actions = result.data and "_required_frontend_actions" in result.data if result.data else False
        logger.info(f"[WorkflowAgent] has _required_frontend_actions: {has_actions}")
        if result.success and result.data and result.data.get("_required_frontend_actions"):
            frontend_actions = result.data["_required_frontend_actions"]
            logger.info(f"[WorkflowAgent] Auto-executing {len(frontend_actions)} frontend actions: {frontend_actions}")
            
            for action_desc in frontend_actions:
                # Parse action description to extract tool name and params
                # Format: [Required] work_form_update_node_status(node_id="xxx", status="yyy") - description
                import re
                match = re.search(r'(work_form_\w+)\(([^)]+)\)', action_desc)
                if match:
                    tool_name = match.group(1)
                    params_str = match.group(2)
                    
                    # Parse params
                    params = {}
                    for param_match in re.finditer(r'(\w+)=["\']([^"\']+)["\']', params_str):
                        params[param_match.group(1)] = param_match.group(2)
                    
                    logger.info(f"[WorkflowAgent] Auto-executing frontend tool: {tool_name} with params: {params}")
                    
                    # Execute frontend tool
                    try:
                        if tool_name == "work_form_update_node_status":
                            node_id = params.get("node_id", "")
                            status = params.get("status", "")
                            # For jump scenario: skip validation when marking previous node as completed
                            skip_validation = status == "completed"
                            logger.info(f"[WorkflowAgent] Calling frontend_update_node_status with node_id={node_id}, status={status}, skip_validation={skip_validation}")
                            tool_call = ToolCall(
                                tool_type=ToolType.FRONTEND,
                                tool_name="work_form_update_node_status",
                                params={"node_id": node_id, "status": status, "skip_validation": skip_validation},
                                description="Update node status in UI"
                            )
                            logger.info(f"[WorkflowAgent] ToolCall created: {tool_call}")
                            exec_result = await runner.executor.execute(tool_call)
                            logger.info(f"[WorkflowAgent] Frontend tool execution result: {exec_result}")
                        # Add other frontend tools as needed
                    except Exception as e:
                        logger.error(f"[WorkflowAgent] Failed to execute frontend tool {tool_name}: {e}")
                        import traceback
                        logger.error(f"[WorkflowAgent] Exception traceback: {traceback.format_exc()}")
        
        # Step 4: Return result
        return json.dumps({
            "success": result.success,
            "message": result.data.get("message") if result.success else result.error,
            "current_node": runner.current_node_id if result.success else None,
            "_workflow_mode": "active"
        }, ensure_ascii=False)


class WorkflowGetStatusTool(Tool):
    """Get current workflow execution status (only for user query, NOT for starting task)."""
    
    name = "workflow_get_status"
    description = (
        "[Only for user-initiated progress queries] Query current workflow execution status."
        "⚠️ Prohibited: Cannot use this tool to start or resume workflow task!"
        "⚠️ Required: When user wants to start/resume task, must call workflow_start_task, not this tool!"
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }
    
    async def execute(self, **kwargs) -> str:
        from loguru import logger
        runner = get_runner()
        summary = runner.get_execution_summary()
        guide = runner.get_current_node_guide()

        # If runner has no active task, warn AI to use workflow_start_task instead
        if not summary.get("task_id"):
            logger.warning("[WorkflowAgent] workflow_get_status called but no active task in runner. AI should call workflow_start_task instead.")
            return json.dumps({
                "success": False,
                "state": "idle",
                "task_id": None,
                "current_node": None,
                "guide_text": None,
                "warning": (
                    "⚠️ No active workflow task currently running."
                    "If user wants to start/resume task, please call workflow_start_task immediately, do not continue calling this tool!"
                )
            })
        
        return json.dumps({
            "success": True,
            "state": summary["state"],
            "task_id": summary["task_id"],
            "current_node": summary["current_node"],
            "guide_text": guide,
            "history_count": len(summary["history"]),
            "notice": (
                "⚠️ Note: This tool only returns status in memory, will not open or restore frontend work form."
                "If user wants to start/resume task, must call workflow_start_task!"
            )
        })


def register_tools(registry: ToolRegistry) -> None:
    """Register workflow agent tools with nanobots-ai."""
    registry.register(WorkflowInitializeTool())
    registry.register(WorkflowStartTool())
    registry.register(WorkflowCollectFieldsTool())
    registry.register(WorkflowCompleteNodeTool())
    registry.register(WorkflowJumpToNodeTool())
    registry.register(WorkflowGetStatusTool())
