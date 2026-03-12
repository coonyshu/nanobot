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
        
        # Clear active_agent in parent session metadata
        try:
            from nanobot.session.manager import SessionManager
            user_workspace = self._session.agent_workspace.parent.parent if self._session.agent_workspace.parent.name == "agents" else None
            if user_workspace:
                sessions = SessionManager(user_workspace)
                session_key = f"{self._session.channel}:{self._session.chat_id}"
                session = sessions.get_or_create(session_key)
                if "active_agent" in session.metadata:
                    del session.metadata["active_agent"]
                    sessions.save(session)
        except Exception as e:
            from loguru import logger
            logger.warning(f"Failed to clear active_agent: {e}")
        
        return f"Exiting agent session. Summary: {summary}"
