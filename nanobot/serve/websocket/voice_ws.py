"""
Voice WebSocket endpoint.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from loguru import logger

from nanobot.multi_tenant.agent_pool import current_tenant_id
from nanobot.multi_tenant.auth import verify_websocket_token, extract_tenant_id, WS_AUTH_MODE

router = APIRouter()


@router.websocket("/ws/v1/voice/{user_id}")
async def voice_websocket(websocket: WebSocket, user_id: str):
    """
    Voice interaction WebSocket.

    Protocol:
    - Auth via query param ``?token=xxx``
    - Upstream: Opus audio frames (binary) + control messages (JSON)
    - Downstream: status (JSON) + TTS audio frames (binary)
    """
    voice_handler = websocket.app.state.voice_handler
    if not voice_handler:
        await websocket.close(code=1003, reason="Voice module disabled")
        return

    svc = websocket.app.state.svc
    tenant_pool = websocket.app.state.tenant_pool
    action_manager = websocket.app.state.action_manager

    # JWT verification
    token_payload = await verify_websocket_token(websocket)
    tenant_id = extract_tenant_id(token_payload)
    
    # Debug logging
    if token_payload:
        logger.debug("WebSocket token verified: user_id={}, tenant_id={}, payload={}", user_id, tenant_id, token_payload)
    else:
        logger.warning("WebSocket token verification failed: user_id={}", user_id)

    if WS_AUTH_MODE == "required":
        if not token_payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid or missing token")
            logger.warning("WebSocket auth failed: user_id={}", user_id)
            return
        auth_user = token_payload.get("sub")
    else:
        auth_user = token_payload.get("sub") if token_payload else None
        if token_payload:
            token_user_id = token_payload.get("user_id")
            if token_user_id and token_user_id != user_id:
                logger.warning("WebSocket user_id mismatch: token={}, url={}", token_user_id, user_id)

    await websocket.accept()
    if auth_user:
        logger.info("Voice WebSocket connected: user_id={}, auth_user={}, tenant_id={}", user_id, auth_user, tenant_id)
        svc.set_user_auth_info(user_id, {
            "username": auth_user,
            "user_id": token_payload.get("user_id"),
            "tenant_id": tenant_id,
            "role": token_payload.get("role"),
        })
    else:
        logger.info("Voice WebSocket connected: user_id={} (no auth), tenant_id={}", user_id, tenant_id)

    _tenant_token = current_tenant_id.set(tenant_id)
    try:
        await voice_handler.handle_connection(websocket, user_id)
    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected: user_id={}", user_id)
    except Exception as e:
        logger.error("Voice WebSocket error: {}", e)
    finally:
        current_tenant_id.reset(_tenant_token)
