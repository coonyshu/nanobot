"""
Chat WebSocket endpoint.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from loguru import logger

from nanobot.multi_tenant.agent_pool import current_tenant_id
from nanobot.multi_tenant.auth import verify_websocket_token, extract_tenant_id

router = APIRouter()


@router.websocket("/ws/v1/chat/{user_id}")
async def chat_websocket(websocket: WebSocket, user_id: str):
    """
    Text chat WebSocket — real-time streaming responses.
    """
    svc = websocket.app.state.svc
    tenant_pool = websocket.app.state.tenant_pool
    action_manager = websocket.app.state.action_manager

    token_payload = await verify_websocket_token(websocket)
    tenant_id = extract_tenant_id(token_payload)
    auth_user = token_payload.get("sub") if token_payload else None

    await websocket.accept()
    logger.info("Chat WebSocket connected: user_id={}, tenant_id={}, auth_user={}", user_id, tenant_id, auth_user)

    if auth_user and token_payload:
        svc.set_user_auth_info(user_id, {
            "username": auth_user,
            "user_id": token_payload.get("user_id"),
            "tenant_id": tenant_id,
            "role": token_payload.get("role"),
        })

    # Register WS sender for frontend actions
    async def ws_send_json(msg: dict):
        await websocket.send_json(msg)

    await tenant_pool.get_or_create_loop(tenant_id)
    chat_action_mgr = tenant_pool.get_action_manager_safe(tenant_id) or action_manager
    chat_action_mgr.register_ws_sender(user_id, ws_send_json)
    logger.info("Chat WS sender registered for user={}, tenant={}", user_id, tenant_id)

    _tenant_token = current_tenant_id.set(tenant_id)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "action_result":
                action_id = data.get("action_id", "")
                success = data.get("success", False)
                result = data.get("result", "")
                chat_action_mgr.resolve(action_id, success, result)
            elif msg_type == "register_tools":
                descriptors = data.get("descriptors", [])
                registered = chat_action_mgr.register_from_descriptors(user_id, descriptors)
                await websocket.send_json({"type": "tools_registered", "tools": registered})
            elif msg_type == "context_update":
                context = data.get("context", {})
                svc.update_tab_context(user_id, context)
            else:
                message = data.get("message", "")
                if message:
                    tid = current_tenant_id.get()
                    t_mgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                    t_mgr.set_user_context(user_id)

                    from ..callbacks import agent_callback

                    response = await agent_callback(
                        user_id, message,
                        svc=svc, tenant_pool=tenant_pool, action_manager=action_manager,
                    )
                    await websocket.send_json({"type": "complete", "content": response})
    except WebSocketDisconnect:
        logger.info("Chat WebSocket disconnected: user_id={}", user_id)
    finally:
        chat_action_mgr.unregister_ws_sender(user_id)
        current_tenant_id.reset(_tenant_token)
