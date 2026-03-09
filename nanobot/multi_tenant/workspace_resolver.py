"""Workspace path resolver for tenant / user directory isolation."""

from __future__ import annotations

from pathlib import Path

from loguru import logger


class WorkspaceResolver:
    """Maps tenant_id / user_id to file-system paths.

    Directory layout::

        base_dir/                           # e.g. ~/.nanobots/
        ├── config.json                     # system-level config
        ├── tenants.json                    # tenant registry
        ├── users.json                      # user registry
        ├── tenants/                        # all tenant workspaces
        │   └── {tenant_id}/
        │       ├── config.json             # tenant-level config (providers, mcp, agent)
        │       ├── skills/                 # tenant-specific skills
        │       ├── AGENTS.md
        │       ├── mochat/                 # shared channel state
        │       ├── matrix-store/           # shared encryption state
        │       ├── whatsapp-auth/          # shared bridge auth
        │       └── users/
        │           └── {user_id}/
        │               ├── config.json     # user-level config (channels, agent prefs)
        │               ├── memory/
        │               │   ├── MEMORY.md
        │               │   └── HISTORY.md
        │               ├── sessions/
        │               ├── media/          # user media files
        │               └── cron/
        │                   └── jobs.json   # user cron tasks
        └── ...
    """

    TENANTS_SUBDIR = "tenants"

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.tenants_dir = base_dir / self.TENANTS_SUBDIR

    # ── tenant paths ──

    def tenant_workspace(self, tenant_id: str) -> Path:
        """Root workspace for a tenant (used as AgentLoop.workspace)."""
        return self.tenants_dir / tenant_id

    def tenant_config_path(self, tenant_id: str) -> Path:
        """Path to tenant-specific config.json (providers, mcp, etc.)."""
        return self.tenants_dir / tenant_id / "config.json"

    def tenant_skills_dir(self, tenant_id: str) -> Path:
        return self.tenants_dir / tenant_id / "skills"

    # ── user paths ──

    def user_workspace(self, tenant_id: str, user_id: str) -> Path:
        """Root workspace for a user (used for SessionManager / MemoryStore)."""
        return self.tenants_dir / tenant_id / "users" / user_id

    def user_memory_dir(self, tenant_id: str, user_id: str) -> Path:
        return self.user_workspace(tenant_id, user_id) / "memory"

    def user_sessions_dir(self, tenant_id: str, user_id: str) -> Path:
        return self.user_workspace(tenant_id, user_id) / "sessions"

    def user_config_path(self, tenant_id: str, user_id: str) -> Path:
        """Path to user-specific config.json (channels, agent prefs)."""
        return self.user_workspace(tenant_id, user_id) / "config.json"

    def user_media_dir(self, tenant_id: str, user_id: str) -> Path:
        """User-level media download directory."""
        return self.user_workspace(tenant_id, user_id) / "media"

    def user_cron_dir(self, tenant_id: str, user_id: str) -> Path:
        """User-level cron jobs directory."""
        return self.user_workspace(tenant_id, user_id) / "cron"

    # ── helpers ──

    def ensure_tenant_dirs(self, tenant_id: str) -> Path:
        """Create tenant directory structure. Returns tenant workspace path."""
        ws = self.tenant_workspace(tenant_id)
        ws.mkdir(parents=True, exist_ok=True)
        # Also create skills directory
        skills_dir = self.tenant_skills_dir(tenant_id)
        skills_dir.mkdir(parents=True, exist_ok=True)
        return ws

    def ensure_user_dirs(self, tenant_id: str, user_id: str) -> Path:
        """Create user directory structure. Returns user workspace path."""
        ws = self.user_workspace(tenant_id, user_id)
        (ws / "memory").mkdir(parents=True, exist_ok=True)
        (ws / "sessions").mkdir(parents=True, exist_ok=True)
        (ws / "media").mkdir(parents=True, exist_ok=True)
        (ws / "cron").mkdir(parents=True, exist_ok=True)
        logger.debug("Ensured user dirs: {}", ws)
        return ws
