"""Automatic migrations for nanobot data and configuration."""

import json
import shutil
from pathlib import Path

from loguru import logger

OLD_ROOT = Path.home() / ".nanobot"
NEW_ROOT = Path.home() / ".nanobots"
MIGRATION_MARKER = NEW_ROOT / ".migrated_from_nanobot"
LAYERED_MARKER = NEW_ROOT / ".layered_migrated"

# Global directories: copied to ~/.nanobots/<name>
GLOBAL_DIRS = ("bridge", "history")

# Tenant-level directories (shared channel infra): → tenants/default/<name>
TENANT_DIRS = ("mochat", "matrix-store", "whatsapp-auth")

# User-level directories: → tenants/default/users/default/<name>
USER_DIRS = ("media", "cron")

# Fields that belong at system level in config.json
_SYSTEM_KEYS = {"gateway", "providers"}
# Nested keys inside "tools" that are system-level
_SYSTEM_TOOLS_KEYS = {"web", "exec"}
# Fields that belong at tenant level
_TENANT_KEYS = {"agents"}
# Nested keys inside "tools" that are tenant-level
_TENANT_TOOLS_KEYS = {"mcpServers", "mcp_servers", "restrictToWorkspace", "restrict_to_workspace"}
# Fields that belong at user level
_USER_KEYS = {"channels"}


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy directory tree, skipping files that already exist at destination."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


# ---------------------------------------------------------------------------
# Phase 1: ~/.nanobot → ~/.nanobots  (directory structure migration)
# ---------------------------------------------------------------------------

def auto_migrate_if_needed() -> None:
    """Migrate data from ~/.nanobot to ~/.nanobots, then split flat config.

    Runs once per phase and creates marker files to avoid repeated migration.
    The old directory is NOT deleted.
    """
    _migrate_old_root()
    _migrate_flat_to_layered()


def _migrate_old_root() -> None:
    """Phase 1: Copy ~/.nanobot → ~/.nanobots."""
    if MIGRATION_MARKER.exists():
        return
    if not OLD_ROOT.is_dir():
        NEW_ROOT.mkdir(parents=True, exist_ok=True)
        MIGRATION_MARKER.write_text("no old directory found", encoding="utf-8")
        return

    logger.info("Migrating data from {} to {} ...", OLD_ROOT, NEW_ROOT)
    NEW_ROOT.mkdir(parents=True, exist_ok=True)

    tenant_default = NEW_ROOT / "tenants" / "default"
    user_default = tenant_default / "users" / "default"
    tenant_default.mkdir(parents=True, exist_ok=True)
    user_default.mkdir(parents=True, exist_ok=True)

    # --- Global config.json ---
    old_config = OLD_ROOT / "config.json"
    new_config = NEW_ROOT / "config.json"
    if old_config.is_file() and not new_config.exists():
        shutil.copy2(old_config, new_config)
        logger.info("  Copied config.json")

    # --- Global directories ---
    for name in GLOBAL_DIRS:
        src = OLD_ROOT / name
        if src.is_dir():
            _copy_tree(src, NEW_ROOT / name)
            logger.info("  Copied global dir: {}", name)

    # --- Tenant-level directories ---
    for name in TENANT_DIRS:
        src = OLD_ROOT / name
        if src.is_dir():
            _copy_tree(src, tenant_default / name)
            logger.info("  Copied tenant dir: {}", name)

    # --- User-level directories ---
    for name in USER_DIRS:
        src = OLD_ROOT / name
        if src.is_dir():
            _copy_tree(src, user_default / name)
            logger.info("  Copied user dir: {}", name)

    # --- Workspace content (old workspace → tenants/default) ---
    old_workspace = OLD_ROOT / "workspace"
    if old_workspace.is_dir():
        _copy_tree(old_workspace, tenant_default)
        logger.info("  Copied workspace content to tenants/default")

    MIGRATION_MARKER.write_text(
        f"migrated from {OLD_ROOT}", encoding="utf-8"
    )
    logger.info("Migration complete. Old directory kept at {}", OLD_ROOT)


