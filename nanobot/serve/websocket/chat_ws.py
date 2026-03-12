"""
Chat WebSocket endpoint.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from loguru import logger

from .websocket_common import (
    authenticate_websocket,
    set_user_auth_info,
    TenantContextManager,
    register_ws_sender,
    unregister_ws_sender,
    handle_register_tools,
    handle_action_result,
    handle_context_update,
)

router = APIRouter()


@router.websocket("/ws/v1/chat/{user_id}")
async def chat_websocket(websocket: WebSocket, user_id: str):
    """
    Text chat WebSocket — real-time streaming responses.
    """
    svc = websocket.app.state.svc
    tenant_pool = websocket.app.state.tenant_pool
    action_manager = websocket.app.state.action_manager

    ctx = await authenticate_websocket(websocket, user_id)
    if not ctx:
        return

    await websocket.accept()
    logger.info(
        "Chat WebSocket connected: user_id={}, tenant_id={}, auth_user={}",
        user_id, ctx.tenant_id, ctx.auth_user
    )

    set_user_auth_info(svc, ctx)

    t_mgr = await register_ws_sender(tenant_pool, action_manager, ctx)

    with TenantContextManager(ctx.tenant_id):
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "message")

                if msg_type == "action_result":
                    await handle_action_result(t_mgr, data)

                elif msg_type == "register_tools":
                    descriptors = data.get("descriptors", [])
                    await handle_register_tools(
                        t_mgr, user_id, descriptors, ctx.ws_send_json
                    )

                elif msg_type == "context_update":
                    context = data.get("context", {})
                    await handle_context_update(svc, user_id, context)

                else:
                    message = data.get("message", "")
                    if message:
                        t_mgr.set_user_context(user_id)

                        from ..callbacks import agent_callback

                        logger.info("[ChatWS] Calling agent_callback for user={}", user_id)
                        response, _ = await agent_callback(
                            user_id, message,
                            svc=svc, tenant_pool=tenant_pool, action_manager=action_manager,
                        )
                        logger.info("[ChatWS] agent_callback returned, sending 'complete' to websocket")
                        await websocket.send_json({"type": "complete", "content": response})

        except WebSocketDisconnect:
            logger.info("Chat WebSocket disconnected: user_id={}", user_id)
        finally:
            unregister_ws_sender(t_mgr, user_id)
