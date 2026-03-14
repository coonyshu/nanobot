"""
Agent callback functions — called by voice/chat handlers.
"""

import json

from loguru import logger

from .context_resolver import ContextResolver
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
        resolver = ContextResolver(svc, tenant_pool)

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
        stream_text_sent = False
        _, cur_session_data = svc.get_user_session(user_id)
        logger.info("[Stream] enable_streaming={}, cur_session_data={}", enable_streaming, cur_session_data is not None)
        if enable_streaming and cur_session_data:
            collected_text = ""
            session_obj = cur_session_data.get("session")
            show_thinking = getattr(session_obj, "show_thinking", True) if session_obj else True

            async def on_stream(chunk: str, *, is_first: bool = False, reasoning: bool = False, agent_name: str | None = None):
                nonlocal collected_text, stream_text_sent
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
                        stream_text_sent = True
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
            resolver.set_active_agent(user_id, agent_name)
            resolved_ctx = resolver.resolve(user_id)
            sid, voice_session = resolved_ctx.session_id, resolved_ctx.session_data
            active_tenant_id = resolved_ctx.tenant_id or tenant_id
            if voice_session:
                websocket = voice_session["websocket"]
                session_obj = voice_session.get("session")
                show_photo_buttons = None
                if session_obj:
                    session_obj.active_agent_name = agent_name
                    try:
                        if isinstance(response, str) and response.strip().startswith('{'):
                            parsed = json.loads(response)
                            if isinstance(parsed, dict) and "show_photo_buttons" in parsed:
                                session_obj.agent_context["show_photo_buttons"] = parsed["show_photo_buttons"]
                                logger.info("[AgentCallback] extracted show_photo_buttons={} from workflow agent response", parsed["show_photo_buttons"])
                    except Exception as e:
                        logger.warning("[AgentCallback] failed to parse workflow agent response: {}", e)
                    show_photo_buttons = session_obj.agent_context.get("show_photo_buttons")
                    if show_photo_buttons is None:
                        try:
                            from nanobot.session.manager import SessionManager
                            user_ws = tenant_pool._resolver.ensure_user_dirs(active_tenant_id, user_id)
                            user_sessions = SessionManager(user_ws)
                            sess = user_sessions.get_or_create(f"voice:{user_id}")
                            show_photo_buttons = sess.metadata.get("show_photo_buttons")
                            logger.info("[AgentCallback] Loaded show_photo_buttons={} from persistent session metadata", show_photo_buttons)
                            if show_photo_buttons is not None:
                                session_obj.agent_context["show_photo_buttons"] = show_photo_buttons
                        except Exception as e:
                            logger.warning("[AgentCallback] failed to load show_photo_buttons from session metadata: {}", e)

                    # Last resort: if still None, try to query Workflow Runner directly (in-process fallback)
                    if show_photo_buttons is None and agent_name and "workflow" in agent_name:
                        try:
                            from nanobot_agent_workflow_agent.tools import get_runner
                            
                            # Get runner for current tenant
                            runner = get_runner(active_tenant_id)
                            if runner.current_node_id:
                                current_node_data = runner.collected_data.get(runner.current_node_id, {})
                                if runner._should_show_photo_buttons(runner.current_node_id, current_node_data):
                                    show_photo_buttons = True
                                    session_obj.agent_context["show_photo_buttons"] = True
                                    logger.info("[AgentCallback] Retrieved show_photo_buttons=True directly from WorkflowRunner (fallback)")
                        except ImportError:
                            # Try relative import if installed as package
                            try:
                                from agents.workflow_agent.tools import get_runner
                                runner = get_runner(active_tenant_id)
                                if runner.current_node_id:
                                    current_node_data = runner.collected_data.get(runner.current_node_id, {})
                                    if runner._should_show_photo_buttons(runner.current_node_id, current_node_data):
                                        show_photo_buttons = True
                                        session_obj.agent_context["show_photo_buttons"] = True
                                        logger.info("[AgentCallback] Retrieved show_photo_buttons=True directly from WorkflowRunner (fallback)")
                            except Exception as e:
                                logger.debug("[AgentCallback] Failed to import/query WorkflowRunner: {}", e)
                        except Exception as e:
                            logger.warning("[AgentCallback] Failed to query WorkflowRunner directly: {}", e)

                    if enable_streaming and on_stream_callback:
                        try:
                            await websocket.send_json({"type": "clear_thinking"})
                            if not stream_text_sent and response:
                                await websocket.send_json({
                                    "type": "text_chunk",
                                    "chunk": response,
                                    "is_first": True,
                                    "agent_name": agent_name,
                                })
                            complete_payload = {"type": "text_complete", "agent_name": agent_name}
                            if show_photo_buttons is not None:
                                complete_payload["show_photo_buttons"] = show_photo_buttons
                            await websocket.send_json(complete_payload)
                            if response and hasattr(session_obj, "tts_text_queue"):
                                await session_obj.tts_text_queue.put(response)
                            session_obj._streaming_sent = True
                            logger.info(
                                "[AgentCallback] sent subagent stream completion, stream_text_sent={}",
                                stream_text_sent,
                            )
                        except Exception as e:
                            logger.warning("[AgentCallback] failed to send subagent stream completion: {}", e)

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
            resolver.clear_active_agent(user_id)
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
    svc: ServiceState = None,
) -> tuple[str, str | None]:
    from nanobot.agent.image_processor import AgentImageProcessor

    processor = AgentImageProcessor(provider=provider, model=model, svc=svc)
    return await processor.process(user_id, message, image_b64, mime_type)


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
