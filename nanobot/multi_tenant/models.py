"""Data models for multi-tenant isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TenantConfig:
    """Tenant-specific configuration loaded from config.json.
    
    Schema::
    
        {
            "providers": {
                "default": "openai",           // default provider name
                "openai": {
                    "api_key": "sk-...",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o"
                },
                "azure": {
                    "api_key": "...",
                    "base_url": "https://xxx.openai.azure.com",
                    "model": "gpt-4o",
                    "api_version": "2024-02-01"
                }
            },
            "mcp_servers": {
                "memory": {
                    "command": "npx",
                    "args": ["-y", "@anthropics/mcp-memory"]
                }
            },
            "agent": {
                "model": "gpt-4o",
                "temperature": 0.7,
                "max_tokens": 4096,
                "max_tool_iterations": 20,
                "memory_window": 20
            }
        }
    """
    
    providers: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": self.providers,
            "mcp_servers": self.mcp_servers,
            "agent": self.agent,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TenantConfig:
        # --- agent ---
        # Native format: {"agent": {"model": "gpt-4o", "max_tokens": 4096}}
        # nanobot-fork format: {"agents": {"defaults": {"model": "gpt-4o", "maxTokens": 4096}}}
        agent = data.get("agent", {})
        if not agent and "agents" in data:
            agents_block = data["agents"]
            if isinstance(agents_block, dict):
                defaults = agents_block.get("defaults", agents_block)
                _CAMEL_MAP = {
                    "maxTokens": "max_tokens",
                    "maxToolIterations": "max_tool_iterations",
                    "memoryWindow": "memory_window",
                }
                agent = {}
                for k, v in defaults.items():
                    if k == "workspace":
                        continue
                    agent[_CAMEL_MAP.get(k, k)] = v

        # --- mcp_servers ---
        # Native format: {"mcp_servers": {...}}
        # nanobot-fork format: {"tools": {"mcpServers": {...}}}
        mcp_servers = data.get("mcp_servers", {})
        if not mcp_servers and "tools" in data:
            tools = data["tools"]
            if isinstance(tools, dict):
                mcp_servers = tools.get("mcpServers", tools.get("mcp_servers", {}))
                # Also extract restrictToWorkspace into agent dict
                for key, mapped in (("restrictToWorkspace", "restrict_to_workspace"),):
                    if key in tools and mapped not in agent:
                        agent[mapped] = tools[key]

        # --- providers ---
        providers = data.get("providers", {})

        return cls(
            providers=providers,
            mcp_servers=mcp_servers,
            agent=agent,
        )
    
    def get_provider_config(self, provider_name: str | None = None) -> dict[str, Any]:
        """Get configuration for a specific provider, or the default."""
        if not provider_name:
            provider_name = self.providers.get("default", "openai")
        return self.providers.get(provider_name, {})
    
    def get_default_provider(self) -> str:
        """Get the default provider name."""
        return self.providers.get("default", "openai")


@dataclass
class UserConfig:
    """User-specific configuration loaded from users/{uid}/config.json.

    Schema::

        {
            "channels": {
                "telegram": { "enabled": true, "token": "123:ABC", "allowFrom": ["alice"] },
                "whatsapp": { "enabled": false }
            },
            "agent": {
                "model": "claude-3-5-sonnet-20241022",
                "temperature": 0.1
            }
        }
    """

    channels: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": self.channels,
            "agent": self.agent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserConfig:
        return cls(
            channels=data.get("channels", {}),
            agent=data.get("agent", {}),
        )


@dataclass
class Tenant:
    """A tenant / organisation."""

    tenant_id: str  # slug, e.g. "acme-corp"
    name: str
    created_at: str = ""  # ISO datetime
    config_override: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "created_at": self.created_at,
            "config_override": self.config_override,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tenant:
        return cls(
            tenant_id=data["tenant_id"],
            name=data.get("name", data["tenant_id"]),
            created_at=data.get("created_at", ""),
            config_override=data.get("config_override", {}),
            enabled=data.get("enabled", True),
        )


@dataclass
class User:
    """A user belonging to a tenant."""

    user_id: str  # UUID
    username: str  # unique within tenant
    password_hash: str  # bcrypt
    tenant_id: str
    role: str = "user"  # "admin" | "user"
    created_at: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "password_hash": self.password_hash,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "created_at": self.created_at,
            "enabled": self.enabled,
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Return dict without password_hash."""
        d = self.to_dict()
        d.pop("password_hash", None)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            password_hash=data.get("password_hash", ""),
            tenant_id=data["tenant_id"],
            role=data.get("role", "user"),
            created_at=data.get("created_at", ""),
            enabled=data.get("enabled", True),
        )
