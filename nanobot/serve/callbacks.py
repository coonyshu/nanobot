"""
Agent callback functions — called by voice/chat handlers.
"""

import base64
import tempfile
from pathlib import Path

from loguru import logger

from .state import ServiceState


def _format_tool_hint(tool_hint: str) -> str:
    """Convert a tool call hint into a friendly Chinese description."""
    tool_descriptions = {
        "address_search": "🔍 正在搜索地址...",
        "work_form_open_form": "\U0001f4cb 正在打开工作表单...",
        "inspection_open_form": "\U0001f4cb 正在打开工作表单...",
        "ui_show_address_selector": "📍 正在准备地址选择...",
        "camera_take_photo": "📸 正在拍照...",
        "exec": "⚙️ 正在执行命令...",
        "read_file": "📄 正在读取文件...",
        "write_file": "💾 正在写入文件...",
        "web_search": "🌐 正在搜索信息...",
    }
    tool_name = tool_hint.split("(")[0].strip() if "(" in tool_hint else tool_hint.strip()
    return tool_descriptions.get(tool_name, f"⚙️ 正在执行 {tool_name}...")


async def agent_callback(
    user_id: str,
    message: str,
    *,
    svc: ServiceState,
    tenant_pool,
    action_manager,
    enable_streaming: bool = True,
) -> str:
    """
    Agent callback — invoked by the voice / chat WebSocket handlers.

    ``svc``, ``tenant_pool``, and ``action_manager`` are injected from
    ``app.state`` at the call site.
    """
    from nanobot.tenant.agent_pool import current_tenant_id

    try:
        logger.info("[MainAgent] processing: user={}, message={}", user_id, message)

        # Set frontend action tool user context
        tenant_id = current_tenant_id.get()
        tenant_action_mgr = tenant_pool.get_action_manager_safe(tenant_id) or action_manager
        tenant_action_mgr.set_user_context(user_id)

        # Inject auth + tab context
        auth_prefix = svc.build_auth_context_prefix(user_id)
        tab_prefix = svc.build_tab_context_prefix(user_id)
        context_parts = [p for p in [auth_prefix, tab_prefix] if p]
        context_prefix = "\n".join(context_parts)
        enriched_message = f"{context_prefix}\n\n{message}" if context_prefix else message

        # Progress callback
        async def on_progress(content: str, *, tool_hint: bool = False):
            def clean_surrogates(s: str) -> str:
                return s.encode("utf-8", errors="replace").decode("utf-8")

            if tool_hint:
                logger.info("[Progress] Tool hint: {}", content)
                friendly_hint = _format_tool_hint(content)
                _, session_data = svc.get_user_session(user_id)
                if session_data:
                    try:
                        websocket = session_data["websocket"]
                        await websocket.send_json({
                            "type": "thinking",
                            "text": clean_surrogates(friendly_hint),
                        })
                    except Exception as e:
                        logger.warning("Failed to send progress to frontend: {}", e)
            else:
                logger.info("[Progress] Thinking: {}", content)

        # Stream callback
        on_stream_callback = None
        _, cur_session_data = svc.get_user_session(user_id)
        logger.info("[Stream] enable_streaming={}, cur_session_data={}", enable_streaming, cur_session_data is not None)
        if enable_streaming and cur_session_data:
            collected_text = ""
            session_obj = cur_session_data.get("session")
            show_thinking = getattr(session_obj, "show_thinking", True) if session_obj else True

            async def on_stream(chunk: str, *, is_first: bool = False, reasoning: bool = False, agent_name: str | None = None):
                nonlocal collected_text
                try:
                    _, sd = svc.get_user_session(user_id)
                    if not sd:
                        return
                    websocket = sd["websocket"]
                    if reasoning:
                        if show_thinking:
                            await websocket.send_json({
                                "type": "thinking_chunk",
                                "chunk": chunk,
                                "is_first": is_first,
                                "agent_name": agent_name,
                            })
                    else:
                        await websocket.send_json({
                            "type": "text_chunk",
                            "chunk": chunk,
                            "is_first": is_first,
                            "agent_name": agent_name,
                        })
                        collected_text += chunk
                except Exception as e:
                    logger.warning("Failed to send stream chunk: {}", e)

            on_stream_callback = on_stream
            on_stream_callback.enable_thinking = show_thinking
            logger.info("[Stream] Stream callback enabled for user {}, enable_thinking={}", user_id, show_thinking)

        logger.info("Calling tenant_pool.process_for_user for tenant={}, user={}...", tenant_id, user_id)

        response, agent_name = await tenant_pool.process_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            content=enriched_message,
            session_key=f"voice:{user_id}",
            channel="voice",
            chat_id=user_id,
            on_progress=on_progress,
            on_stream=on_stream_callback,
        )
        logger.info("[AgentCallback] process_for_user returned: agent_name={}, response_len={}", 
                    agent_name, len(response) if response else 0)

        # 显示完整的响应内容，不再截断
        logger.info("[AgentCallback] response from agent (agent_name={}, response_len={}): {}", 
                    agent_name, len(response) if response else 0, response or "empty")

        # Check if SubAgent was active based on returned agent_name
        # If agent_name is not None, SubAgent processed the message and response was already sent via bus
        if agent_name:
            logger.info("[AgentCallback] SubAgent '{}' active, response sent via bus/outbound", agent_name)
            # Set active agent name in voice session
            sid, voice_session = svc.get_user_session(user_id)
            if voice_session:
                session_obj = voice_session.get("session")
                if session_obj:
                    session_obj.active_agent_name = agent_name
            return response or "抱歉，我没有得到有效的回复。", agent_name

        # MainAgent response - check if there's an active voice session
        sid, voice_session = svc.get_user_session(user_id)
        if voice_session:
            # Voice mode: agent loop sends responses via bus -> outbound.py
            # Set a marker so gateway knows not to send duplicate message
            session_obj = voice_session.get("session")
            if session_obj:
                session_obj._response_via_bus = True
                session_obj.active_agent_name = None  # Clear active agent (MainAgent)
            # Don't send WebSocket messages here to avoid duplicates
            logger.info("[AgentCallback] Voice mode active, response sent via bus/outbound")
            return response or "抱歉，我没有得到有效的回复。", agent_name

        # Chat mode: send response directly via callback return
        _, final_session_data = svc.get_user_session(user_id)
        logger.info("[AgentCallback] final_session_data found: {}", final_session_data is not None)
        if enable_streaming and on_stream_callback and final_session_data:
            logger.info("[AgentCallback] Sending stream completion signal (agent_name={}, response_len={})", 
                       agent_name, len(response) if response else 0)
            try:
                websocket = final_session_data["websocket"]
                session_obj = final_session_data.get("session")

                # Send clear_thinking first
                await websocket.send_json({"type": "clear_thinking"})
                
                # Send the full response text if available
                if response:
                    await websocket.send_json({
                        "type": "text_chunk",
                        "chunk": response,
                        "is_first": True,
                        "agent_name": agent_name,
                    })
                    
                # Send text_complete with agent_name - frontend will update avatar
                await websocket.send_json({"type": "text_complete", "agent_name": agent_name})
                logger.info("[AgentCallback] Stream completion signal sent")
                
                # Also send full text to TTS queue
                if response and session_obj and hasattr(session_obj, "tts_text_queue"):
                    await session_obj.tts_text_queue.put(response)
                
                if session_obj:
                    session_obj._streaming_sent = True
            except Exception as e:
                logger.warning("Failed to send stream complete: {}", e)
        else:
            logger.info("[AgentCallback] No stream completion: enable_streaming={}, on_stream_callback={}, final_session_data={}",
                       enable_streaming, on_stream_callback is not None, final_session_data is not None)
            _, clear_session_data = svc.get_user_session(user_id)
            if clear_session_data:
                try:
                    websocket = clear_session_data["websocket"]
                    await websocket.send_json({"type": "clear_thinking"})
                except Exception as e:
                    logger.warning("Failed to send clear_thinking signal: {}", e)

        return response or "抱歉，我没有得到有效的回复。", agent_name

    except Exception as e:
        logger.error("Agent error: {} (type: {})", e, type(e).__name__, exc_info=True)
        return "抱歉，我遇到了一些问题，请稍后再试。", None


