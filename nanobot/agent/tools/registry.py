"""Tool registry for dynamic tool management."""

from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._hidden: set[str] = set()  # Tools hidden from LLM but still callable internally

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._hidden.discard(name)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def hide_from_llm(self, *names: str) -> None:
        """Hide tools from LLM tool definitions.

        Hidden tools are still callable internally (e.g. by WorkflowRunner)
        but the LLM cannot see or invoke them directly.
        """
        self._hidden.update(names)

    def hide_pattern_from_llm(self, prefix: str) -> None:
        """Hide all tools whose names start with the given prefix."""
        for name in list(self._tools):
            if name.startswith(prefix):
                self._hidden.add(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions visible to the LLM (excludes hidden tools)."""
        return [tool.to_schema() for name, tool in self._tools.items()
                if name not in self._hidden]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        internal: bool = False,
        **extra_kwargs: Any,
    ) -> str:
        """Execute a tool by name with given parameters.

        Args:
            internal: If True, bypass the hidden-tool check (for WorkflowRunner
                      internal calls). If False (default, LLM path), hidden tools
                      return an error.
        """
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        if not internal and name in self._hidden:
            return (
                f"Error: Tool '{name}' is not directly available. "
                "Use the workflow_* tools to perform this action."
            ) + _HINT

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            # Attempt to cast parameters to match schema types
            params = tool.cast_params(params)
            
            # Validate parameters
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
            result = await tool.execute(**params, **extra_kwargs)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
