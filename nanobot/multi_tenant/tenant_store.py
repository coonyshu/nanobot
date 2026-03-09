"""Tenant configuration store (JSON file-based)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.multi_tenant.models import Tenant, TenantConfig, UserConfig
from nanobot.multi_tenant.workspace_resolver import WorkspaceResolver


class TenantStore:
    """Reads / writes tenant data from ``data/tenants.json`` and per-tenant config.json."""

    def __init__(self, tenants_file: Path, resolver: WorkspaceResolver | None = None) -> None:
        self._file = tenants_file
        self._resolver = resolver
        self._cache: dict[str, Tenant] | None = None
        self._config_cache: dict[str, TenantConfig] = {}
        self._user_config_cache: dict[str, UserConfig] = {}  # key: "{tid}:{uid}"

    def set_resolver(self, resolver: WorkspaceResolver) -> None:
        """Set the workspace resolver (for loading tenant config.json)."""
        self._resolver = resolver

    # ── public API ──

    def get(self, tenant_id: str) -> Tenant | None:
        tenants = self._load()
        return tenants.get(tenant_id)

    def list_all(self) -> list[Tenant]:
        return list(self._load().values())

    def create(self, tenant: Tenant) -> Tenant:
        tenants = self._load()
        if tenant.tenant_id in tenants:
            raise ValueError(f"Tenant '{tenant.tenant_id}' already exists")
        tenants[tenant.tenant_id] = tenant
        self._save(tenants)
        return tenant

    def update(self, tenant: Tenant) -> Tenant:
        tenants = self._load()
        if tenant.tenant_id not in tenants:
            raise ValueError(f"Tenant '{tenant.tenant_id}' not found")
        tenants[tenant.tenant_id] = tenant
        self._save(tenants)
        return tenant

    def get_tenant_config(self, tenant_id: str) -> TenantConfig:
        """Load tenant-specific config.json from the tenant workspace.
        
        Returns empty TenantConfig if file doesn't exist.
        """
        if tenant_id in self._config_cache:
            return self._config_cache[tenant_id]
        
        if not self._resolver:
            logger.warning("No resolver set, returning empty TenantConfig")
            return TenantConfig()
        
        config_path = self._resolver.tenant_config_path(tenant_id)
        if not config_path.exists():
            self._config_cache[tenant_id] = TenantConfig()
            return self._config_cache[tenant_id]
        
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            config = TenantConfig.from_dict(raw)
            self._config_cache[tenant_id] = config
            logger.info("Loaded tenant config for '{}' from {}", tenant_id, config_path)
            return config
        except Exception:
            logger.exception("Failed to load tenant config from {}", config_path)
            return TenantConfig()

    def save_tenant_config(self, tenant_id: str, config: TenantConfig) -> None:
        """Save tenant-specific config.json to the tenant workspace."""
        if not self._resolver:
            raise ValueError("No resolver set")
        
        config_path = self._resolver.tenant_config_path(tenant_id)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self._config_cache[tenant_id] = config
        logger.info("Saved tenant config for '{}' to {}", tenant_id, config_path)

    def reload_tenant_config(self, tenant_id: str) -> TenantConfig:
        """Force reload of tenant config.json."""
        if tenant_id in self._config_cache:
            del self._config_cache[tenant_id]
        return self.get_tenant_config(tenant_id)

    # ── user config ──

    def get_user_config(self, tenant_id: str, user_id: str) -> UserConfig:
        """Load user-specific config.json from the user workspace.

        Returns empty UserConfig if file doesn't exist.
        """
        cache_key = f"{tenant_id}:{user_id}"
        if cache_key in self._user_config_cache:
            return self._user_config_cache[cache_key]

        if not self._resolver:
            return UserConfig()

        config_path = self._resolver.user_config_path(tenant_id, user_id)
        if not config_path.exists():
            self._user_config_cache[cache_key] = UserConfig()
            return self._user_config_cache[cache_key]

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            config = UserConfig.from_dict(raw)
            self._user_config_cache[cache_key] = config
            logger.info("Loaded user config for '{}:{}' from {}", tenant_id, user_id, config_path)
            return config
        except Exception:
            logger.exception("Failed to load user config from {}", config_path)
            return UserConfig()

    def save_user_config(self, tenant_id: str, user_id: str, config: UserConfig) -> None:
        """Save user-specific config.json to the user workspace."""
        if not self._resolver:
            raise ValueError("No resolver set")

        config_path = self._resolver.user_config_path(tenant_id, user_id)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cache_key = f"{tenant_id}:{user_id}"
        self._user_config_cache[cache_key] = config
        logger.info("Saved user config for '{}:{}' to {}", tenant_id, user_id, config_path)

    def reload_user_config(self, tenant_id: str, user_id: str) -> UserConfig:
        """Force reload of user config.json."""
        cache_key = f"{tenant_id}:{user_id}"
        self._user_config_cache.pop(cache_key, None)
        return self.get_user_config(tenant_id, user_id)

    # ── merged agent config ──

    def get_agent_config(self, tenant_id: str, global_config: Any = None, user_id: str | None = None) -> dict[str, Any]:
        """Return merged agent configuration.

        Priority (highest to lowest):
        1. User config.json   (channels, agent prefs)  — if *user_id* given
        2. Tenant config.json (providers, mcp_servers, agent)
        3. Tenant config_override in tenants.json (legacy)
        4. Global config defaults
        """
        tenant = self.get(tenant_id)
        tenant_config = self.get_tenant_config(tenant_id)
        
        base: dict[str, Any] = {}
        
        # Start with global defaults
        if global_config:
            defaults = global_config.agents.defaults if hasattr(global_config, "agents") else None
            if defaults:
                base = {
                    "model": defaults.model,
                    "temperature": defaults.temperature,
                    "max_tokens": defaults.max_tokens,
                    "max_tool_iterations": defaults.max_tool_iterations,
                    "memory_window": defaults.memory_window,
                }
            if hasattr(global_config.tools, "mcp_servers"):
                base["mcp_servers"] = global_config.tools.mcp_servers
            if hasattr(global_config.tools.web, "search"):
                base["brave_api_key"] = global_config.tools.web.search.api_key
            base["exec_config"] = global_config.tools.exec
            base["restrict_to_workspace"] = global_config.tools.restrict_to_workspace
        
        # Apply tenant config_override from tenants.json (legacy)
        if tenant and tenant.config_override:
            base.update(tenant.config_override)
        
        # Apply tenant config.json settings (highest priority for tenant level)
        if tenant_config.agent:
            base.update(tenant_config.agent)
        if tenant_config.mcp_servers:
            base["mcp_servers"] = tenant_config.mcp_servers
        if tenant_config.providers:
            base["providers"] = tenant_config.providers

        # Apply user config.json settings (highest priority overall)
        if user_id:
            user_config = self.get_user_config(tenant_id, user_id)
            if user_config.agent:
                base.update(user_config.agent)
            if user_config.channels:
                base["channels"] = user_config.channels

        # Detect whether model was actually configured (not just Pydantic default).
        # If neither system config, tenant config, nor user config set a model,
        # ``global_config.agents.defaults.model`` returns the schema default
        # ``"anthropic/claude-opus-4-5"`` which almost certainly has no API key.
        _SCHEMA_DEFAULT_MODEL = "anthropic/claude-opus-4-5"
        model = base.get("model")
        if model == _SCHEMA_DEFAULT_MODEL:
            explicitly_set = False
            if tenant_config.agent and "model" in tenant_config.agent:
                explicitly_set = True
            if user_id:
                uc = self.get_user_config(tenant_id, user_id)
                if uc.agent and "model" in uc.agent:
                    explicitly_set = True
            if not explicitly_set:
                base.pop("model", None)

        return base

    # ── persistence ──

    def _load(self) -> dict[str, Tenant]:
        if self._cache is not None:
            return self._cache
        if not self._file.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            self._cache = {tid: Tenant.from_dict(d) for tid, d in raw.items()}
        except Exception:
            logger.exception("Failed to load tenants from {}", self._file)
            self._cache = {}
        return self._cache

    def _save(self, tenants: dict[str, Tenant]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {tid: t.to_dict() for tid, t in tenants.items()}
        self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache = tenants
        logger.info("Saved {} tenants to {}", len(tenants), self._file)