async def agent_image_callback(
    user_id: str,
    message: str,
    image_b64: str,
    mime_type: str = "image/jpeg",
    *,
    provider,
    model: str,
) -> str:
    """Image recognition callback (LLM-only, no full agent loop)."""
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
    suffix = ext_map.get(mime_type, ".jpg")
    tmp_path = None

    try:
        prompt = message or "请描述这张图片的内容"
        logger.info("Agent image processing: user={}, prompt={}", user_id, prompt)

        image_data = base64.b64decode(image_b64)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name

        system_prompt = (
            "你是一个图片识别助手。请根据用户的问题和要求来回复：\n"
            "- 如果用户要求简洁回答（如\"只回答有人/没有人\"），请严格按要求回复，不要添加额外内容\n"
            "- 如果用户要求详细描述，请提供完整的图片分析\n"
            "- 如果用户没有明确要求，请简洁准确地回答问题\n"
            "- 如果用户要求在回复末尾输出JSON代码块，请严格按照指定格式输出，确保JSON语法正确；"
            "无法识别的字段填null，布尔值用true/false，数字不加引号"
        )

        content_parts = [
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_parts},
        ]

        response = await provider.chat(messages=messages, tools=[], model=model, temperature=0.7)
        result = response.content or "抱歉，我无法识别这张图片。"
        logger.info("Agent image response: {}", result)
        return result

    except Exception as e:
        logger.error("Agent image error: {}", e, exc_info=True)
        return "抱歉，图片识别遇到了一些问题，请稍后再试。"
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


async def send_subagent_message_to_user(user_id: str, message: str, *, svc: ServiceState):
    """Send a subagent message to the user via the voice WebSocket."""
    _, session_data = svc.get_user_session(user_id)
    if not session_data:
        logger.debug("No active voice session for user {}, skipping message", user_id)
        return

    try:
        websocket = session_data["websocket"]
        session = session_data["session"]

        await websocket.send_json({"type": "text", "text": message})
        logger.info("[SubAgent] sent message to user {}: {}...", user_id, message[:50])

        await session.tts_text_queue.put(message)
    except Exception as e:
        logger.error("Failed to send subagent message to user {}: {}", user_id, e)
