"""Tenant-scoped AgentLoop pool with per-user concurrency control."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider

from nanobot.tenant.tenant_store import TenantStore
from nanobot.tenant.workspace_resolver import WorkspaceResolver

current_tenant_id: ContextVar[str] = ContextVar("current_tenant_id", default="default")
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")


class ModelNotConfiguredError(Exception):
    """Raised when no LLM model has been configured for a tenant."""


class TenantAgentPool:
    """One :class:`AgentLoop` per tenant, with per-user async locks.

    Shared across all tenants:
      - ``provider`` (LLM client, stateless)
      - ``bus`` (message bus)

    Per tenant:
      - ``AgentLoop`` instance (tools, MCP, skills)
      - ``ActionManager`` instance (WebSocket senders, dynamic tools)

    Per user:
      - ``asyncio.Lock`` (serialise concurrent requests from the same user)
      - ``user_workspace`` injected into ``process_direct`` for memory/session isolation
    """

    def __init__(
        self,
        provider: LLMProvider,
        bus: MessageBus,
        global_config: Any,
        tenant_store: TenantStore,
        resolver: WorkspaceResolver,
        global_skills_dir: Path,
        global_agents_dir: Path | None = None,
        default_action_manager: Any = None,
        session_registry: dict | None = None,
    ) -> None:
        self._provider = provider
        self._bus = bus
        self._global_config = global_config
        self._tenant_store = tenant_store
        self._resolver = resolver
        self._global_skills_dir = global_skills_dir
        self._global_agents_dir = global_agents_dir
        self._default_action_manager = default_action_manager
        self._session_registry = session_registry

        self._pool: dict[str, AgentLoop] = {}
        self._pool_lock = asyncio.Lock()
        self._user_locks: dict[str, asyncio.Lock] = {}

        self._action_managers: dict[str, Any] = {}
        self._plugin_loaders: dict[str, Any] = {}

    async def get_or_create_loop(self, tenant_id: str) -> AgentLoop:
        """Return (or lazily create) the :class:`AgentLoop` for *tenant_id*."""
        if tenant_id in self._pool:
            return self._pool[tenant_id]

        async with self._pool_lock:
            # double-check after acquiring lock
            if tenant_id in self._pool:
                return self._pool[tenant_id]

            tenant_ws = self._resolver.ensure_tenant_dirs(tenant_id)
            cfg = self._tenant_store.get_agent_config(tenant_id, self._global_config)
            logger.debug("Tenant '{}' config: agents_dirs={}", tenant_id, cfg.get("agents_dirs"))

            if not cfg.get("model"):
                raise ModelNotConfiguredError(
                    f"租户 '{tenant_id}' 的 AI 模型未配置。\n"
                    "请在配置文件中设置 agents.defaults.model 及对应的 providers API Key：\n"
                    f"  系统级: ~/.nanobots/config.json\n"
                    f"  租户级: ~/.nanobots/tenants/{tenant_id}/config.json"
                )

            from nanobot.agent.agent_context import AgentContext
            agent_context = AgentContext(
                provider=self._provider,
                workspace=tenant_ws,
                bus=self._bus,
                model=cfg.get("model"),
                temperature=cfg.get("temperature", 0.1),
                max_tokens=cfg.get("max_tokens", 4096),
                brave_api_key=cfg.get("brave_api_key"),
                exec_config=cfg.get("exec_config"),
                restrict_to_workspace=cfg.get("restrict_to_workspace", False),
                mcp_servers=cfg.get("mcp_servers"),
                agents_dirs=cfg.get("agents_dirs"),
            )
            loop = AgentLoop(
                agent_context=agent_context,
                max_iterations=cfg.get("max_tool_iterations", 40),
                memory_window=cfg.get("memory_window", 100),
            )

            # Merge global base skills into the tenant's SkillsLoader
            loop.context.skills.additional_skills_dirs = [self._global_skills_dir]

            # Merge global agents dir into the tenant's AgentRegistry
            if self._global_agents_dir:
                loop.agent_registry.add_extra_dir(self._global_agents_dir)
                # Re-run agent tool registration to pick up newly discovered agents
                # (EnterAgentTool, DelegateTool, integrated tools)
                loop._register_agent_tools()
            logger.info(
                "Created AgentLoop for tenant '{}': workspace={}, model={}",
                tenant_id, tenant_ws, cfg.get("model"),
            )

            # Create per-tenant ActionManager
            from nanobot.service_tools.action_manager import ActionManager
            action_mgr = ActionManager()
            action_mgr.set_registry(loop.tools)
            self._action_managers[tenant_id] = action_mgr

            # Inherit before-execute hook from default action_manager (for user context updates)
            if self._default_action_manager and hasattr(self._default_action_manager, '_before_execute_hook'):
                if self._default_action_manager._before_execute_hook:
                    action_mgr.set_before_execute_hook(self._default_action_manager._before_execute_hook)
                    logger.info("Inherited before-execute hook for tenant '{}' action_manager", tenant_id)

            # Load plugins (tools.py) from tenant + system skill directories
            from nanobot.agent.plugins import PluginLoader
            tenant_skills = self._resolver.tenant_skills_dir(tenant_id)
            plugin_dirs = [tenant_skills, self._global_skills_dir]
            loader = PluginLoader(plugin_dirs)
            loaded = loader.load_tools(loop.tools)
            self._plugin_loaders[tenant_id] = loader
            if loaded:
                logger.info("Plugins loaded for tenant '{}': {}", tenant_id, loaded)

            self._pool[tenant_id] = loop

            # Re-register any ws_senders from already-connected sessions of this tenant
            # (sessions connected before this loop was created registered to fallback action_manager)
            if self._session_registry is not None:
                for sid, sdata in self._session_registry.items():
                    if sdata.get("tenant_id") == tenant_id and "ws_send_json" in sdata:
                        uid = sdata.get("user_id", sid)
                        sender = sdata["ws_send_json"]
                        action_mgr.register_ws_sender(sid, sender)
                        action_mgr.register_ws_sender(uid, sender)
                        logger.info("Re-registered ws_sender for session {} (user={}) to tenant '{}' action_manager", sid, uid, tenant_id)

            return loop

    def get_action_manager(self, tenant_id: str) -> Any:
        """Return the :class:`ActionManager` for *tenant_id*.
        
        Raises ``KeyError`` if the tenant has not been initialised yet.
        """
        return self._action_managers[tenant_id]

    def get_action_manager_safe(self, tenant_id: str) -> Any | None:
        """Return the ActionManager or ``None``."""
        return self._action_managers.get(tenant_id)

    def get_plugin_loader(self, tenant_id: str) -> Any | None:
        """Return the :class:`PluginLoader` for *tenant_id*, or ``None``."""
        return self._plugin_loaders.get(tenant_id)

    def register_loop(self, tenant_id: str, loop: AgentLoop, action_mgr: Any = None) -> None:
        """Register an externally-created AgentLoop into the pool.

        Useful for the *default* tenant whose loop is created at module level
        with additional setup (monkey-patching, inspection tools, etc.).
        """
        # Ensure system-level skills dir is included (same as get_or_create_loop)
        if self._global_skills_dir not in loop.context.skills.additional_skills_dirs:
            loop.context.skills.additional_skills_dirs.insert(0, self._global_skills_dir)

        # Ensure global agents dir is included (same as get_or_create_loop)
        if self._global_agents_dir:
            loop.agent_registry.add_extra_dir(self._global_agents_dir)
            loop._register_agent_tools()

        # Load plugins from tenant + system skill directories
        from nanobot.agent.plugins import PluginLoader
        tenant_skills = self._resolver.tenant_skills_dir(tenant_id)
        plugin_dirs = [tenant_skills, self._global_skills_dir]
        loader = PluginLoader(plugin_dirs)
        loaded = loader.load_tools(loop.tools)
        self._plugin_loaders[tenant_id] = loader
        if loaded:
            logger.info("Plugins loaded for tenant '{}': {}", tenant_id, loaded)

        self._pool[tenant_id] = loop
        if action_mgr is not None:
            self._action_managers[tenant_id] = action_mgr
        logger.info("Registered external AgentLoop for tenant '{}'", tenant_id)

    # ── active agent query ─────────────────────────────────────────────────

    def get_active_agent(self, tenant_id: str, session_key: str) -> str | None:
        """Get the active agent name for a session, or None if MainAgent is active."""
        return None

    # ── per-user processing ──

    async def process_for_user(
        self,
        tenant_id: str,
        user_id: str,
        content: str,
        session_key: str,
        channel: str,
        chat_id: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, str | None]:
        """Process a message on behalf of *user_id* in *tenant_id*.

        - Acquires a per-user lock so that concurrent requests from the same
          user are serialised (preventing memory/session file corruption).
        - Sets ``current_tenant_id`` / ``current_user_id`` context vars so
          that downstream tool callbacks can identify the caller.
        - Loads user-level config (channels, agent prefs) — available via
          ``self._tenant_store.get_user_config(tenant_id, user_id)``.
        - Injects the user-specific workspace for memory & session isolation.
        
        Returns:
            tuple: (response_content, agent_name) where agent_name is None for MainAgent
        """
        # Early config validation — runs even for pre-registered loops.
        cfg = self._tenant_store.get_agent_config(tenant_id, self._global_config)
        if not cfg.get("model"):
            msg = (
                f"**租户 `{tenant_id}` 的 AI 模型未配置**\n\n"
                "请在以下配置文件中设置 `agents.defaults.model` 及对应的 `providers` API Key：\n\n"
                f"- **系统级**：`~/.nanobots/config.json`\n"
                f"- **租户级**：`~/.nanobots/tenants/{tenant_id}/config.json`\n\n"
                "配置示例：\n"
                "```json\n"
                '{\n'
                '  "agents": {\n'
                '    "defaults": {\n'
                '      "model": "qwen3.5-plus"\n'
                '    }\n'
                '  },\n'
                '  "providers": {\n'
                '    "dashscope": {\n'
                '      "apiKey": "sk-xxx"\n'
                '    }\n'
                '  }\n'
                '}\n'
                "```"
            )
            logger.warning("Model not configured for tenant '{}': {}", tenant_id, msg)
            return msg, None

        try:
            loop = await self.get_or_create_loop(tenant_id)
        except ModelNotConfiguredError as e:
            logger.warning("Model not configured for tenant '{}': {}", tenant_id, e)
            return str(e), None

        user_ws = self._resolver.ensure_user_dirs(tenant_id, user_id)

        # Pre-load and cache user config so it's ready for downstream use.
        self._tenant_store.get_user_config(tenant_id, user_id)

        lock_key = f"{tenant_id}:{user_id}"
        lock = self._user_locks.setdefault(lock_key, asyncio.Lock())

        t_token = current_tenant_id.set(tenant_id)
        u_token = current_user_id.set(user_id)
        try:
            async with lock:
                return await loop.process_direct(
                    content=content,
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                    on_progress=on_progress,
                    on_stream=on_stream,
                    user_workspace=user_ws,
                )
        finally:
            current_tenant_id.reset(t_token)
            current_user_id.reset(u_token)

    # ── lifecycle ──

    async def close_all(self) -> None:
        """Shut down every AgentLoop in the pool."""
        for tid, loop in list(self._pool.items()):
            try:
                await loop.close_mcp()
                loop.stop()
            except Exception:
                logger.exception("Error closing AgentLoop for tenant '{}'", tid)
        self._pool.clear()
        self._action_managers.clear()
        self._user_locks.clear()
        logger.info("TenantAgentPool closed")
