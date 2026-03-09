"""One-time data migration from legacy single-user layout to multi-tenant layout."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import bcrypt
from loguru import logger

from nanobot.multi_tenant.models import Tenant, User
from nanobot.multi_tenant.tenant_store import TenantStore
from nanobot.multi_tenant.user_store import UserStore
from nanobot.multi_tenant.workspace_resolver import WorkspaceResolver


MIGRATION_MARKER = ".migrated"
TENANTS_DIR_MARKER = ".tenants_dir_migrated"
SERVICE_DATA_MARKER = ".service_data_migrated"


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


def needs_service_data_migration(new_data_dir: Path, old_data_dir: Path) -> bool:
    """Check if old nanobot-service/data/ needs to be migrated to ~/.nanobots/."""
    if (new_data_dir / SERVICE_DATA_MARKER).exists():
        return False
    return old_data_dir.is_dir() and old_data_dir != new_data_dir


def run_service_data_migration(new_data_dir: Path, old_data_dir: Path) -> None:
    """Migrate nanobot-service/data/ contents to ~/.nanobots/.

    Copies tenants.json, users.json, tenants/ directory, and marker files.
    The old directory is NOT deleted.
    """
    logger.info("Migrating service data from {} to {}", old_data_dir, new_data_dir)
    new_data_dir.mkdir(parents=True, exist_ok=True)

    # Copy JSON registry files
    for name in ("tenants.json", "users.json"):
        src = old_data_dir / name
        dst = new_data_dir / name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            logger.info("  Copied {}", name)

    # Copy tenants directory
    old_tenants = old_data_dir / "tenants"
    if old_tenants.is_dir():
        _copy_tree(old_tenants, new_data_dir / "tenants")
        logger.info("  Copied tenants/ directory")

    # Carry over existing migration markers so we don't re-run old migrations
    for marker in (MIGRATION_MARKER, TENANTS_DIR_MARKER):
        src = old_data_dir / marker
        dst = new_data_dir / marker
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)

    (new_data_dir / SERVICE_DATA_MARKER).write_text(
        f"migrated from {old_data_dir}\n", encoding="utf-8"
    )
    logger.info("Service data migration complete")


def needs_migration(data_dir: Path) -> bool:
    return not (data_dir / MIGRATION_MARKER).exists()


def needs_tenants_dir_migration(data_dir: Path) -> bool:
    """Check if we need to migrate from data/{tenant} to data/tenants/{tenant}."""
    # Migration needed if:
    # 1. No marker file exists
    # 2. There are tenant directories directly under data/ (not in tenants/)
    if (data_dir / TENANTS_DIR_MARKER).exists():
        return False
    
    tenants_file = data_dir / "tenants.json"
    if not tenants_file.exists():
        return False
    
    # Check if any tenant directory exists directly under data/
    import json
    try:
        tenants = json.loads(tenants_file.read_text(encoding="utf-8"))
        for tenant_id in tenants.keys():
            old_path = data_dir / tenant_id
            new_path = data_dir / "tenants" / tenant_id
            if old_path.exists() and old_path.is_dir() and not new_path.exists():
                return True
    except Exception:
        pass
    
    return False


def run_migration(
    data_dir: Path,
    service_dir: Path,
    resolver: WorkspaceResolver,
    tenant_store: TenantStore,
    user_store: UserStore,
) -> None:
    """Migrate legacy single-user data into the multi-tenant directory layout.

    Steps
    -----
    1. Create ``data/`` and the *default* tenant.
    2. Migrate the hard-coded admin user into ``data/users.json``.
    3. Copy existing ``sessions/*.jsonl`` into the admin user's directory.
    4. Copy existing ``memory/MEMORY.md`` + ``HISTORY.md`` into the admin user's directory.
    5. Write a ``.migrated`` marker so the migration is not repeated.
    """
    logger.info("Starting data migration into {}", data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 – default tenant
    if not tenant_store.get("default"):
        tenant_store.create(Tenant(
            tenant_id="default",
            name="Default",
            config_override={},
        ))
        logger.info("Created default tenant")

    resolver.ensure_tenant_dirs("default")

    # Step 2 – admin user
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    existing_users = user_store.list_by_tenant("default")
    admin_exists = any(u.username == "admin" for u in existing_users)
    if not admin_exists:
        admin_user = User(
            user_id="admin",
            username="admin",
            password_hash=bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode(),
            tenant_id="default",
            role="admin",
        )
        # Write directly (bypass async lock – we're in sync startup)
        users = user_store._load()
        users[admin_user.user_id] = admin_user
        user_store._save(users)
        logger.info("Migrated admin user")

    resolver.ensure_user_dirs("default", "admin")
    admin_ws = resolver.user_workspace("default", "admin")

    # Step 3 – sessions
    legacy_sessions = service_dir / "sessions"
    if legacy_sessions.exists():
        target_sessions = admin_ws / "sessions"
        target_sessions.mkdir(parents=True, exist_ok=True)
        for f in legacy_sessions.glob("*.jsonl"):
            dest = target_sessions / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                logger.info("Migrated session file: {}", f.name)

    # Step 4 – memory
    legacy_memory = service_dir / "memory"
    if legacy_memory.exists():
        target_memory = admin_ws / "memory"
        target_memory.mkdir(parents=True, exist_ok=True)
        for name in ("MEMORY.md", "HISTORY.md"):
            src = legacy_memory / name
            dest = target_memory / name
            if src.exists() and not dest.exists():
                shutil.copy2(src, dest)
                logger.info("Migrated memory file: {}", name)

    # Step 5 – marker
    (data_dir / MIGRATION_MARKER).write_text("migrated\n")
    logger.info("Migration complete")


def run_tenants_dir_migration(data_dir: Path, resolver: WorkspaceResolver) -> None:
    """Migrate from data/{tenant} to data/tenants/{tenant} structure.
    
    This moves existing tenant directories under the new tenants/ subdirectory.
    """
    import json
    
    logger.info("Starting tenants directory migration")
    
    tenants_file = data_dir / "tenants.json"
    if not tenants_file.exists():
        logger.warning("No tenants.json found, skipping migration")
        return
    
    # Ensure tenants directory exists
    tenants_dir = data_dir / "tenants"
    tenants_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        tenants = json.loads(tenants_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read tenants.json: {}", e)
        return
    
    for tenant_id in tenants.keys():
        old_path = data_dir / tenant_id
        new_path = tenants_dir / tenant_id
        
        if not old_path.exists() or not old_path.is_dir():
            continue
        
        if new_path.exists():
            logger.info("Tenant '{}' already exists in tenants/, skipping", tenant_id)
            continue
        
        # Move the tenant directory
        logger.info("Moving tenant '{}': {} -> {}", tenant_id, old_path, new_path)
        try:
            shutil.move(str(old_path), str(new_path))
            logger.info("Moved tenant '{}'", tenant_id)
        except Exception as e:
            logger.error("Failed to move tenant '{}': {}", tenant_id, e)
            continue
        
        # Clean up incorrect tenant-level memory/sessions directories (should only be at user level)
        for bad_dir in ["memory", "sessions"]:
            bad_path = new_path / bad_dir
            if bad_path.exists() and bad_path.is_dir():
                # Check if it's empty or only contains empty files
                contents = list(bad_path.iterdir())
                if not contents:
                    logger.info("Removing empty {} directory at tenant level", bad_dir)
                    bad_path.rmdir()
                else:
                    logger.warning("Tenant-level {} directory is not empty, leaving as-is", bad_dir)
        
        # Create skills directory if it doesn't exist
        skills_dir = new_path / "skills"
        if not skills_dir.exists():
            skills_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created skills directory for tenant '{}'", tenant_id)
    
    # Write marker
    (data_dir / TENANTS_DIR_MARKER).write_text("migrated\n")
    logger.info("Tenants directory migration complete")
