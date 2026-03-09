"""User store with bcrypt password hashing (JSON file-based)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import bcrypt
from loguru import logger

from nanobot.multi_tenant.models import User
from nanobot.multi_tenant.workspace_resolver import WorkspaceResolver


class UserStore:
    """Manages user registration and authentication via ``data/users.json``."""

    def __init__(self, users_file: Path, resolver: WorkspaceResolver) -> None:
        self._file = users_file
        self._resolver = resolver
        self._cache: dict[str, User] | None = None
        self._lock = asyncio.Lock()

    # ── public API ──

    async def register(
        self,
        username: str,
        password: str,
        tenant_id: str,
        role: str = "user",
    ) -> User:
        """Register a new user. Raises ``ValueError`` on duplicate username within tenant."""
        async with self._lock:
            users = self._load()
            # uniqueness check within tenant
            for u in users.values():
                if u.username == username and u.tenant_id == tenant_id:
                    raise ValueError(f"Username '{username}' already exists in tenant '{tenant_id}'")

            user = User(
                user_id=uuid.uuid4().hex,
                username=username,
                password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                tenant_id=tenant_id,
                role=role,
            )
            users[user.user_id] = user
            self._save(users)
            self._resolver.ensure_user_dirs(tenant_id, user.user_id)
            logger.info("Registered user {}/{} ({})", tenant_id, username, user.user_id)
            return user

    async def authenticate(self, username: str, password: str, tenant_id: str | None = None) -> User | None:
        """Return the ``User`` if credentials are valid, else ``None``.
        
        If tenant_id is provided, also verify the user belongs to that tenant.
        For login, tenant_id should be None (only username+password required).
        """
        users = self._load()
        for u in users.values():
            if u.username == username and u.enabled:
                if tenant_id is not None and u.tenant_id != tenant_id:
                    continue
                if bcrypt.checkpw(password.encode(), u.password_hash.encode()):
                    return u
        return None

    def get_by_id(self, user_id: str) -> User | None:
        return self._load().get(user_id)

    def list_by_tenant(self, tenant_id: str) -> list[User]:
        return [u for u in self._load().values() if u.tenant_id == tenant_id]

    async def update_password(self, user_id: str, new_password: str) -> bool:
        async with self._lock:
            users = self._load()
            user = users.get(user_id)
            if not user:
                return False
            user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            self._save(users)
            return True

    # ── persistence ──

    def _load(self) -> dict[str, User]:
        if self._cache is not None:
            return self._cache
        if not self._file.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            self._cache = {uid: User.from_dict(d) for uid, d in raw.items()}
        except Exception:
            logger.exception("Failed to load users from {}", self._file)
            self._cache = {}
        return self._cache

    def _save(self, users: dict[str, User]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {uid: u.to_dict() for uid, u in users.items()}
        self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache = users
