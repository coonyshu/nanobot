"""Agent-to-Agent communication tool."""

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import A2AMessage


class A2ATool(Tool):
    """Tool for sending messages to other agents."""

    def __init__(self, send_callback):
        self._send_callback = send_callback
        self._agent_name: str = "unknown"

    @property
    def name(self) -> str:
        return "send_to_agent"

    @property
    def description(self) -> str:
        return "Send a message to another agent for inter-agent communication."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the target agent"
                },
                "message": {
                    "type": "string",
                    "description": "Message to send to the agent"
                },
                "context": {
                    "type": "object",
                    "description": "Additional context for the message",
                    "properties": {}
                }
            },
            "required": ["agent_name", "message"]
        }

    async def execute(self, **kwargs) -> str:
        """Execute the tool."""
        agent_name = kwargs.get("agent_name")
        message = kwargs.get("message")
        context = kwargs.get("context", {})

        if not agent_name or not message:
            return "Error: agent_name and message are required"

        try:
            msg = A2AMessage(
                from_agent=self._agent_name,
                to_agent=agent_name,
                message=message,
                context=context
            )
            await self._send_callback(msg)
            return f"Message sent to agent {agent_name}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