# ---------------------------------------------------------------------------
# Phase 2: flat config.json → System / Tenant / User layers
# ---------------------------------------------------------------------------

def _migrate_flat_to_layered() -> None:
    """Split a flat ~/.nanobots/config.json into three-level config files.

    Detection: if the system config.json contains a ``channels`` key it is
    still in the old flat layout.

    Destination files:
    - ``~/.nanobots/config.json``                            → system only
    - ``~/.nanobots/tenants/default/config.json``            → tenant fields
    - ``~/.nanobots/tenants/default/users/default/config.json`` → user fields
    """
    if LAYERED_MARKER.exists():
        return

    sys_path = NEW_ROOT / "config.json"
    if not sys_path.is_file():
        # Nothing to split.
        NEW_ROOT.mkdir(parents=True, exist_ok=True)
        LAYERED_MARKER.write_text("no flat config found", encoding="utf-8")
        return

    try:
        with open(sys_path, encoding="utf-8") as f:
            flat = json.load(f)
    except (json.JSONDecodeError, ValueError):
        LAYERED_MARKER.write_text("invalid json", encoding="utf-8")
        return

    if "channels" not in flat and "agents" not in flat:
        # Already layered or minimal config.
        LAYERED_MARKER.write_text("already layered", encoding="utf-8")
        return

    logger.info("Splitting flat config.json into system / tenant / user layers ...")

    system_cfg: dict = {}
    tenant_cfg: dict = {}
    user_cfg: dict = {}

    for key, val in flat.items():
        if key in _SYSTEM_KEYS:
            system_cfg[key] = val
        elif key in _TENANT_KEYS:
            tenant_cfg[key] = val
        elif key in _USER_KEYS:
            user_cfg[key] = val
        elif key == "tools":
            # Split tools between system and tenant
            sys_tools: dict = {}
            ten_tools: dict = {}
            for tk, tv in val.items():
                if tk in _SYSTEM_TOOLS_KEYS:
                    sys_tools[tk] = tv
                elif tk in _TENANT_TOOLS_KEYS:
                    ten_tools[tk] = tv
                else:
                    # Unknown tool key → keep at system level
                    sys_tools[tk] = tv
            if sys_tools:
                system_cfg["tools"] = sys_tools
            if ten_tools:
                tenant_cfg["tools"] = ten_tools
        else:
            # Unknown top-level key → keep at system level
            system_cfg[key] = val

    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # Write system config (overwrite the flat one)
    _write_json(sys_path, system_cfg)
    logger.info("  System config: {}", sys_path)

    # Write tenant config (only if there's content)
    if tenant_cfg:
        tenant_path = NEW_ROOT / "tenants" / "default" / "config.json"
        # Merge with existing tenant config if present
        if tenant_path.is_file():
            try:
                with open(tenant_path, encoding="utf-8") as f:
                    existing = json.load(f)
                from nanobot.config.loader import deep_merge
                tenant_cfg = deep_merge(tenant_cfg, existing)
            except (json.JSONDecodeError, ValueError):
                pass
        _write_json(tenant_path, tenant_cfg)
        logger.info("  Tenant config: {}", tenant_path)

    # Write user config (only if there's content)
    if user_cfg:
        user_path = NEW_ROOT / "tenants" / "default" / "users" / "default" / "config.json"
        if user_path.is_file():
            try:
                with open(user_path, encoding="utf-8") as f:
                    existing = json.load(f)
                from nanobot.config.loader import deep_merge
                user_cfg = deep_merge(user_cfg, existing)
            except (json.JSONDecodeError, ValueError):
                pass
        _write_json(user_path, user_cfg)
        logger.info("  User config: {}", user_path)

    # --- Migrate directories from tenant to user level ---
    tenant_default = NEW_ROOT / "tenants" / "default"
    user_default = tenant_default / "users" / "default"
    for name in USER_DIRS:
        src = tenant_default / name
        dst = user_default / name
        if src.is_dir() and not dst.exists():
            _copy_tree(src, dst)
            logger.info("  Moved dir to user level: {}", name)

    LAYERED_MARKER.write_text("migrated", encoding="utf-8")
    logger.info("Layered config migration complete")
