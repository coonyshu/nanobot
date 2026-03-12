"""Context builder for assembling agent prompts."""

import base64
import json
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import detect_image_mime


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._agent_registry = None  # Set externally by AgentLoop

    def set_agent_registry(self, registry: Any) -> None:
        """Wire the AgentRegistry so agent summaries appear in the system prompt."""
        self._agent_registry = registry

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}\n\n**Note**: Memory contains historical context only. When user triggers a persistent agent (e.g., says \"开始安检\"), you MUST still call `enter_agent` - the agent will handle continuation or restart automatically.")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        # Registered sub-agents (multi-agent scheduling)
        if self._agent_registry is not None:
            # Integrated agents: inject their system prompt into main agent
            for agent_def in (self._agent_registry.get(n) for n in self._agent_registry.list_names()):
                if agent_def is None:
                    continue
                cfg = agent_def.get_config()
                if cfg.mode == "integrated":
                    prompt = agent_def.build_system_prompt(self.workspace)
                    if prompt:
                        parts.append(prompt)

            # Delegated agents: show summary for the delegate tool
            delegated_summary = self._agent_registry.build_agents_summary(
                filter_fn=lambda ad: ad.get_config().mode not in ("integrated", "persistent")
            )
            if delegated_summary:
                parts.append(
                    "# Available Agents\n\n"
                    "Use the `delegate` tool to assign tasks to a specialized sub-agent.\n\n"
                    + delegated_summary
                )

            # Persistent agents: show summary for the enter_agent tool
            persistent_summary = self._agent_registry.build_agents_summary(
                filter_fn=lambda ad: ad.get_config().mode == "persistent"
            )
            if persistent_summary:
                parts.append(
                    "# Persistent Agents\n\n"
                    "Use the `enter_agent` tool to enter a persistent agent session when the user's request "
                    "matches one of the agent's trigger keywords. The agent takes over the conversation until it exits.\n\n"
                    "## ⚠️ CRITICAL RULES - MUST READ\n\n"
                    "1. **When a user's message matches ANY trigger keyword in `<triggers>`, you MUST call `enter_agent` immediately.**\n"
                    "2. **DO NOT respond directly** - even if you think you know the answer or have context from memory.\n"
                    "3. **DO NOT use `spawn`, `delegate`, or handle the request yourself** - these tools cannot access the specialized workflow tools.\n"
                    "4. **DO NOT write scripts or use file operations** - the persistent agent has the correct tools already.\n"
                    "5. **IGNORE any memory about previous sessions** - always call `enter_agent` when triggers match.\n\n"
                    "**Example**: If user says \"开始安检\" and a persistent agent has trigger \"安检\", call:\n"
                    "```\n"
                    "enter_agent(agent_name=\"workflow-inspector\", task=\"开始安检\")\n"
                    "```\n\n"
                    "**IMPORTANT**: Even if you remember a previous安检 session, you MUST still call `enter_agent`. "
                    "The persistent agent will handle continuation or restart automatically.\n\n"
                    + persistent_summary
                )

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        platform_policy = ""
        if system == "Windows":
            platform_policy = """## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
"""
        else:
            platform_policy = """## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
"""

        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

{platform_policy}

## nanobot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(channel, chat_id)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content

        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": merged},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            # Detect real MIME type from magic bytes; fallback to filename guess
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Supports multimodal content (text + images).
        If result is JSON with __multimodal__=true, extract image_b64 and format as vision content.
        """
        # 尝试解析为 JSON，检查是否是多模态内容
        try:
            result_data = json.loads(result)
            if isinstance(result_data, dict) and result_data.get("__multimodal__"):
                # 多模态内容：构建图文混合格式
                text_content = result_data.get("text", "操作完成")
                image_b64 = result_data.get("image_b64", "")
                mime_type = result_data.get("mime_type", "image/jpeg")
                
                if image_b64:
                    # 使用 OpenAI vision 格式：[{type: "text", text: ...}, {type: "image_url", image_url: {...}}]
                    content = [
                        {"type": "text", "text": text_content},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_b64}"
                            }
                        }
                    ]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": content
                    })
                    return messages
        except (json.JSONDecodeError, TypeError, KeyError):
            # 不是 JSON 或解析失败，按普通文本处理
            pass
        
        # 普通文本结果
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
