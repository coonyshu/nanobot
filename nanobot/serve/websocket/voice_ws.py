"""
Voice WebSocket endpoint.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from loguru import logger

from .websocket_common import (
    authenticate_websocket,
    set_user_auth_info,
    TenantContextManager,
)

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

    ctx = await authenticate_websocket(websocket, user_id)
    if not ctx:
        return

    await websocket.accept()
    
    if ctx.auth_user:
        logger.info(
            "Voice WebSocket connected: user_id={}, auth_user={}, tenant_id={}",
            user_id, ctx.auth_user, ctx.tenant_id
        )
        set_user_auth_info(svc, ctx)
    else:
        logger.info(
            "Voice WebSocket connected: user_id={} (no auth), tenant_id={}",
            user_id, ctx.tenant_id
        )

    with TenantContextManager(ctx.tenant_id):
        try:
            await voice_handler.handle_connection(websocket, user_id)
        except WebSocketDisconnect:
            logger.info("Voice WebSocket disconnected: user_id={}", user_id)
        except Exception as e:
            logger.error("Voice WebSocket error: {}", e)
