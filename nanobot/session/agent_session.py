"""Unified Agent session management."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class AgentSession:
    """Session data for an Agent instance.
    
    Stores messages and session state for any Agent (root or sub).
    """

    key: str
    agent_name: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_activity: float = field(default_factory=time.time)
    last_consolidated: int = 0
    exit_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
        self.last_activity = time.time()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return messages for LLM input."""
        sliced = self.messages[-max_messages:]
        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def is_expired(self, idle_timeout: int = 300) -> bool:
        """Check if idle timeout has elapsed."""
        return time.time() - self.last_activity > idle_timeout

    def touch(self) -> None:
        """Update last activity time."""
        self.last_activity = time.time()
        self.updated_at = datetime.now()


class AgentSessionManager:
    """Manages Agent sessions with persistence and timeout handling."""

    def __init__(self, workspace: Path, idle_timeout: int = 300):
        self.workspace = workspace
        self.idle_timeout = idle_timeout
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache: dict[str, AgentSession] = {}
        self._active: dict[str, AgentSession] = {}
        self._timeout_task: asyncio.Task | None = None

    def _get_session_path(self, key: str, agent_name: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(f"{agent_name}_{key}".replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str, agent_name: str) -> AgentSession:
        """Get an existing session or create a new one."""
        cache_key = f"{agent_name}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        session = self._load(key, agent_name)
        if session is None:
            session = AgentSession(key=key, agent_name=agent_name)

        self._cache[cache_key] = session
        return session

    def _load(self, key: str, agent_name: str) -> AgentSession | None:
        """Load a session from disk."""
        path = self._get_session_path(key, agent_name)
        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0
            status = "active"
            exit_summary = None

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                        status = data.get("status", "active")
                        exit_summary = data.get("exit_summary")
                    else:
                        messages.append(data)

            return AgentSession(
                key=key,
                agent_name=agent_name,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
                status=status,
                exit_summary=exit_summary,
            )
        except Exception as e:
            logger.warning("Failed to load Agent session {}/{}: {}", agent_name, key, e)
            return None

    def save(self, session: AgentSession) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key, session.agent_name)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "agent_name": session.agent_name,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated,
                "status": session.status,
                "exit_summary": session.exit_summary,
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        cache_key = f"{session.agent_name}:{session.key}"
        self._cache[cache_key] = session

    def get_active(self, key: str, agent_name: str | None = None) -> AgentSession | None:
        """Get an active session."""
        if agent_name:
            cache_key = f"{agent_name}:{key}"
            return self._active.get(cache_key)
        for k, s in self._active.items():
            if k.endswith(f":{key}"):
                return s
        return None

    def has_active(self, key: str, agent_name: str | None = None) -> bool:
        """Check if there's an active session."""
        session = self.get_active(key, agent_name)
        return session is not None and session.status == "active"

    def set_active(self, session: AgentSession) -> None:
        """Mark a session as active."""
        cache_key = f"{session.agent_name}:{session.key}"
        self._active[cache_key] = session
        session.status = "active"

    def deactivate(self, key: str, agent_name: str) -> AgentSession | None:
        """Deactivate a session."""
        cache_key = f"{agent_name}:{key}"
        session = self._active.pop(cache_key, None)
        if session:
            session.status = "closed"
        return session

    def list_sessions(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        """List all sessions, optionally filtered by agent."""
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            s_agent = data.get("agent_name")
                            if agent_name and s_agent != agent_name:
                                continue
                            sessions.append({
                                "key": data.get("key"),
                                "agent_name": s_agent,
                                "status": data.get("status"),
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    async def start_timeout_checker(
        self,
        on_expire: Callable[[str, str, AgentSession], Awaitable[None]],
    ) -> None:
        """Start background task that checks for expired sessions every 30s."""
        async def _checker() -> None:
            while True:
                await asyncio.sleep(30)
                expired = [
                    (k, s) for k, s in list(self._active.items())
                    if s.is_expired(self.idle_timeout)
                ]
                for cache_key, session in expired:
                    logger.info("Agent session '{}' expired (timeout)", cache_key)
                    self._active.pop(cache_key, None)
                    try:
                        agent_name, key = cache_key.rsplit(":", 1)
                        await on_expire(key, agent_name, session)
                    except Exception:
                        logger.exception("Error handling session timeout for {}", cache_key)

        self._timeout_task = asyncio.create_task(_checker())

    def stop(self) -> None:
        """Cancel the timeout checker."""
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            self._timeout_task = None

    def clear_cache(self, key: str, agent_name: str) -> None:
        """Remove a session from the in-memory cache."""
        cache_key = f"{agent_name}:{key}"
        self._cache.pop(cache_key, None)
        self._active.pop(cache_key, None)


SubAgentSession = AgentSession
SubAgentSessionManager = AgentSessionManager
