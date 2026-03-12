"""Unified Agent Loop: the core LLM processing engine."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.providers.base import LLMProvider


class AgentLoop:
    """The core LLM processing loop for unified Agent.
    
    This loop handles:
    1. Running LLM inference with tools
    2. Executing tool calls
    3. Handling streaming responses
    4. Checking for exit conditions
    
    Used by both MainAgent (via AgentLoop in loop.py) and SubAgent (via Agent class).
    """

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_iterations: int = 40,
        reasoning_effort: str | None = None,
        agent_name: str = "agent",
        get_active_agent: Callable[[], str | None] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.reasoning_effort = reasoning_effort
        self.agent_name = agent_name
        self.get_active_agent = get_active_agent

    def _get_effective_agent_name(self) -> str:
        """Get the effective agent name for streaming.
        
        If there's an active sub-agent, use its name instead of the main agent.
        """
        if self.get_active_agent:
            active = self.get_active_agent()
            if active:
                return active
        return self.agent_name

    async def run(
        self,
        messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        check_exit: Callable[[], bool] | None = None,
        get_exit_summary: Callable[[], str | None] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Run the agent loop.

        Args:
            messages: Conversation messages (will be modified in place)
            on_progress: Callback for progress updates
            on_stream: Callback for streaming chunks
            check_exit: Function to check if exit was requested
            get_exit_summary: Function to get exit summary

        Returns:
            tuple: (final_content, tools_used, updated_messages)
        """
        if on_stream is not None and hasattr(self.provider, "chat_stream"):
            return await self._run_streaming(
                messages, on_progress, on_stream, check_exit, get_exit_summary
            )
        else:
            return await self._run_non_streaming(
                messages, on_progress, check_exit, get_exit_summary
            )

    async def _run_non_streaming(
        self,
        messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        check_exit: Callable[[], bool] | None = None,
        get_exit_summary: Callable[[], str | None] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Non-streaming LLM tool-calling loop."""
        iteration = 0
        final_content: str | None = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions() or None,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tool_call_dicts,
                })

                for tc in response.tool_calls:
                    tools_used.append(tc.name)
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    logger.info("[Agent:{}] tool: {}({})",
                                self.agent_name, tc.name, args_str[:200])
                    result = await self.tools.execute(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    })

                if check_exit and check_exit():
                    final_content = (get_exit_summary() if get_exit_summary else None) or "Session ended."
                    messages.append({"role": "assistant", "content": final_content})
                    break
            else:
                clean = self._strip_think(response.content)
                if response.finish_reason == "error":
                    logger.error("[Agent:{}] LLM error: {}",
                                 self.agent_name, (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error."
                    break
                messages.append({"role": "assistant", "content": clean})
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("[Agent:{}] max iterations ({}) reached",
                           self.agent_name, self.max_iterations)
            final_content = (
                f"Reached maximum iterations ({self.max_iterations}). "
                "Please try a simpler instruction."
            )

        return final_content, tools_used, messages

    async def _run_streaming(
        self,
        messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        check_exit: Callable[[], bool] | None = None,
        get_exit_summary: Callable[[], str | None] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Streaming LLM tool-calling loop."""
        iteration = 0
        final_content: str | None = None
        tools_used: list[str] = []
        enable_thinking = getattr(on_stream, "enable_thinking", True)

        while iteration < self.max_iterations:
            iteration += 1

            collected_content = ""
            collected_tool_calls = None
            is_first_chunk = True
            finish_reason: str | None = None

            async for content_chunk, reasoning_chunk, tool_calls, finish_reason in self.provider.chat_stream(
                messages=messages,
                tools=self.tools.get_definitions() or None,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                enable_thinking=enable_thinking,
            ):
                effective_agent_name = self._get_effective_agent_name()
                if reasoning_chunk and on_stream:
                    await on_stream(
                        reasoning_chunk, 
                        is_first=is_first_chunk, 
                        reasoning=True, 
                        agent_name=effective_agent_name
                    )
                    is_first_chunk = False
                if content_chunk and on_stream:
                    collected_content += content_chunk
                    await on_stream(
                        content_chunk, 
                        is_first=is_first_chunk, 
                        reasoning=False, 
                        agent_name=effective_agent_name
                    )
                    is_first_chunk = False
                if tool_calls:
                    collected_tool_calls = tool_calls

            if collected_tool_calls:
                if on_progress:
                    clean = self._strip_think(collected_content)
                    if clean:
                        await on_progress(clean)
                    await on_progress(self._tool_hint(collected_tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in collected_tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": collected_content,
                    "tool_calls": tool_call_dicts,
                })

                for tc in collected_tool_calls:
                    tools_used.append(tc.name)
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    logger.info("[Agent:{}] tool: {}({})",
                                self.agent_name, tc.name, args_str[:200])
                    result = await self.tools.execute(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    })

                if check_exit and check_exit():
                    final_content = (get_exit_summary() if get_exit_summary else None) or "Session ended."
                    messages.append({"role": "assistant", "content": final_content})
                    break
            else:
                clean = self._strip_think(collected_content)
                if finish_reason == "error":
                    logger.error("[Agent:{}] LLM stream error: {}",
                                 self.agent_name, (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error."
                    break
                messages.append({"role": "assistant", "content": clean})
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("[Agent:{}] max iterations ({}) reached",
                           self.agent_name, self.max_iterations)
            final_content = (
                f"Reached maximum iterations ({self.max_iterations}). "
                "Please try a simpler instruction."
            )
        
        return final_content, tools_used, messages

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think/> blocks from content."""
        if not text:
            return None
        return re.sub(r"<think[\s\S]*?</think*>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}...")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)


SubAgentLoop = AgentLoop
