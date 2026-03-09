"""Configuration loading utilities with three-level hierarchy: System → Tenant → User."""

import json
from pathlib import Path
from typing import Any

from nanobot.config.schema import Config


# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

def deep_merge(base: dict, override: dict) -> dict:
    """Recursively deep-merge *override* into *base* (returns new dict).

    Rules:
    - dict values are merged recursively.
    - Scalar / list values in *override* replace those in *base*.
    - Keys present only in *base* are preserved.
    """
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _nanobots_root() -> Path:
    return Path.home() / ".nanobots"


def get_system_config_path() -> Path:
    """System-level config: ``~/.nanobots/config.json``."""
    return _nanobots_root() / "config.json"


# Keep old name as alias for backward compatibility.
get_config_path = get_system_config_path


def get_tenant_config_path(tenant_id: str) -> Path:
    """Tenant-level config: ``~/.nanobots/tenants/{tenant_id}/config.json``."""
    return _nanobots_root() / "tenants" / tenant_id / "config.json"


def get_user_config_path(tenant_id: str, user_id: str) -> Path:
    """User-level config: ``~/.nanobots/tenants/{tid}/users/{uid}/config.json``."""
    return _nanobots_root() / "tenants" / tenant_id / "users" / user_id / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


# ---------------------------------------------------------------------------
# Layer loaders (return raw dicts)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    """Load a JSON file; return empty dict when missing or invalid."""
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def load_system_config_dict() -> dict:
    """Load system config as dict, applying legacy migrations."""
    data = _load_json(get_system_config_path())
    return _migrate_config(data)


def load_tenant_config_dict(tenant_id: str) -> dict:
    """Load tenant-level config as dict."""
    return _load_json(get_tenant_config_path(tenant_id))


def load_user_config_dict(tenant_id: str, user_id: str) -> dict:
    """Load user-level config as dict."""
    return _load_json(get_user_config_path(tenant_id, user_id))


# ---------------------------------------------------------------------------
# Merged config loader
# ---------------------------------------------------------------------------

def load_merged_config(
    tenant_id: str = "default",
    user_id: str = "default",
) -> Config:
    """Load and merge System → Tenant → User configs into a single Config.

    Merge priority (highest wins): User > Tenant > System > Pydantic defaults.
    """
    from nanobot.migration import auto_migrate_if_needed
    auto_migrate_if_needed()

    system = load_system_config_dict()
    tenant = load_tenant_config_dict(tenant_id)
    user = load_user_config_dict(tenant_id, user_id)

    merged = deep_merge(deep_merge(system, tenant), user)

    try:
        return Config.model_validate(merged)
    except ValueError as e:
        print(f"Warning: Failed to validate merged config: {e}")
        print("Using default configuration.")
        return Config()


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration (backward-compatible entry point).

    When *config_path* is given it is loaded directly (legacy behaviour).
    Otherwise delegates to the three-level merge with default tenant/user.
    """
    if config_path is not None:
        from nanobot.migration import auto_migrate_if_needed
        auto_migrate_if_needed()

        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                data = _migrate_config(data)
                return Config.model_validate(data)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to load config from {config_path}: {e}")
                print("Using default configuration.")
        return Config()

    return load_merged_config("default", "default")


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_config(config: Config, config_path: Path | None = None) -> None:
    """Save full config to a single file (legacy helper)."""
    path = config_path or get_system_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_system_config(data: dict) -> None:
    """Write system-level config dict to ``~/.nanobots/config.json``."""
    path = get_system_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_tenant_config_file(tenant_id: str, data: dict) -> None:
    """Write tenant-level config dict."""
    path = get_tenant_config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_user_config(tenant_id: str, user_id: str, data: dict) -> None:
    """Write user-level config dict."""
    path = get_user_config_path(tenant_id, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Migrate old workspace path from ~/.nanobot/workspace to new location
    agents = data.get("agents", {})
    defaults = agents.get("defaults", {})
    ws = defaults.get("workspace", "")
    if ws in ("~/.nanobot/workspace", "~/.nanobot\\workspace"):
        defaults["workspace"] = "~/.nanobots/tenants/default"

    return data
