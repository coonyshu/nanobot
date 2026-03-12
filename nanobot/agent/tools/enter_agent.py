"""Enter agent tool: activates a persistent sub-agent session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.agent import Agent, AgentPool
from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from pathlib import Path

    from nanobot.agent.agent_def import ModelOverride
    from nanobot.agent.agent_registry import AgentRegistry
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import SessionManager


class EnterAgentTool(Tool):
    """Tool that enters a persistent sub-agent session.

    Unlike ``DelegateTool`` (one-shot), this creates a long-lived session where
    the sub-agent takes over the conversation until it exits.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        agent_pool: AgentPool | None = None,
        provider: LLMProvider | None = None,
        workspace: Path | None = None,
        bus: MessageBus | None = None,
        parent_agent: Agent | None = None,
        parent_tools: ToolRegistry | None = None,
        sessions: SessionManager | None = None,
        agent: Agent | None = None,
        *,
        default_model: str | None = None,
        default_temperature: float = 0.1,
        default_max_tokens: int = 4096,
        default_reasoning_effort: str | None = None,
        config_overrides: dict[str, Any] | None = None,
        user_workspace: Path | None = None,
    ) -> None:
        if agent is not None:
            self._agent_instance = agent
            self._registry = agent.agent_registry
            self._agent_pool = None
            self._provider = agent.provider
            self._workspace = agent.workspace
            self._bus = agent.bus
            self._parent_agent = agent.parent
            self._parent_tools = agent._tools
            self._user_workspace = agent.agent_workspace.parent.parent if agent.agent_workspace.parent.name == "agents" else None
            self._default_model = agent.model
            self._default_temperature = agent.temperature
            self._default_max_tokens = agent.max_tokens
            self._default_reasoning_effort = agent.reasoning_effort
            self._config_overrides = agent._config_overrides or {}
            if self._user_workspace:
                from nanobot.session.manager import SessionManager
                self._sessions = SessionManager(self._user_workspace)
            else:
                self._sessions = None
        else:
            self._agent_instance = None
            self._registry = agent_registry
            self._agent_pool = agent_pool
            self._provider = provider
            self._workspace = workspace
            self._bus = bus
            self._parent_agent = parent_agent
            self._parent_tools = parent_tools
            self._sessions = sessions
            self._user_workspace = user_workspace
            self._default_model = default_model
            self._default_temperature = default_temperature
            self._default_max_tokens = default_max_tokens
            self._default_reasoning_effort = default_reasoning_effort
            self._config_overrides = config_overrides or {}

        self._channel: str = "cli"
        self._chat_id: str = "default"
        self._session_key: str = "cli:default"

    def set_context(self, channel: str, chat_id: str, session_key: str | None = None) -> None:
        """Set the current message origin context."""
        self._channel = channel
        self._chat_id = chat_id
        if session_key:
            self._session_key = session_key
        else:
            self._session_key = f"{channel}:{chat_id}"

        logger.debug("[EnterAgentTool] context set: channel={}, chat_id={}, session_key={}", 
                     channel, chat_id, self._session_key)

    def set_user_workspace(self, workspace: Path) -> None:
        """Set the user workspace for SubAgent memory isolation."""
        logger.debug("[EnterAgentTool] user_workspace set: {}", workspace)
        self._user_workspace = workspace

    @property
    def name(self) -> str:
        return "enter_agent"

    @property
    def description(self) -> str:
        return (
            "Enter a persistent sub-agent session. The sub-agent takes over the conversation "
            "with its own tools and context until it exits. Use this for multi-turn tasks "
            "that require sustained interaction with a specialized agent.\n\n"
            "**CRITICAL**: Only use this tool when the user's message contains trigger keywords "
            "listed in the system prompt's `<triggers>` section for any persistent agent. "
            "Do NOT use spawn or handle the request directly when triggers match."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the persistent agent to enter",
                },
                "task": {
                    "type": "string",
                    "description": "Initial task or instruction for the agent",
                },
            },
            "required": ["agent", "task"],
        }

    async def execute(self, agent: str, task: str, **kwargs: Any) -> str:
        """Execute the tool to enter a sub-agent session."""
        logger.info("[EnterAgentTool.execute] agent_name={}, task={}, user_workspace={}", 
                    agent, task[:50], self._user_workspace)
        
        if self._agent_instance is not None:
            return await self._execute_via_agent_instance(agent, task)
        
        return await self._execute_via_registry(agent, task)

    async def _execute_via_agent_instance(self, agent_name: str, task: str) -> str:
        """Execute using the parent Agent instance."""
        parent = self._agent_instance
        if parent is None:
            return "Error: No parent agent configured."
        
        # Set active_agent in session metadata
        if self._sessions:
            main_session = self._sessions.get_or_create(self._session_key)
            main_session.metadata["active_agent"] = agent_name
            self._sessions.save(main_session)
            logger.info("EnterAgentTool: set active_agent='{}' in session {}", agent_name, self._session_key)
        
        return await parent.enter_child_agent(agent_name, task)

    async def _execute_via_registry(self, agent_name: str, task: str) -> str:
        """Execute using the registry and pool."""
        if self._registry is None:
            return "Error: Agent registry not configured."
        
        agent_def = self._registry.get(agent_name)
        if agent_def is None:
            available = ", ".join(self._registry.list_names())
            return f"Error: Agent '{agent_name}' not found. Available agents: {available}"

        cfg = agent_def.get_config()
        if cfg.mode != "persistent":
            return f"Error: Agent '{agent_name}' is not a persistent agent (mode={cfg.mode})."

        context_messages: list[dict[str, Any]] = []
        if self._sessions:
            main_session = self._sessions.get_or_create(self._session_key)
            context_messages = main_session.get_history(max_messages=10)
        
        if self._agent_pool is None:
            return "Error: Agent pool not configured."
        
        agent_instance, is_new = await self._agent_pool.get_or_create(
            agent_name=agent_name,
            session_key=self._session_key,
            agent_def=agent_def,
            provider=self._provider,
            workspace=self._workspace,
            bus=self._bus,
            parent=self._parent_agent,
            parent_tools=self._parent_tools,
            channel=self._channel,
            chat_id=self._chat_id,
            user_workspace=self._user_workspace,
            default_model=self._default_model,
            default_temperature=self._default_temperature,
            default_max_tokens=self._default_max_tokens,
            default_reasoning_effort=self._default_reasoning_effort,
            config_overrides=self._config_overrides,
        )
        
        if is_new:
            agent_instance.activate(context_messages)
            await agent_instance.connect_mcp()
        
        if self._sessions:
            main_session = self._sessions.get_or_create(self._session_key)
            main_session.metadata["active_agent"] = agent_name
            self._sessions.save(main_session)
            logger.info("EnterAgentTool: set active_agent='{}' in session {}", agent_name, self._session_key)
        
        logger.info("EnterAgentTool: activated persistent session '{}' for {}",
                    agent_name, self._session_key)
        
        try:
            reply = await agent_instance.process_message(task)
            return reply
        except Exception as e:
            await self._agent_pool.release(self._session_key)
            logger.error("EnterAgentTool: failed to process initial task: {}", e)
            return f"Error: Failed to start agent session: {e}"
