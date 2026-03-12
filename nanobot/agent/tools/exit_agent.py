"""Exit agent tool: allows a persistent sub-agent to return control to the main agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.agent import Agent


class ExitAgentTool(Tool):
    """Tool for a persistent sub-agent to exit and return to the main agent."""

    def __init__(self, session: Agent) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "exit_agent"

    @property
    def description(self) -> str:
        return (
            "Exit the current agent session and return control to the main agent. "
            "Call this when the assigned task is completed or when the user wants to leave."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was accomplished in this session",
                },
            },
            "required": ["summary"],
        }

    async def execute(self, summary: str, **kwargs: Any) -> str:
        self._session.exit_summary = summary
        self._session.status = "exiting"
        return f"Exiting agent session. Summary: {summary}"
