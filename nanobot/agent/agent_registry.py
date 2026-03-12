"""Agent registry: discovery, registration and summary of sub-agent definitions."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from loguru import logger

from nanobot.agent.agent_def import (
    AgentConfig,
    AgentDefinition,
    _ConfigAgentDefinition,
)


class AgentRegistry:
    """Discovers, registers, and manages sub-agent definitions.

    Discovery order (higher priority wins):
      1. ``{workspace}/agents/{name}/AGENT.json`` or ``AGENT.yaml`` (tenant-level)
      2. Paths from config ``agents.agents_dirs``    (explicit / dev)
      3. Global agents dir via :meth:`add_extra_dir` (system-level, e.g. ``~/.nanobots/agents/``)

    AGENT.json takes priority over AGENT.yaml if both exist (JSON supports nested
    structures like mcp_servers that YAML cannot express).

    If an ``agent.py`` file exists alongside the config file, it is dynamically
    imported and its :class:`AgentDefinition` subclass is used.  Otherwise a
    :class:`_ConfigAgentDefinition` wrapper is created from the config alone.
    """

    AGENT_YAML = "AGENT.yaml"
    AGENT_JSON = "AGENT.json"

    def __init__(self, workspace: Path, extra_dirs: list[Path] | None = None) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._workspace = workspace
        self._extra_dirs = extra_dirs or []
        logger.debug("AgentRegistry: workspace={}, extra_dirs={}", workspace, self._extra_dirs)
        self._discover(workspace)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> AgentDefinition | None:
        """Return the agent definition for *name*, or ``None``."""
        return self._agents.get(name)

    def has(self, name: str) -> bool:
        return name in self._agents

    def list_agents(self) -> list[AgentConfig]:
        """Return configs for all registered agents."""
        return [a.get_config() for a in self._agents.values()]

    def list_names(self) -> list[str]:
        """Return sorted list of registered agent names."""
        return sorted(self._agents.keys())

    def register(self, agent_def: AgentDefinition) -> None:
        """Manually register an agent definition (e.g. from code)."""
        name = agent_def.get_config().name
        self._agents[name] = agent_def
        logger.info("Agent '{}' registered (manual)", name)

    def add_extra_dir(self, path: Path) -> None:
        """Scan an additional directory for agent definitions (e.g. global agents dir).

        Already-registered agent names are not overwritten.
        """
        seen = set(self._agents.keys())
        self._scan_dir(path, "global", seen)
        if self._agents:
            logger.debug("AgentRegistry now has {} agent(s) after adding {}", len(self._agents), path)

    def build_agents_summary(self, filter_fn=None) -> str:
        """Build an XML summary of registered agents for the system prompt.

        Args:
            filter_fn: Optional callable ``(AgentDefinition) -> bool`` to filter
                       which agents appear in the summary.
        """
        if not self._agents:
            return ""

        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<agents>"]
        for name in sorted(self._agents):
            ad = self._agents[name]
            if filter_fn and not filter_fn(ad):
                continue
            cfg = ad.get_config()
            lines.append(f'  <agent name="{_esc(name)}">')
            lines.append(f"    <description>{_esc(cfg.description)}</description>")
            lines.append(f"    <mode>{cfg.mode}</mode>")
            if cfg.triggers:
                triggers_str = ", ".join(cfg.triggers)
                lines.append(f"    <triggers>{_esc(triggers_str)}</triggers>")
            lines.append("  </agent>")
        lines.append("</agents>")
        # Return empty string if only the wrapper tags remain (no agents matched)
        return "\n".join(lines) if len(lines) > 2 else ""

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self, workspace: Path) -> None:
        """Scan workspace and extra dirs for agent definitions."""
        seen: set[str] = set()

        # 1. Workspace agents (highest priority)
        ws_agents = workspace / "agents"
        self._scan_dir(ws_agents, "workspace", seen)

        # 2. Extra directories (from config agents_dirs)
        for extra in self._extra_dirs:
            self._scan_dir(extra, "config", seen)

        if self._agents:
            logger.info(
                "AgentRegistry: {} agent(s) discovered: {}",
                len(self._agents),
                ", ".join(sorted(self._agents)),
            )

    def _scan_dir(self, base: Path, source: str, seen: set[str]) -> None:
        """Scan *base* for sub-directories containing AGENT.json or AGENT.yaml.

        AGENT.json takes priority if both exist.
        """
        if not base.is_dir():
            logger.debug("_scan_dir: {} is not a directory, skipping", base)
            return

        logger.debug("_scan_dir: scanning {} (source={})", base, source)

        try:
            entries = sorted(base.iterdir())
        except OSError:
            return

        for child in entries:
            if not child.is_dir():
                continue
            # Check for AGENT.json first (priority), then AGENT.yaml
            json_path = child / self.AGENT_JSON
            yaml_path = child / self.AGENT_YAML
            config_path = None
            if json_path.exists():
                config_path = json_path
            elif yaml_path.exists():
                config_path = yaml_path
            else:
                continue
            if child.name in seen:
                continue
            seen.add(child.name)

            try:
                agent_def = self._load_agent(child, config_path, source)
                name = agent_def.get_config().name
                self._agents[name] = agent_def
                logger.debug("Agent '{}' loaded from {} ({})", name, child, source)
            except Exception as e:
                logger.warning("Failed to load agent from {}: {}", child, e)

    def _load_agent(
        self, agent_dir: Path, config_path: Path, source: str
    ) -> AgentDefinition:
        """Load a single agent definition from *agent_dir*.

        If ``agent.py`` exists, dynamically import it and look for an
        :class:`AgentDefinition` subclass.  Otherwise wrap the config.
        """
        config = AgentConfig.from_file(config_path)
        config.agent_dir = agent_dir
        logger.debug("_load_agent: loaded config from {} -> name={}, mode={}",
                     config_path, config.name, config.mode)

        agent_py = agent_dir / "agent.py"
        if agent_py.exists():
            mod = self._import_agent_module(agent_dir, agent_py)
            # Find the first AgentDefinition subclass in the module
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name, None)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, AgentDefinition)
                    and obj is not AgentDefinition
                    and not getattr(obj, "__abstractmethods__", None)
                ):
                    return obj()
            logger.warning(
                "agent.py in {} has no AgentDefinition subclass; falling back to config",
                agent_dir,
            )

        return _ConfigAgentDefinition(config)

    # ------------------------------------------------------------------
    # Dynamic import (similar to PluginLoader._import_plugin)
    # ------------------------------------------------------------------

    @staticmethod
    def _import_agent_module(agent_dir: Path, agent_py: Path) -> ModuleType:
        """Dynamically import ``agent.py`` from *agent_dir*."""
        mod_name = f"nanobot_agent_{agent_dir.name}"

        init_py = agent_dir / "__init__.py"
        sub_name = f"{mod_name}.agent"

        # If the agent submodule is already loaded, return it directly
        if init_py.exists() and sub_name in sys.modules:
            return sys.modules[sub_name]

        if mod_name in sys.modules:
            # If __init__.py exists, we need to check for agent.py submodule
            if init_py.exists():
                # The package module is cached, but agent.py might not be
                # We'll continue to load agent.py as a submodule below
                pass
            else:
                return sys.modules[mod_name]

        parent_str = str(agent_dir.parent)
        added = parent_str not in sys.path
        if added:
            sys.path.insert(0, parent_str)

        try:
            if init_py.exists():
                pkg_spec = importlib.util.spec_from_file_location(
                    mod_name,
                    str(init_py),
                    submodule_search_locations=[str(agent_dir)],
                )
            else:
                pkg_spec = importlib.util.spec_from_file_location(
                    mod_name,
                    str(agent_py),
                    submodule_search_locations=[str(agent_dir)],
                )

            if pkg_spec is None or pkg_spec.loader is None:
                raise ImportError(f"Cannot create spec for agent module '{agent_dir.name}'")

            pkg_mod = importlib.util.module_from_spec(pkg_spec)
            sys.modules[mod_name] = pkg_mod
            pkg_mod.__path__ = [str(agent_dir)]  # type: ignore[attr-defined]
            pkg_mod.__package__ = mod_name
            pkg_spec.loader.exec_module(pkg_mod)

            # If __init__.py existed, also import agent.py as submodule
            if init_py.exists():
                if sub_name not in sys.modules:
                    sub_spec = importlib.util.spec_from_file_location(sub_name, str(agent_py))
                    if sub_spec and sub_spec.loader:
                        sub_mod = importlib.util.module_from_spec(sub_spec)
                        sub_mod.__package__ = mod_name
                        sys.modules[sub_name] = sub_mod
                        sub_spec.loader.exec_module(sub_mod)
                # Return the agent submodule (either newly loaded or cached)
                if sub_name in sys.modules:
                    return sys.modules[sub_name]

            return pkg_mod

        except Exception:
            for key in list(sys.modules):
                if key == mod_name or key.startswith(f"{mod_name}."):
                    del sys.modules[key]
            raise
        finally:
            if added and parent_str in sys.path:
                sys.path.remove(parent_str)
