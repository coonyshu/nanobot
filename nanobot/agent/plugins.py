"""Plugin loader for skill directories containing Python tools and routes.

A skill directory becomes a *plugin* when it contains a ``tools.py`` file.
The loader discovers plugins across multiple directories (tenant → system)
with the same priority semantics as :class:`SkillsLoader`.

Convention for ``tools.py``
---------------------------
Expose **at least one** of:

* ``register_tools(registry: ToolRegistry) -> None``
* ``register_routes(app: FastAPI) -> None``

If ``register_tools`` is absent the loader auto-discovers all
:class:`~nanobot.agent.tools.base.Tool` subclasses in the module.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from loguru import logger


@dataclass
class PluginInfo:
    """Metadata for a discovered plugin."""

    name: str  # directory name = plugin name
    path: Path  # directory containing tools.py
    source: str  # "tenant" | "system"
    module: ModuleType | None = field(default=None, repr=False)


class PluginLoader:
    """Discover and load plugins from skill directories.

    Parameters
    ----------
    dirs : list[Path]
        Directories to scan, **ordered by priority** (highest first).
        Typically ``[tenant_skills_dir, system_skills_dir]``.
    """

    def __init__(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._plugins: list[PluginInfo] | None = None  # lazy

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[PluginInfo]:
        """Scan directories and return de-duplicated plugin list.

        Same-name plugins in a higher-priority directory shadow those in
        lower-priority directories (consistent with SkillsLoader).
        """
        if self._plugins is not None:
            return self._plugins

        seen: set[str] = set()
        plugins: list[PluginInfo] = []

        for idx, base_dir in enumerate(self._dirs):
            if not base_dir.is_dir():
                continue
            source = "tenant" if idx == 0 and len(self._dirs) > 1 else "system"
            try:
                entries = sorted(base_dir.iterdir())
            except OSError:
                continue
            for child in entries:
                if not child.is_dir():
                    continue
                tools_py = child / "tools.py"
                if tools_py.exists() and child.name not in seen:
                    seen.add(child.name)
                    plugins.append(PluginInfo(name=child.name, path=child, source=source))

        self._plugins = plugins
        return plugins

    # ------------------------------------------------------------------
    # Tool loading
    # ------------------------------------------------------------------

    def load_tools(self, registry: Any) -> list[str]:
        """Import each plugin and register its tools into *registry*.

        Returns the names of successfully loaded plugins.
        """
        loaded: list[str] = []
        for info in self.discover():
            try:
                mod = self._import_plugin(info)
                if hasattr(mod, "register_tools"):
                    mod.register_tools(registry)
                else:
                    self._auto_register_tools(mod, registry)
                loaded.append(info.name)
                logger.info("Plugin '{}' tools loaded (source={})", info.name, info.source)
            except Exception as e:
                logger.warning("Failed to load plugin '{}' tools: {}", info.name, e)
        return loaded

    # ------------------------------------------------------------------
    # Route loading
    # ------------------------------------------------------------------

    def load_routes(self, app: Any) -> list[str]:
        """Call ``register_routes(app)`` for each plugin that exposes it.

        Returns the names of plugins that registered routes.
        """
        loaded: list[str] = []
        for info in self.discover():
            try:
                mod = self._import_plugin(info)
                if hasattr(mod, "register_routes"):
                    mod.register_routes(app)
                    loaded.append(info.name)
                    logger.info("Plugin '{}' routes loaded", info.name)
            except Exception as e:
                logger.warning("Failed to load plugin '{}' routes: {}", info.name, e)
        return loaded

    # ------------------------------------------------------------------
    # Dynamic import
    # ------------------------------------------------------------------

    def _import_plugin(self, info: PluginInfo) -> ModuleType:
        """Dynamically import a plugin's ``tools.py`` as a package module.

        The plugin directory is treated as a Python package so that
        internal relative imports (``from .state_machine import ...``)
        work correctly.
        """
        if info.module is not None:
            return info.module

        pkg_name = f"nanobot_plugin_{info.name}"

        # If already loaded in a previous call, reuse
        if pkg_name in sys.modules:
            info.module = sys.modules[pkg_name]
            return info.module

        plugin_dir = info.path
        init_py = plugin_dir / "__init__.py"
        tools_py = plugin_dir / "tools.py"

        # Ensure the plugin's *parent* is on sys.path so that the
        # package can be resolved.  We add temporarily and clean up if
        # it was not already present.
        parent_str = str(plugin_dir.parent)
        added_to_path = False
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)
            added_to_path = True

        try:
            # Step 1 – create the package (from __init__.py or synthetic)
            if init_py.exists():
                pkg_spec = importlib.util.spec_from_file_location(
                    pkg_name, str(init_py),
                    submodule_search_locations=[str(plugin_dir)],
                )
            else:
                # Synthetic package – no __init__.py required
                pkg_spec = importlib.util.spec_from_file_location(
                    pkg_name, str(tools_py),
                    submodule_search_locations=[str(plugin_dir)],
                )

            if pkg_spec is None or pkg_spec.loader is None:
                raise ImportError(f"Cannot create spec for plugin '{info.name}'")

            pkg_mod = importlib.util.module_from_spec(pkg_spec)
            sys.modules[pkg_name] = pkg_mod
            pkg_mod.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
            pkg_mod.__package__ = pkg_name
            pkg_spec.loader.exec_module(pkg_mod)

            # Step 2 – if __init__.py existed, also import tools.py as a submodule
            if init_py.exists():
                tools_mod_name = f"{pkg_name}.tools"
                if tools_mod_name not in sys.modules:
                    tools_spec = importlib.util.spec_from_file_location(
                        tools_mod_name, str(tools_py),
                    )
                    if tools_spec and tools_spec.loader:
                        tools_mod = importlib.util.module_from_spec(tools_spec)
                        tools_mod.__package__ = pkg_name
                        sys.modules[tools_mod_name] = tools_mod
                        tools_spec.loader.exec_module(tools_mod)
                        info.module = tools_mod
                        return tools_mod

            # When there is no __init__.py, the package IS tools.py
            info.module = pkg_mod
            return pkg_mod

        except Exception:
            # Clean up partial registrations on failure
            for key in list(sys.modules):
                if key == pkg_name or key.startswith(f"{pkg_name}."):
                    del sys.modules[key]
            raise
        finally:
            if added_to_path and parent_str in sys.path:
                sys.path.remove(parent_str)

    # ------------------------------------------------------------------
    # Auto-discovery fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_register_tools(mod: ModuleType, registry: Any) -> None:
        """Register all :class:`Tool` subclasses found in *mod*."""
        from nanobot.agent.tools.base import Tool

        for attr_name in dir(mod):
            obj = getattr(mod, attr_name, None)
            if (
                isinstance(obj, type)
                and issubclass(obj, Tool)
                and obj is not Tool
                and not getattr(obj, "__abstractmethods__", None)
            ):
                try:
                    registry.register(obj())
                except Exception as e:
                    logger.warning("Auto-register tool '{}' failed: {}", attr_name, e)
