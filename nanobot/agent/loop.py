"""Agent loop: message router and dispatcher."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from nanobot.agent.agent import Agent
from nanobot.agent.agent_context import AgentContext
from nanobot.agent.agent_registry import AgentRegistry
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig


class AgentLoop:
    """
    Message router and dispatcher.
    
    Responsibilities:
    1. Receive messages from the bus
    2. Find or create the appropriate Agent
    3. Delegate processing to the Agent
    4. Send responses back
    """

    def __init__(
        self,
        agent_context: AgentContext,
        channels_config: ChannelsConfig | None = None,
        max_iterations: int = 40,
        memory_window: int = 100,
    ):
        self.agent_context = agent_context
        self.channels_config = channels_config
        self.provider = agent_context.provider
        self.workspace = agent_context.workspace
        self.model = agent_context.model
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._agents: dict[str, Agent] = {}
        self._processing_lock = asyncio.Lock()

    @property
    def tools(self):
        return self.agent_context.tools

    @property
    def sessions(self):
        return self.agent_context.sessions

    @property
    def bus(self):
        return self.agent_context.bus

    @property
    def context(self):
        return self.agent_context.context_builder

    @property
    def agent_registry(self):
        return self.agent_context.agent_registry

    def _register_agent_tools(self) -> None:
        self.agent_context._register_agent_tools()

    async def run(self) -> None:
        self._running = True
        await self.agent_context.connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(
                    lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) 
                    if t in self._active_tasks.get(k, []) else None
                )

    async def _handle_stop(self, msg: InboundMessage) -> None:
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.agent_context.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        await self.agent_context.close_mcp()

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        user_workspace: Path | None = None,
    ) -> OutboundMessage | None:
        from nanobot.session.manager import SessionManager
        
        sessions = SessionManager(user_workspace) if user_workspace else self.sessions
        key = session_key or msg.session_key
        session = sessions.get_or_create(key)
        
        if msg.channel == "system":
            return await self._handle_system_message(msg, session, sessions, user_workspace)
        
        if not session.metadata.get("initialized"):
            self._init_session(session)
            sessions.save(session)

        cmd = msg.content.strip().lower()
        if cmd == "/new":
            return await self._handle_new_command(msg, session, sessions)
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        agent = await self._get_or_create_agent(key, msg.channel, msg.chat_id, sessions, user_workspace)
        
        self.agent_context.set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        
        progress_cb = on_progress or self._create_progress_callback(msg)
        stream_cb = on_stream
        
        try:
            response = await agent.process_message(
                msg.content,
                on_progress=progress_cb,
                on_stream=stream_cb,
            )
        except Exception as e:
            logger.exception("Agent error for session {}", key)
            response = f"Error: {e}"
        
        # Save session with last_active timestamp
        # Reload session to preserve active_agent if set by enter_agent tool
        sessions.invalidate(key)
        session = sessions.get_or_create(key)
        session.metadata["last_active"] = session.updated_at.isoformat()
        sessions.save(session)
        
        if self._should_use_message_tool(agent):
            return None

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=response,
            metadata=msg.metadata or {},
            agent_name=agent.agent_name if hasattr(agent, 'agent_name') else None,
        )

    async def _handle_system_message(
        self,
        msg: InboundMessage,
        session,
        sessions,
        user_workspace: Path | None = None,
    ) -> OutboundMessage:
        channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                            else ("cli", msg.chat_id))
        key = f"{channel}:{chat_id}"
        
        agent = await self._get_or_create_agent(key, channel, chat_id, sessions, user_workspace)
        response = await agent.process_message(msg.content)
        
        return OutboundMessage(
            channel=channel, chat_id=chat_id,
            content=response or "Background task completed.",
            agent_name=agent.agent_name if hasattr(agent, 'agent_name') else None,
        )

    async def _get_or_create_agent(
        self,
        session_key: str,
        channel: str,
        chat_id: str,
        sessions,
        user_workspace: Path | None = None,
    ) -> Agent:
        if session_key in self._agents:
            return self._agents[session_key]
        
        session = sessions.get_or_create(session_key)
        agent_name = session.metadata.get("active_agent", "default")
        
        agent_def = self.agent_registry.get(agent_name)
        if agent_def is None:
            from nanobot.agent.agent_def import AgentConfig, _ConfigAgentDefinition
            default_config = AgentConfig(name="default", description="Default agent")
            agent_def = _ConfigAgentDefinition(default_config)
        
        agent = Agent(
            agent_def=agent_def,
            provider=self.provider,
            workspace=self.workspace,
            bus=self.bus,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            default_model=self.model,
            default_temperature=self.agent_context.temperature,
            default_max_tokens=self.agent_context.max_tokens,
            default_reasoning_effort=self.agent_context.reasoning_effort,
            user_workspace=user_workspace,
            agent_registry=self.agent_registry,
        )
        
        history = session.get_history(max_messages=self.memory_window)
        agent.activate(history)
        await agent.connect_mcp()
        
        self._agents[session_key] = agent
        return agent

    def _init_session(self, session) -> None:
        session.metadata["initialized"] = True
        session.metadata["model"] = self.model
        session.metadata["temperature"] = self.agent_context.temperature
        session.metadata["max_tokens"] = self.agent_context.max_tokens
        session.metadata["reasoning_effort"] = self.agent_context.reasoning_effort
        session.metadata["enable_thinking"] = True
        session.metadata["show_tool_hints"] = True
        session.metadata["message_count"] = 0
        session.metadata["tool_call_count"] = 0
        session.metadata["created_at"] = session.created_at.isoformat()

    def _create_progress_callback(self, msg: InboundMessage):
        show_tool_hints = True
        async def _progress(content: str, *, tool_hint: bool = False) -> None:
            if tool_hint and not show_tool_hints:
                return
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))
        return _progress

    def _should_use_message_tool(self, agent: Agent) -> bool:
        from nanobot.agent.tools.message import MessageTool
        mt = agent.tools.get("message")
        return isinstance(mt, MessageTool) and mt._sent_in_turn

    async def _handle_new_command(
        self,
        msg: InboundMessage,
        session,
        sessions,
    ) -> OutboundMessage:
        if session_key := msg.session_key:
            if agent := self._agents.pop(session_key, None):
                try:
                    await agent.consolidate_memory(archive_all=True)
                except Exception:
                    logger.exception("Memory consolidation failed for {}", session_key)
        
        session.clear()
        sessions.save(session)
        sessions.invalidate(session.key)
        
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                              content="New session started.")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        user_workspace: Path | None = None,
    ) -> tuple[str, str | None]:
        from nanobot.session.manager import SessionManager
        
        logger.info(f"process_direct started: channel={channel}, chat_id={chat_id}, content={content[:50]}...")
        
        await self.agent_context.mcp_manager.ensure_connected(timeout=15.0)
        
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress, on_stream=on_stream,
            user_workspace=user_workspace,
        )
        
        sessions = SessionManager(user_workspace) if user_workspace else self.sessions
        logger.info(f"[process_direct] Reading session: session_key={session_key}, user_workspace={user_workspace}, sessions_dir={sessions.sessions_dir}")
        # Invalidate cache to force reloading from disk
        sessions.invalidate(session_key)
        session = sessions.get_or_create(session_key)
        agent_name = session.metadata.get("active_agent", None)
        logger.info(f"[process_direct] Session metadata: active_agent={agent_name}, metadata={session.metadata}")
        if "active_agent" in session.metadata:
            del session.metadata["active_agent"]
            sessions.save(session)
        return response.content if response else "", agent_name
