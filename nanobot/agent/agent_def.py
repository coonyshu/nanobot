"""Agent definition: data models and abstract base class for sub-agents."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ModelOverride:
    """Optional per-agent LLM configuration overrides."""

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass
class AgentConfig:
    """Declarative configuration for a sub-agent."""

    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    tools_include_pattern: str | None = None
    skills: list[str] = field(default_factory=list)
    model_config: ModelOverride | None = None
    max_iterations: int = 15
    mode: str = "sync"  # "sync" | "background" | "integrated" | "persistent"
    mcp_servers: dict[str, Any] = field(default_factory=dict)  # Agent-scoped MCP servers
    triggers: list[str] = field(default_factory=list)  # Keywords/patterns that should trigger this agent
    agent_dir: Path | None = None  # Directory containing AGENT.json/yaml and SKILL.md

    @classmethod
    def from_yaml_text(cls, text: str) -> AgentConfig:
        """Parse an AGENT.yaml file content into an AgentConfig.

        Uses simple line-based parsing (no PyYAML dependency) similar to
        ``SkillsLoader.get_skill_metadata``.  Supports scalar values and
        simple lists (``- item`` style).
        """
        raw: dict[str, Any] = {}
        current_key: str | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # List item continuation
            if stripped.startswith("- ") and current_key is not None:
                raw.setdefault(current_key, [])
                if isinstance(raw[current_key], list):
                    raw[current_key].append(stripped[2:].strip().strip("\"'"))
                continue

            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip().strip("\"'")
                current_key = key
                if value:
                    raw[key] = value
                else:
                    # Could be a list or multi-line — wait for next lines
                    raw[key] = []
            else:
                current_key = None

        # Build model_config if nested keys present
        mc = None
        mc_raw = raw.get("model_config")
        if isinstance(mc_raw, dict):
            mc = ModelOverride(
                model=mc_raw.get("model"),
                temperature=_to_float(mc_raw.get("temperature")),
                max_tokens=_to_int(mc_raw.get("max_tokens")),
                reasoning_effort=mc_raw.get("reasoning_effort"),
            )

        return cls(
            name=str(raw.get("name", "")),
            description=str(raw.get("description", "")),
            system_prompt=str(raw.get("system_prompt", "")),
            tools=_ensure_list(raw.get("tools", [])),
            tools_include_pattern=raw.get("tools_include_pattern") or None,
            skills=_ensure_list(raw.get("skills", [])),
            model_config=mc,
            max_iterations=_to_int(raw.get("max_iterations")) or 15,
            mode=str(raw.get("mode", "sync")),
            mcp_servers={},  # YAML parser cannot handle nested mcp_servers; use JSON instead
            triggers=_ensure_list(raw.get("triggers", [])),
        )

    @classmethod
    def from_yaml_file(cls, path: Path) -> AgentConfig:
        """Load an AgentConfig from an AGENT.yaml file."""
        return cls.from_yaml_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_json_text(cls, text: str) -> AgentConfig:
        """Parse an AGENT.json file content into an AgentConfig.

        JSON format supports nested structures like mcp_servers that
        the simple YAML parser cannot handle.
        """
        raw = json.loads(text)

        # Build model_config if present
        mc = None
        mc_raw = raw.get("model_config")
        if isinstance(mc_raw, dict):
            mc = ModelOverride(
                model=mc_raw.get("model"),
                temperature=_to_float(mc_raw.get("temperature")),
                max_tokens=_to_int(mc_raw.get("max_tokens")),
                reasoning_effort=mc_raw.get("reasoning_effort"),
            )

        return cls(
            name=str(raw.get("name", "")),
            description=str(raw.get("description", "")),
            system_prompt=str(raw.get("system_prompt", "")),
            tools=_ensure_list(raw.get("tools", [])),
            tools_include_pattern=raw.get("tools_include_pattern") or None,
            skills=_ensure_list(raw.get("skills", [])),
            model_config=mc,
            max_iterations=_to_int(raw.get("max_iterations")) or 15,
            mode=str(raw.get("mode", "sync")),
            mcp_servers=raw.get("mcp_servers", {}) or {},
            triggers=_ensure_list(raw.get("triggers", [])),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> AgentConfig:
        """Load an AgentConfig from an AGENT.json file."""
        return cls.from_json_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_file(cls, path: Path) -> AgentConfig:
        """Load an AgentConfig from either AGENT.json or AGENT.yaml.

        Priority: If path ends with .json, use JSON parser.
                  If path ends with .yaml/.yml, use YAML parser.
        """
        suffix = path.suffix.lower()
        if suffix == ".json":
            return cls.from_json_file(path)
        else:
            return cls.from_yaml_file(path)


class AgentDefinition(ABC):
    """Abstract base class for sub-agent definitions.

    Concrete implementations are either:
    * Pure-config agents (``_ConfigAgentDefinition``, created automatically
      when only an ``AGENT.yaml`` is present).
    * Code agents (user-written Python class that inherits this ABC and lives
      alongside ``AGENT.yaml`` in an ``agent.py`` file).
    """

    @abstractmethod
    def get_config(self) -> AgentConfig:
        """Return the agent configuration."""

    # -- Optional hooks (override as needed) ----------------------------------

    def register_tools(self, registry: Any, parent_tools: Any = None) -> None:
        """Register agent-specific tools into *registry* (a ``ToolRegistry``)."""

    def build_system_prompt(self, workspace: Path) -> str:
        """Build system prompt.  Defaults to ``AgentConfig.system_prompt``."""
        return self.get_config().system_prompt

    def on_complete(self, result: str) -> str:
        """Post-process the final result before returning to the parent agent."""
        return result


class _ConfigAgentDefinition(AgentDefinition):
    """Wrapper that turns a plain ``AgentConfig`` into an ``AgentDefinition``."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    def get_config(self) -> AgentConfig:
        return self._config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str) and val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return []


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
