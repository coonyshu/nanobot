"""Agent Context: encapsulates agent-related functionality."""

from __future__ import annotations

import asyncio
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.agent_registry import AgentRegistry
from nanobot.agent.context import ContextBuilder
from nanobot.agent.mcp_manager import MCPManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
    from nanobot.cron.service import CronService
    from nanobot.providers.base import LLMProvider


class AgentContext:
    """
    Encapsulates agent-related functionality.
    
    Responsibilities:
    - Tool registration and management
    - MCP connection management
    - Subagent management
    - Agent registry
    - Session management
    - Context building
    """
    
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        agents_dirs: list[str] | list[Path] | None = None,
        tools: ToolRegistry | None = None,
        skip_agent_tools: bool = False,
    ):
        from nanobot.config.schema import ExecToolConfig
        
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        
        self.tools = ToolRegistry()
        
        self.context_builder = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        
        self._agents_dirs: list[Path] | None = None
        if agents_dirs:
            self._agents_dirs = [Path(d) if isinstance(d, str) else d for d in agents_dirs]
        
        self.agent_registry = AgentRegistry(workspace, extra_dirs=self._agents_dirs)
        self.context_builder.set_agent_registry(self.agent_registry)
        
        self.mcp_manager = MCPManager(servers=mcp_servers, tools=self.tools)
        
        from nanobot.agent.subagent import SubagentManager
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        
        from nanobot.agent.agent import AgentPool
        self._agent_pool = AgentPool()
        
        self._consolidating: set[str] = set()
        self._consolidation_tasks: set[asyncio.Task] = set()
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        
        if tools is not None:
            self.tools = tools
        else:
            self._register_default_tools()
        
        if not skip_agent_tools and tools is None:
            self._register_agent_tools()
    
    @classmethod
    def create_simple(
        cls,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
    ) -> "AgentContext":
        """Create a simplified AgentContext without agent registry or subagents."""
        from nanobot.config.schema import ExecToolConfig
        
        tools = ToolRegistry()
        allowed_dir = workspace if restrict_to_workspace else None
        
        for tool_cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            tools.register(tool_cls(workspace=workspace, allowed_dir=allowed_dir))
        
        tools.register(ExecTool(
            working_dir=str(workspace),
            timeout=(exec_config or ExecToolConfig()).timeout,
            restrict_to_workspace=restrict_to_workspace,
            path_append=(exec_config or ExecToolConfig()).path_append,
        ))
        tools.register(WebSearchTool(api_key=brave_api_key, proxy=web_proxy))
        tools.register(WebFetchTool(proxy=web_proxy))
        tools.register(MessageTool(send_callback=bus.publish_outbound))
        
        return cls(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=exec_config or ExecToolConfig(),
            restrict_to_workspace=restrict_to_workspace,
            tools=tools,
            skip_agent_tools=True,
        )
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
    
    def _register_agent_tools(self) -> None:
        """Register tools for discovered agents."""
        from nanobot.agent.tools.enter_agent import EnterAgentTool
        from nanobot.agent.tools.delegate import DelegateTool
        
        if not self.agent_registry:
            return
        
        for agent_name in self.agent_registry.list_names():
            agent_def = self.agent_registry.get(agent_name)
            if agent_def is None:
                continue
            cfg = agent_def.get_config()
            
            if cfg.mode == "integrated":
                agent_def.register_tools(self.tools, parent_tools=self.tools)
                logger.debug("Registered integrated tools for agent '{}'", agent_name)
        
        if not self.tools.has("enter_agent"):
            self.tools.register(EnterAgentTool(
                agent_registry=self.agent_registry,
                agent_pool=self._agent_pool,
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                parent_tools=self.tools,
                sessions=self.sessions,
            ))
        
        if not self.tools.has("delegate"):
            self.tools.register(DelegateTool(
                agent_registry=self.agent_registry,
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                parent_tools=self.tools,
            ))
        
        logger.debug("Agent tools registered: {} agents discovered", 
                    len(self.agent_registry.list_names()))
    
    async def connect_mcp(self) -> None:
        """Connect to MCP servers."""
        await self.mcp_manager.connect()
    
    async def close_mcp(self) -> None:
        """Close MCP connections."""
        await self.mcp_manager.close()
    
    def set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))
        
        # For enter_agent tool, pass session_key
        if tool := self.tools.get("enter_agent"):
            if hasattr(tool, "set_context"):
                session_key = f"{channel}:{chat_id}"
                logger.info("[AgentContext.set_tool_context] Setting enter_agent context: channel={}, chat_id={}, session_key={}", 
                           channel, chat_id, session_key)
                tool.set_context(channel, chat_id, session_key)
        else:
            logger.warning("[AgentContext.set_tool_context] enter_agent tool not found in tools registry")
    
    def get_consolidation_lock(self, session_key: str) -> asyncio.Lock:
        """Get or create a consolidation lock for a session."""
        return self._consolidation_locks.setdefault(session_key, asyncio.Lock())
    
    def is_consolidating(self, session_key: str) -> bool:
        """Check if a session is being consolidated."""
        return session_key in self._consolidating
    
    def start_consolidation(self, session_key: str) -> None:
        """Mark a session as being consolidated."""
        self._consolidating.add(session_key)
    
    def end_consolidation(self, session_key: str) -> None:
        """Mark a session as no longer being consolidated."""
        self._consolidating.discard(session_key)
    
    def add_consolidation_task(self, task: asyncio.Task) -> None:
        """Add a consolidation task to track."""
        self._consolidation_tasks.add(task)
    
    def remove_consolidation_task(self, task: asyncio.Task) -> None:
        """Remove a consolidation task from tracking."""
        self._consolidation_tasks.discard(task)
