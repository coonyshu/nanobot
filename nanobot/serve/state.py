"""
Runtime state for the serve module.

Holds all mutable runtime state (voice sessions, tab contexts, auth info)
and provides accessor functions. State is stored on app.state at startup.
"""

from typing import Optional, Dict, Any
from contextvars import ContextVar

from loguru import logger


# Context variable: identifies whether execution is inside a subagent
is_subagent_context: ContextVar[bool] = ContextVar('is_subagent_context', default=False)

# Global ServiceState instance
_service_state_instance: Optional['ServiceState'] = None


class ServiceState:
    """
    Mutable runtime state container.

    An instance is created once during ``create_app()`` and attached to
    ``app.state.svc``.  Route handlers access it via
    ``request.app.state.svc``.
    """

    def __init__(self):
        # Active voice WebSocket sessions, key = session_id
        self.active_voice_sessions: Dict[str, Any] = {}
        # Per-user frontend tab context (updated by context_update messages)
        self.tab_contexts: Dict[str, dict] = {}
        # Per-user auth info (extracted from JWT on WS connect)
        self.user_auth_info: Dict[str, dict] = {}
        # Per-user active agent: user_id -> agent_name (None = MainAgent)
        self.user_active_agent: Dict[str, str | None] = {}
        # Tenant pool reference (set by app.py after creation)
        self.tenant_pool: Optional[Any] = None
        # Default action manager reference (set by app.py after creation)
        self.action_manager: Optional[Any] = None

    @classmethod
    def get(cls) -> Optional['ServiceState']:
        """Get the global ServiceState instance."""
        return _service_state_instance

    @classmethod
    def set_instance(cls, instance: 'ServiceState') -> None:
        """Set the global ServiceState instance."""
        global _service_state_instance
        _service_state_instance = instance

    # -- voice sessions -------------------------------------------------------

    def get_user_session(self, user_id: str):
        """Find the first active voice session for *user_id*.

        Returns ``(session_id, session_data)`` or ``(None, None)``.
        """
        for sid, data in self.active_voice_sessions.items():
            if data.get("user_id") == user_id:
                return sid, data
        return None, None

    def register_voice_session(
        self, user_id: str, session_id: str, websocket, session,
        tenant_id: str, action_mgr,
    ):
        """Register a voice session for receiving subagent real-time responses."""
        self.active_voice_sessions[session_id] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "websocket": websocket,
            "session": session,
        }

        async def ws_send_json(msg: dict):
            await websocket.send_json(msg)

        action_mgr.register_ws_sender(session_id, ws_send_json)
        action_mgr.register_ws_sender(user_id, ws_send_json)
        self.active_voice_sessions[session_id]["ws_send_json"] = ws_send_json
        logger.info(
            "Registered voice session for user: {} (session_id={}, tenant={})",
            user_id, session_id, tenant_id,
        )

    def unregister_voice_session(self, user_id: str, session_id: str, action_mgr):
        """Unregister a voice session."""
        if session_id in self.active_voice_sessions:
            tenant_id = self.active_voice_sessions[session_id].get("tenant_id", "default")
            del self.active_voice_sessions[session_id]
            action_mgr.unregister_ws_sender(session_id)
            remaining = [
                v for v in self.active_voice_sessions.values()
                if v.get("user_id") == user_id
            ]
            if not remaining:
                action_mgr.unregister_ws_sender(user_id)
            logger.info(
                "Unregistered voice session for user: {} (session_id={}, tenant={})",
                user_id, session_id, tenant_id,
            )

    # -- auth info -------------------------------------------------------------

    def set_user_auth_info(self, user_id: str, auth_info: dict):
        self.user_auth_info[user_id] = auth_info
        logger.info("Set auth info for user: {}, username={}", user_id, auth_info.get("username"))

    def get_user_auth_info(self, user_id: str) -> Optional[dict]:
        return self.user_auth_info.get(user_id)

    def clear_user_auth_info(self, user_id: str):
        self.user_auth_info.pop(user_id, None)

    # -- active agent -----------------------------------------------------------

    def set_user_active_agent(self, user_id: str, agent_name: str | None):
        """Set the active agent for a user. None = MainAgent."""
        self.user_active_agent[user_id] = agent_name
        logger.info("Set active agent for user {}: {}", user_id, agent_name or "MainAgent")

    def get_user_active_agent(self, user_id: str) -> str | None:
        """Get the active agent for a user. None = MainAgent."""
        return self.user_active_agent.get(user_id)

    def clear_user_active_agent(self, user_id: str):
        """Clear the active agent for a user (reset to MainAgent)."""
        self.user_active_agent.pop(user_id, None)

    # -- tab context -----------------------------------------------------------

    def update_tab_context(self, user_id: str, context: dict):
        self.tab_contexts[user_id] = context
        logger.info(
            "Tab context updated for user {}: type={}, tab={}",
            user_id, context.get("type"), context.get("activeTabId"),
        )

    def get_tab_context(self, user_id: str) -> Optional[dict]:
        return self.tab_contexts.get(user_id)

    # -- context prefix builders -----------------------------------------------

    def build_tab_context_prefix(self, user_id: str) -> str:
        ctx = self.tab_contexts.get(user_id)
        if not ctx:
            return ""
        if ctx.get("closedTabId"):
            return ""

        tab_type = ctx.get("type", "unknown")
        parts = [f"[当前页签上下文] 类型: {tab_type}"]

        if tab_type == "inspection":
            if ctx.get("userId"):
                parts.append(f"用户号: {ctx['userId']}")
            if ctx.get("address"):
                parts.append(f"地址: {ctx['address']}")
            if ctx.get("workType"):
                parts.append(f"工作类型: {ctx['workType']}")
            if ctx.get("currentScene"):
                parts.append(f"当前场景: {ctx['currentScene']}")
            completed = ctx.get("completedScenes", [])
            if completed:
                parts.append(f"已完成场景: {', '.join(completed)}")
            # Include current scene collected fields
            scene_fields = ctx.get("sceneFields", {})
            if scene_fields:
                fields_desc = ", ".join([f"{k}={v}" for k, v in scene_fields.items()])
                parts.append(f"当前场景已采集字段: {fields_desc}")
        elif tab_type == "task-list":
            parts.append("当前在任务列表页签")

        return " | ".join(parts)

    def build_auth_context_prefix(self, user_id: str) -> str:
        auth_info = self.get_user_auth_info(user_id)
        if not auth_info:
            return ""
        username = auth_info.get("username")
        role = auth_info.get("role", "user")
        tenant_id = auth_info.get("tenant_id", "default")
        if not username:
            return ""
        return f"[当前登录用户] 用户名: {username} | 角色: {role} | 租户: {tenant_id}"
