from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from nanobot.tenant.agent_pool import current_tenant_id


@dataclass
class ResolvedContext:
    user_id: str
    tenant_id: Optional[str]
    session_id: Optional[str]
    session_data: Optional[dict]
    session_obj: Any
    active_agent: Optional[str]


class ContextResolver:
    def __init__(self, svc, tenant_pool=None):
        self.svc = svc
        self.tenant_pool = tenant_pool or getattr(svc, "tenant_pool", None)

    def resolve(self, user_id: str) -> ResolvedContext:
        session_id, session_data = self.svc.get_user_session(user_id)
        session_obj = session_data.get("session") if session_data else None

        tenant_id = None
        if session_data:
            tenant_id = session_data.get("tenant_id")
        if not tenant_id:
            auth_info = self.svc.get_user_auth_info(user_id) or {}
            tenant_id = auth_info.get("tenant_id")
        if not tenant_id:
            tenant_id = current_tenant_id.get()

        active_agent = self.svc.get_user_active_agent(user_id)
        if not active_agent and session_obj:
            active_agent = getattr(session_obj, "active_agent_name", None)
        if not active_agent and session_obj and hasattr(session_obj, "metadata"):
            active_agent = session_obj.metadata.get("active_agent")
        if not active_agent and session_obj and hasattr(session_obj, "agent_context"):
            active_agent = session_obj.agent_context.get("active_agent")

        if not active_agent and tenant_id and self.tenant_pool:
            active_agent = self._load_persistent_active_agent(tenant_id, user_id)

        return ResolvedContext(
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            session_data=session_data,
            session_obj=session_obj,
            active_agent=active_agent,
        )

    def set_active_agent(self, user_id: str, agent_name: Optional[str]) -> None:
        self.svc.set_user_active_agent(user_id, agent_name)
        ctx = self.resolve(user_id)
        if ctx.session_obj and hasattr(ctx.session_obj, "active_agent_name"):
            ctx.session_obj.active_agent_name = agent_name
        if ctx.tenant_id and self.tenant_pool:
            self._save_persistent_active_agent(ctx.tenant_id, user_id, agent_name)

    def clear_active_agent(self, user_id: str) -> None:
        self.svc.clear_user_active_agent(user_id)
        ctx = self.resolve(user_id)
        if ctx.session_obj and hasattr(ctx.session_obj, "active_agent_name"):
            ctx.session_obj.active_agent_name = None
        if ctx.tenant_id and self.tenant_pool:
            self._save_persistent_active_agent(ctx.tenant_id, user_id, None)

    def _load_persistent_active_agent(self, tenant_id: str, user_id: str) -> Optional[str]:
        try:
            from nanobot.session.manager import SessionManager

            user_ws = self.tenant_pool._resolver.ensure_user_dirs(tenant_id, user_id)
            sessions = SessionManager(user_ws)
            sess = sessions.get_or_create(f"voice:{user_id}")
            return sess.metadata.get("active_agent")
        except Exception as e:
            logger.debug("[ContextResolver] load persistent active_agent failed: {}", e)
            return None

    def _save_persistent_active_agent(
        self,
        tenant_id: str,
        user_id: str,
        agent_name: Optional[str],
    ) -> None:
        try:
            from nanobot.session.manager import SessionManager

            user_ws = self.tenant_pool._resolver.ensure_user_dirs(tenant_id, user_id)
            sessions = SessionManager(user_ws)
            sess = sessions.get_or_create(f"voice:{user_id}")
            if agent_name:
                sess.metadata["active_agent"] = agent_name
            elif "active_agent" in sess.metadata:
                del sess.metadata["active_agent"]
            sessions.save(sess)
        except Exception as e:
            logger.debug("[ContextResolver] save persistent active_agent failed: {}", e)
