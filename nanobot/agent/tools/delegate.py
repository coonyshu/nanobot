"""Delegate tool: allows the main agent to delegate tasks to sub-agents."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.agent_def import ModelOverride
from nanobot.agent.tools.base import Tool
from nanobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nanobot.agent.agent_registry import AgentRegistry
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import LLMProvider
    from pathlib import Path


class DelegateTool(Tool):
    """Tool that delegates a task to a registered sub-agent.

    Supports two execution modes:
    * **sync** — awaits the sub-agent and returns the result directly.
    * **background** — fires the sub-agent as an ``asyncio.Task`` and announces
      the result later via the message bus.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        parent_tools: ToolRegistry,
        *,
        default_model: str | None = None,
        default_temperature: float = 0.1,
        default_max_tokens: int = 4096,
        default_reasoning_effort: str | None = None,
        config_overrides: dict[str, ModelOverride] | None = None,
        sync_timeout: float = 300.0,
    ) -> None:
        self._registry = agent_registry
        self._provider = provider
        self._workspace = workspace
        self._bus = bus
        self._parent_tools = parent_tools

        self._default_model = default_model
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._default_reasoning_effort = default_reasoning_effort
        self._config_overrides = config_overrides or {}
        self._sync_timeout = sync_timeout

        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

        self._bg_tasks: dict[str, asyncio.Task] = {}

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        return (
            "Delegate a task to a specialized sub-agent. "
            "Use this when a task matches a registered agent's expertise. "
            "The sub-agent has its own tools and system prompt optimized for its domain."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the target sub-agent",
                },
                "task": {
                    "type": "string",
                    "description": "Task description for the sub-agent",
                },
                "context": {
                    "type": "object",
                    "description": "Optional extra context to pass to the sub-agent",
                },
                "mode": {
                    "type": "string",
                    "enum": ["sync", "background"],
                    "description": "Execution mode (default: agent's configured mode)",
                },
            },
            "required": ["agent", "task"],
        }

    async def execute(
        self,
        agent: str,
        task: str,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        **kwargs: Any,
    ) -> str:
        from nanobot.agent.agent import Agent
        
        agent_def = self._registry.get(agent)
        if agent_def is None:
            available = ", ".join(self._registry.list_names())
            return f"Error: Agent '{agent}' not found. Available agents: {available}"

        cfg = agent_def.get_config()
        effective_mode = mode or cfg.mode

        if effective_mode == "background":
            return await self._run_background(agent_def, task, context)
        else:
            return await self._run_sync(agent_def, task, context)

    async def _run_sync(
        self, agent_def: Any, task: str, context: dict[str, Any] | None
    ) -> str:
        from nanobot.agent.agent import Agent
        
        try:
            result = await asyncio.wait_for(
                Agent.run_one_shot(
                    agent_def=agent_def,
                    provider=self._provider,
                    workspace=self._workspace,
                    bus=self._bus,
                    parent_tools=self._parent_tools,
                    task=task,
                    channel=self._origin_channel,
                    chat_id=self._origin_chat_id,
                    extra_context=context,
                    default_model=self._default_model,
                    default_temperature=self._default_temperature,
                    default_max_tokens=self._default_max_tokens,
                    default_reasoning_effort=self._default_reasoning_effort,
                    config_override=self._config_overrides.get(agent_def.get_config().name),
                ),
                timeout=self._sync_timeout,
            )
            return result
        except asyncio.TimeoutError:
            return f"Error: Sub-agent timed out after {self._sync_timeout}s"
        except Exception as e:
            logger.error("Delegate sync error: {}", e)
            return f"Error: Sub-agent failed: {e}"

    async def _run_background(
        self, agent_def: Any, task: str, context: dict[str, Any] | None
    ) -> str:
        from nanobot.agent.agent import Agent
        
        task_id = str(uuid.uuid4())[:8]
        label = agent_def.get_config().name

        async def _bg_worker() -> None:
            try:
                result = await Agent.run_one_shot(
                    agent_def=agent_def,
                    provider=self._provider,
                    workspace=self._workspace,
                    bus=self._bus,
                    parent_tools=self._parent_tools,
                    task=task,
                    channel=self._origin_channel,
                    chat_id=self._origin_chat_id,
                    extra_context=context,
                    default_model=self._default_model,
                    default_temperature=self._default_temperature,
                    default_max_tokens=self._default_max_tokens,
                    default_reasoning_effort=self._default_reasoning_effort,
                    config_override=self._config_overrides.get(label),
                )
                await self._announce_result(task_id, label, task, result, "ok")
            except Exception as e:
                logger.error("Delegate bg error [{}]: {}", label, e)
                await self._announce_result(task_id, label, task, str(e), "error")
            finally:
                self._bg_tasks.pop(task_id, None)

        bg = asyncio.create_task(_bg_worker())
        self._bg_tasks[task_id] = bg
        return f"Sub-agent '{label}' started in background (id: {task_id}). I'll notify you when it completes."

    async def _announce_result(
        self, task_id: str, label: str, task: str, result: str, status: str
    ) -> None:
        status_text = "completed successfully" if status == "ok" else "failed"
        content = (
            f"[Sub-agent '{label}' {status_text}]\n\n"
            f"Task: {task}\n\nResult:\n{result}\n\n"
            "Summarize this naturally for the user. Keep it brief."
        )
        msg = InboundMessage(
            channel="system",
            sender_id="delegate",
            chat_id=f"{self._origin_channel}:{self._origin_chat_id}",
            content=content,
        )
        await self._bus.publish_inbound(msg)
