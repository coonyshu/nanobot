"""Unified Agent implementation supporting multi-level nested calls."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import platform as _platform
import time as _time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.agent_def import AgentDefinition, ModelOverride
from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from nanobot.agent.tools.a2a import A2ATool
    from nanobot.agent.tools.enter_agent import EnterAgentTool
    from nanobot.agent.tools.exit_agent import ExitAgentTool
    from nanobot.agent.tools.mcp import connect_mcp_servers
    from nanobot.agent.tools.message import MessageTool
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import LLMProvider
    from nanobot.session.agent_session import AgentSession, AgentSessionManager


def _first_not_none(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


@dataclass
class AgentConfig:
    """Configuration for an Agent instance."""
    agent_name: str
    workspace: Path
    user_workspace: Path | None = None
    bus: MessageBus | None = None
    parent: Agent | None = None
    session_key: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    default_model: str | None = None
    default_temperature: float = 0.1
    default_max_tokens: int = 4096
    default_reasoning_effort: str | None = None
    config_overrides: dict[str, ModelOverride] | None = None
    idle_timeout: int = 300
    max_depth: int = 10


class Agent:
    """Unified Agent implementation supporting multi-level nested calls.
    
    This class provides a single, unified Agent implementation that can:
    1. Act as a top-level agent (MainAgent) receiving messages from MessageBus
    2. Act as a sub-agent (SubAgent) processing messages directly
    3. Support multi-level nesting (Agent calling Agent)
    
    Each Agent has a unique name and can be invoked via @AgentName syntax.
    """
    
    def __init__(
        self,
        agent_def: AgentDefinition,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        session_key: str,
        channel: str,
        chat_id: str,
        *,
        parent: Agent | None = None,
        parent_tools: ToolRegistry | None = None,
        default_model: str | None = None,
        default_temperature: float = 0.1,
        default_max_tokens: int = 4096,
        default_reasoning_effort: str | None = None,
        config_overrides: dict[str, ModelOverride] | None = None,
        idle_timeout: int = 300,
        user_workspace: Path | None = None,
        session_manager: AgentSessionManager | None = None,
        agent_registry: Any = None,
    ) -> None:
        self.agent_def = agent_def
        self.provider = provider
        self.workspace = workspace
        self.agent_registry = agent_registry
        self.bus = bus
        self.parent = parent
        self.parent_tools = parent_tools or (parent._tools if parent else None)
        self.session_key = session_key
        self.channel = channel
        self.chat_id = chat_id
        self.idle_timeout = idle_timeout
        self._config_overrides = config_overrides
        
        cfg = agent_def.get_config()
        self.agent_name = cfg.name
        self.max_iterations = cfg.max_iterations
        self.depth = (parent.depth + 1) if parent else 0
        
        if self.depth > 10:
            raise ValueError(f"Maximum agent nesting depth (10) exceeded: current depth={self.depth}")
        
        self._mcp_servers = cfg.mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        
        agent_mc = cfg.model_config or ModelOverride()
        co = config_overrides.get(self.agent_name) if config_overrides else None
        co = co or ModelOverride()
        self.model = co.model or agent_mc.model or default_model or provider.get_default_model()
        self.temperature = _first_not_none(co.temperature, agent_mc.temperature, default_temperature)
        self.max_tokens = _first_not_none(co.max_tokens, agent_mc.max_tokens, default_max_tokens)
        self.reasoning_effort = co.reasoning_effort or agent_mc.reasoning_effort or default_reasoning_effort
        
        if user_workspace:
            self.agent_workspace = user_workspace / "agents" / self.agent_name
            logger.info("[Agent:{}] using user_workspace: {}", self.agent_name, self.agent_workspace)
        else:
            self.agent_workspace = workspace / "agents" / self.agent_name
            logger.info("[Agent:{}] using global workspace: {}", self.agent_name, self.agent_workspace)
        self.agent_workspace.mkdir(parents=True, exist_ok=True)
        self._memory = MemoryStore(self.agent_workspace)
        
        self._tools: ToolRegistry = ToolRegistry()
        self._loop: Any = None
        self._session: AgentSession | None = None
        
        if session_manager:
            self._session_manager = session_manager
        else:
            from nanobot.session.agent_session import AgentSessionManager
            self._session_manager = AgentSessionManager(
                self.agent_workspace,
                idle_timeout=idle_timeout,
            )
        
        self.status: str = "inactive"
        self.exit_summary: str | None = None
        self.last_activity: float = _time.time()
        self.last_consolidated: int = 0
        self.is_busy: bool = False
        
        self._child_agents: dict[str, Agent] = {}
        
    @property
    def is_root(self) -> bool:
        """Check if this is a root agent (no parent)."""
        return self.parent is None
    
    @property
    def is_subagent(self) -> bool:
        """Check if this is a sub-agent."""
        return self.parent is not None
    
    @property
    def subagent_workspace(self) -> Path:
        """Alias for agent_workspace (backward compatibility)."""
        return self.agent_workspace
    
    @property
    def memory(self) -> MemoryStore:
        """Get memory store."""
        return self._memory
    
    @property
    def tools(self) -> ToolRegistry:
        """Get tool registry."""
        return self._tools
    
    @property
    def messages(self) -> list[dict[str, Any]]:
        """Get session messages."""
        return self._session.messages if self._session else []
    
    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        """Set session messages."""
        if self._session:
            self._session.messages = value
    
    def activate(self, context_messages: list[dict[str, Any]] | None = None) -> None:
        """Activate the agent with context messages."""
        from nanobot.agent.agent_loop import AgentLoop
        
        self._tools = self._build_tools()
        system_prompt = self._build_system_prompt(context_messages or [])
        
        self._session = self._session_manager.get_or_create(
            self.session_key, self.agent_name
        )
        self._session.messages = [{"role": "system", "content": system_prompt}]
        self._session.status = "active"
        self._session_manager.set_active(self._session)
        
        def get_active_agent() -> str | None:
            """Get the active agent name from user session metadata."""
            user_workspace = self.agent_workspace.parent.parent if self.agent_workspace.parent.name == "agents" else None
            if user_workspace:
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(user_workspace)
                session_key = f"{self.channel}:{self.chat_id}"
                session = sessions.get_or_create(session_key)
                return session.metadata.get("active_agent")
            return None
        
        self._loop = AgentLoop(
            provider=self.provider,
            tools=self._tools,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_iterations=self.max_iterations,
            reasoning_effort=self.reasoning_effort,
            agent_name=self.agent_name,
            get_active_agent=get_active_agent,
        )
        
        self.status = "active"
        self.last_activity = _time.time()
        
        logger.info("[Agent:{}] activated (depth={}, parent={})",
                     self.agent_name, self.depth,
                     self.parent.agent_name if self.parent else "None")
    
    async def process_message(
        self,
        content: str,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message through this agent.
        
        Args:
            content: User message content
            on_progress: Optional progress callback
            on_stream: Optional streaming callback
            
        Returns:
            Agent's response text
        """
        if self._loop is None or self._session is None:
            raise RuntimeError("Agent not activated. Call activate() first.")
        
        self.last_activity = _time.time()
        self._session.touch()
        self._session.messages.append({"role": "user", "content": content})
        
        final_content, _, _ = await self._loop.run(
            messages=self._session.messages,
            on_progress=on_progress,
            on_stream=on_stream,
            check_exit=lambda: self._session.status == "exiting" if self._session else False,
            get_exit_summary=lambda: self._session.exit_summary if self._session else None,
        )
        
        if final_content is None:
            final_content = "Processing completed."
        
        unconsolidated = len(self._session.messages) - self.last_consolidated
        if unconsolidated >= 20:
            try:
                await self.consolidate_memory(archive_all=False, memory_window=20)
            except Exception:
                logger.exception("[Agent:{}] periodic memory consolidation failed",
                                 self.agent_name)
        
        return final_content
    
    async def enter_child_agent(self, agent_name: str, task: str) -> str:
        """Enter a child agent.
        
        Args:
            agent_name: Name of the child agent to enter
            task: Initial task for the child agent
            
        Returns:
            Child agent's response
        """
        # Set active_agent in parent session metadata
        from nanobot.session.manager import SessionManager
        user_workspace = self.agent_workspace.parent.parent if self.agent_workspace.parent.name == "agents" else None
        if user_workspace:
            sessions = SessionManager(user_workspace)
            session_key = f"{self.channel}:{self.chat_id}"
            session = sessions.get_or_create(session_key)
            session.metadata["active_agent"] = agent_name
            sessions.save(session)
            logger.info("[Agent:{}] set active_agent='{}' in session {}", 
                        self.agent_name, agent_name, session_key)
        
        if self.agent_registry:
            child_def = self.agent_registry.get(agent_name)
        else:
            from nanobot.agent.agent_registry import AgentRegistry
            registry = AgentRegistry()
            child_def = registry.get(agent_name)
        
        if child_def is None:
            return f"Error: Agent '{agent_name}' not found."
        
        if agent_name in self._child_agents:
            child = self._child_agents[agent_name]
        else:
            child = Agent(
                agent_def=child_def,
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                parent=self,
                parent_tools=self._tools,
                session_key=self.session_key,
                channel=self.channel,
                chat_id=self.chat_id,
                user_workspace=user_workspace,
                default_model=self.model,
                default_temperature=self.temperature,
                default_max_tokens=self.max_tokens,
                default_reasoning_effort=self.reasoning_effort,
                config_overrides=self._config_overrides,
                idle_timeout=self.idle_timeout,
                agent_registry=self.agent_registry,
            )
            child.activate(self._session.messages[-20:] if self._session else [])
            await child.connect_mcp()
            self._child_agents[agent_name] = child
        
        logger.info("[Agent:{}] entering child agent '{}'",
                     self.agent_name, agent_name)
        
        try:
            response = await child.process_message(task)
            return response
        finally:
            if child._session and child._session.status == "closed":
                await child.disconnect_mcp()
                del self._child_agents[agent_name]
                logger.info("[Agent:{}] child agent '{}' exited",
                             self.agent_name, agent_name)
    
    async def consolidate_memory(self, archive_all: bool = False, memory_window: int = 20) -> bool:
        """Consolidate conversation history into MEMORY.md + HISTORY.md."""
        if not self._session:
            return False
        
        if archive_all:
            old_messages = self._session.messages
            keep_count = 0
        else:
            keep_count = memory_window // 2
            if len(self._session.messages) <= keep_count:
                return True
            if len(self._session.messages) - self.last_consolidated <= 0:
                return True
            old_messages = self._session.messages[self.last_consolidated:-keep_count]
            if not old_messages:
                return True
        
        lines = []
        for m in old_messages:
            content = m.get("content", "")
            if not content:
                continue
            role = m.get("role", "")
            if role == "system":
                continue
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            label = {"user": "User", "assistant": "Assistant", "tool": "Tool"}.get(role, role.title())
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(f"[{ts}] {label}: {content}")
        
        if not lines:
            return True
        
        current_memory = self._memory.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""
        
        try:
            from nanobot.agent.memory import _SAVE_MEMORY_TOOL
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent."},
                    {"role": "user", "content": prompt},
                ],
                tools=_SAVE_MEMORY_TOOL,
                model=self.model,
            )
            
            if not response.has_tool_calls:
                return False
            
            args = response.tool_calls[0].arguments
            if isinstance(args, str):
                args = json.loads(args)
            if isinstance(args, list) and args and isinstance(args[0], dict):
                args = args[0]
            if not isinstance(args, dict):
                return False
            
            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self._memory.append_history(entry)
            if update := args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    self._memory.write_long_term(update)
            
            self.last_consolidated = 0 if archive_all else len(self._session.messages) - keep_count
            return True
        except Exception:
            logger.exception("[Agent:{}] memory consolidation failed", self.agent_name)
            return False
    
    def exit(self, summary: str | None = None) -> str:
        """Exit this agent session."""
        if self._session:
            self._session.status = "closed"
            self._session.exit_summary = summary
        self.status = "closed"
        return summary or self.exit_summary or "Session ended."
    
    def is_expired(self) -> bool:
        """Check if idle timeout has elapsed."""
        if self._session:
            return self._session.is_expired(self.idle_timeout)
        return True

    @staticmethod
    async def run_one_shot(
        agent_def: "AgentDefinition",
        provider: "LLMProvider",
        workspace: Path,
        bus: "MessageBus",
        parent_tools: "ToolRegistry",
        task: str,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        extra_context: dict[str, Any] | None = None,
        default_model: str | None = None,
        default_temperature: float = 0.7,
        default_max_tokens: int = 4096,
        default_reasoning_effort: str | None = None,
        config_override: "ModelOverride | None" = None,
    ) -> str:
        """Execute a one-shot task without session/memory management.
        
        This is a lightweight alternative to creating a full Agent instance.
        Used by DelegateTool and SubagentManager for background tasks.
        
        Args:
            agent_def: Agent definition
            provider: LLM provider
            workspace: Workspace path
            bus: Message bus
            parent_tools: Parent's tool registry
            task: Task description
            channel: Origin channel
            chat_id: Origin chat ID
            extra_context: Additional context
            default_model: Default model
            default_temperature: Default temperature
            default_max_tokens: Default max tokens
            default_reasoning_effort: Default reasoning effort
            config_override: Model config override
            
        Returns:
            Final result string
        """
        from nanobot.agent.agent_loop import AgentLoop
        from nanobot.agent.tools.message import MessageTool
        
        cfg = agent_def.get_config()
        agent_name = cfg.name
        
        agent_mc = cfg.model_config or ModelOverride()
        co = config_override or ModelOverride()
        model = co.model or agent_mc.model or default_model or provider.get_default_model()
        temperature = _first_not_none(co.temperature, agent_mc.temperature, default_temperature)
        max_tokens = _first_not_none(co.max_tokens, agent_mc.max_tokens, default_max_tokens)
        reasoning_effort = co.reasoning_effort or agent_mc.reasoning_effort or default_reasoning_effort
        
        tools = ToolRegistry()
        for tool_name in cfg.tools:
            tool = parent_tools.get(tool_name)
            if tool is not None:
                tools.register(tool)
        
        if cfg.tools_include_pattern:
            for name in parent_tools.tool_names:
                if fnmatch.fnmatch(name, cfg.tools_include_pattern):
                    tool = parent_tools.get(name)
                    if tool is not None and not tools.has(name):
                        tools.register(tool)
        
        agent_def.register_tools(tools, parent_tools=parent_tools)
        
        if not tools.has("message"):
            tools.register(MessageTool(
                send_callback=bus.publish_outbound,
                default_channel=channel,
                default_chat_id=chat_id,
            ))
        
        custom_prompt = agent_def.build_system_prompt(workspace)
        tz = _time.strftime("%Z") or "UTC"
        os_name = _platform.system()
        
        prompt_parts = [
            f"# Sub-Agent: {agent_name}",
            f"\n{cfg.description}" if cfg.description else "",
            f"\n## Environment\n- OS: {os_name}\n- Timezone: {tz}\n- Workspace: {workspace}",
        ]
        if custom_prompt:
            prompt_parts.append(f"\n## Instructions\n\n{custom_prompt}")
        prompt_parts.append(
            "\n## Behavior\n"
            "You are a sub-agent spawned by the main agent to complete a specific task.\n"
            "Stay focused on the assigned task. Your final text response will be returned "
            "to the main agent as the result."
        )
        system_prompt = "\n".join(prompt_parts)
        
        user_msg = task
        if extra_context:
            ctx_str = json.dumps(extra_context, ensure_ascii=False, indent=2)
            user_msg = f"{task}\n\nAdditional context:\n```json\n{ctx_str}\n```"
        
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        
        loop = AgentLoop(
            provider=provider,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_iterations=cfg.max_iterations,
            reasoning_effort=reasoning_effort,
            agent_name=agent_name,
        )
        
        final_result, _, _ = await loop.run(messages)
        
        if final_result is None:
            final_result = "Task completed but no final response was generated."
        
        return agent_def.on_complete(final_result)
    
    async def connect_mcp(self) -> None:
        """Connect to agent-scoped MCP servers."""
        if not self._mcp_servers or self._mcp_connected:
            return
        
        from nanobot.agent.tools.mcp import connect_mcp_servers
        
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await asyncio.wait_for(
                connect_mcp_servers(self._mcp_servers, self._tools, self._mcp_stack),
                timeout=10.0
            )
            self._mcp_connected = True
            logger.info("[Agent:{}] MCP connected: {} server(s)",
                        self.agent_name, len(self._mcp_servers))
        except asyncio.TimeoutError:
            logger.error("[Agent:{}] MCP connection timeout", self.agent_name)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        except Exception as e:
            logger.error("[Agent:{}] MCP connection failed: {}", self.agent_name, e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
    
    async def disconnect_mcp(self) -> None:
        """Disconnect from agent-scoped MCP servers."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None
            self._mcp_connected = False
            logger.debug("[Agent:{}] MCP disconnected", self.agent_name)
    
    def save_session(self) -> None:
        """Save the current session to disk."""
        if self._session:
            self._session_manager.save(self._session)
    
    def _build_tools(self) -> ToolRegistry:
        """Build an isolated ToolRegistry for this agent."""
        from nanobot.agent.tools.a2a import A2ATool
        from nanobot.agent.tools.enter_agent import EnterAgentTool
        from nanobot.agent.tools.exit_agent import ExitAgentTool
        from nanobot.agent.tools.message import MessageTool
        
        registry = ToolRegistry()
        cfg = self.agent_def.get_config()
        
        for tool_name in cfg.tools:
            tool = None
            if self.parent_tools:
                tool = self.parent_tools.get(tool_name)
            if tool is not None:
                registry.register(tool)
        
        if cfg.tools_include_pattern and self.parent_tools:
            for name in self.parent_tools.tool_names:
                if fnmatch.fnmatch(name, cfg.tools_include_pattern):
                    tool = self.parent_tools.get(name)
                    if tool is not None and not registry.has(name):
                        registry.register(tool)
        
        self.agent_def.register_tools(registry, parent_tools=self.parent_tools)
        
        registry.register(ExitAgentTool(session=self))
        
        if not registry.has("message"):
            registry.register(MessageTool(
                send_callback=self.bus.publish_outbound,
                default_channel=self.channel,
                default_chat_id=self.chat_id,
                agent_name=self.agent_name,
            ))
        
        if not registry.has("enter_agent"):
            enter_agent_tool = EnterAgentTool(
                agent=self,
                parent_agent=self,
            )
            # Set context from Agent instance
            enter_agent_tool.set_context(self.channel, self.chat_id, self.session_key)
            registry.register(enter_agent_tool)
        
        if not registry.has("send_to_agent"):
            a2a_tool = A2ATool(send_callback=self.bus.publish_a2a)
            a2a_tool.agent_name = self.agent_name
            registry.register(a2a_tool)
        
        if not self._mcp_servers:
            registry.hide_pattern_from_llm("mcp_workflow-engine_")
        
        return registry
    
    def _build_system_prompt(self, context_messages: list[dict[str, Any]]) -> str:
        """Build system prompt with background context."""
        cfg = self.agent_def.get_config()
        custom_prompt = self.agent_def.build_system_prompt(self.workspace)
        
        tz = _time.strftime("%Z") or "UTC"
        os_name = _platform.system()
        
        parts = [
            f"# Agent: {cfg.name}",
            f"\n{cfg.description}" if cfg.description else "",
            f"\n## Environment\n- OS: {os_name}\n- Timezone: {tz}\n- Workspace: {self.workspace}",
        ]
        
        if custom_prompt:
            parts.append(f"\n## Instructions\n\n{custom_prompt}")
        
        behavior_parts = [
            "\n## Behavior",
        ]
        if self.is_root:
            behavior_parts.append(
                "You are a root agent managing a conversation.\n"
                "Process user messages and coordinate with sub-agents when needed.\n"
                "Use @AgentName to directly invoke a specific agent."
            )
            
            if self.agent_registry:
                persistent_summary = self.agent_registry.build_agents_summary(
                    filter_fn=lambda ad: ad.get_config().mode == "persistent"
                )
                if persistent_summary:
                    behavior_parts.append(
                        "\n## Persistent Agents\n\n"
                        "Use the `enter_agent` tool to enter a persistent agent session when the user's request "
                        "matches one of the agent's trigger keywords. The agent takes over the conversation until it exits.\n\n"
                        "### ⚠️ CRITICAL RULES - MUST READ\n\n"
                        "1. **When a user's message matches ANY trigger keyword in `<triggers>`, you MUST call `enter_agent` immediately.**\n"
                        "2. **DO NOT respond directly** - even if you think you know the answer or have context from memory.\n"
                        "3. **DO NOT use `spawn`, `delegate`, or handle the request yourself** - these tools cannot access the specialized workflow tools.\n"
                        "4. **DO NOT write scripts or use file operations** - the persistent agent has the correct tools already.\n"
                        "5. **IGNORE any memory about previous sessions** - always call `enter_agent` when triggers match.\n\n"
                        "**Example**: If user says \"开始安检\" and a persistent agent has trigger \"安检\", call:\n"
                        "```\n"
                        "enter_agent(agent_name=\"workflow-inspector\", task=\"开始安检\")\n"
                        "```\n\n"
                        "**IMPORTANT**: Even if you remember a previous安检 session, you MUST still call `enter_agent`. "
                        "The persistent agent will handle continuation or restart automatically.\n\n"
                        + persistent_summary
                    )
        else:
            behavior_parts.append(
                f"You are a sub-agent at depth {self.depth}.\n"
                f"Parent agent: {self.parent.agent_name if self.parent else 'None'}\n"
                "Stay focused on the assigned task.\n"
                "Call exit_agent when the task is completed."
            )
        parts.append("\n".join(behavior_parts))
        
        memory_context = self._memory.get_memory_context()
        if memory_context:
            parts.append(f"\n## Memory\n\n{memory_context}")
        
        if context_messages:
            context_text = self._format_context(context_messages)
            if context_text:
                parts.append(
                    f"\n## Background Context\n"
                    f"Recent conversation:\n\n{context_text}"
                )
        
        return "\n".join(parts)
    
    @staticmethod
    def _format_context(messages: list[dict[str, Any]]) -> str:
        """Format history messages into readable text."""
        lines = []
        for m in messages[-20:]:
            role = m.get("role", "")
            content = m.get("content", "")
            if role in ("system",):
                continue
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if c.get("type") == "text"
                )
            if not content or not content.strip():
                continue
            label = {"user": "User", "assistant": "Assistant"}.get(role, role.title())
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(f"**{label}**: {content}")
        return "\n".join(lines)


class AgentPool:
    """Pool of Agent instances for efficient resource utilization."""
    
    def __init__(
        self,
        max_instances: int = 10,
        idle_timeout: int = 300,
        instance_timeout: int = 1800,
    ):
        self._max_instances = max_instances
        self._idle_timeout = idle_timeout
        self._instance_timeout = instance_timeout
        
        self._instances: dict[str, list[Agent]] = {}
        self._session_mappings: dict[str, tuple[str, Agent]] = {}
        
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
    
    async def start(self):
        """Start the pool cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("AgentPool started with max_instances={}, idle_timeout={}s",
                    self._max_instances, self._idle_timeout)
    
    async def stop(self):
        """Stop the pool and cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        
        for agent_name, agent_instances in list(self._instances.items()):
            for agent in list(agent_instances):
                try:
                    await agent.disconnect_mcp()
                except Exception:
                    pass
        
        self._instances.clear()
        self._session_mappings.clear()
        logger.info("AgentPool stopped")
    
    async def get_or_create(
        self,
        agent_name: str,
        session_key: str,
        agent_def: AgentDefinition,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        parent: Agent | None = None,
        parent_tools: ToolRegistry | None = None,
        **kwargs
    ) -> tuple[Agent, bool]:
        """Get or create an Agent instance.
        
        Returns:
            tuple: (Agent instance, is_new_instance)
        """
        async with self._lock:
            cache_key = f"{agent_name}:{session_key}"
            
            if cache_key in self._session_mappings:
                _, agent = self._session_mappings[cache_key]
                if not agent.is_expired():
                    agent.last_activity = _time.time()
                    return agent, False
                else:
                    del self._session_mappings[cache_key]
            
            instances = self._instances.setdefault(agent_name, [])
            free_instances = [a for a in instances if not a.is_busy and a.is_expired()]
            if free_instances:
                instance = free_instances[0]
                instance.is_busy = True
                instance.last_activity = _time.time()
                self._session_mappings[cache_key] = (agent_name, instance)
                return instance, False
            
            total = sum(len(v) for v in self._instances.values())
            if total >= self._max_instances:
                logger.warning("AgentPool at capacity, waiting...")
                await asyncio.sleep(1)
                return await self.get_or_create(
                    agent_name, session_key, agent_def, provider, workspace, bus, parent, parent_tools, **kwargs
                )
            
            agent = Agent(
                agent_def=agent_def,
                provider=provider,
                workspace=workspace,
                bus=bus,
                parent=parent,
                parent_tools=parent_tools,
                session_key=session_key,
                **kwargs
            )
            agent.is_busy = True
            agent.last_activity = _time.time()
            
            if agent_name not in self._instances:
                self._instances[agent_name] = []
            self._instances[agent_name].append(agent)
            self._session_mappings[cache_key] = (agent_name, agent)
            
            return agent, True
    
    async def release(self, session_key: str):
        """Release an Agent instance back to pool."""
        async with self._lock:
            for key in list(self._session_mappings.keys()):
                if key.endswith(f":{session_key}"):
                    _, agent = self._session_mappings.pop(key)
                    agent.is_busy = False
                    logger.debug("Released Agent for session {}", session_key)
                    return
    
    async def _cleanup_loop(self):
        """Background task to clean up expired instances."""
        while True:
            await asyncio.sleep(60)
            await self._cleanup_expired()
    
    async def _cleanup_expired(self):
        """Clean up expired instances."""
        async with self._lock:
            expired = []
            for agent_name, instances in list(self._instances.items()):
                for agent in list(instances):
                    if agent.is_expired() and not agent.is_busy:
                        expired.append((agent_name, agent))
            
            for agent_name, agent in expired:
                self._instances[agent_name].remove(agent)
                if not self._instances[agent_name]:
                    del self._instances[agent_name]
                
                for key in list(self._session_mappings.keys()):
                    if key.startswith(f"{agent_name}:"):
                        del self._session_mappings[key]
                
                try:
                    await agent.disconnect_mcp()
                    logger.info("Cleaned up expired Agent: {}", agent_name)
                except Exception:
                    logger.exception("Error cleaning up expired Agent: {}", agent_name)
    
    def get_instance_count(self, agent_name: str | None = None) -> int:
        """Get the number of instances."""
        if agent_name:
            return len(self._instances.get(agent_name, []))
        return sum(len(v) for v in self._instances.values())
    
    def get_busy_count(self, agent_name: str | None = None) -> int:
        """Get the number of busy instances."""
        if agent_name:
            return sum(1 for a in self._instances.get(agent_name, []) if a.is_busy)
        return sum(
            sum(1 for a in instances if a.is_busy)
            for instances in self._instances.values()
        )
    
    def get_free_count(self, agent_name: str | None = None) -> int:
        """Get the number of free instances."""
        if agent_name:
            return sum(1 for a in self._instances.get(agent_name, []) if not a.is_busy)
        return sum(
            sum(1 for a in instances if not a.is_busy)
            for instances in self._instances.values()
        )
