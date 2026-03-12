"""
WebSocket common utilities.

Shared logic for Chat and Voice WebSocket endpoints.
"""

from typing import Optional, Callable, Any, Awaitable
from dataclasses import dataclass

from fastapi import WebSocket
from loguru import logger

from nanobot.tenant.agent_pool import current_tenant_id
from nanobot.tenant.auth import verify_websocket_token, extract_tenant_id, WS_AUTH_MODE


@dataclass
class WebSocketContext:
    """Context for a WebSocket connection."""
    user_id: str
    tenant_id: str
    auth_user: Optional[str]
    token_payload: Optional[dict]
    ws_send_json: Callable[[dict], Awaitable[None]]


async def authenticate_websocket(
    websocket: WebSocket,
    user_id: str,
    require_auth: bool = False,
) -> Optional[WebSocketContext]:
    """
    Authenticate a WebSocket connection and return context.
    
    Args:
        websocket: The WebSocket connection
        user_id: User ID from URL path
        require_auth: Whether authentication is required
        
    Returns:
        WebSocketContext if authentication succeeds, None otherwise
    """
    token_payload = await verify_websocket_token(websocket)
    tenant_id = extract_tenant_id(token_payload)
    
    if token_payload:
        logger.debug(
            "WebSocket token verified: user_id={}, tenant_id={}",
            user_id, tenant_id
        )
    else:
        logger.warning("WebSocket token verification failed: user_id={}", user_id)
    
    if WS_AUTH_MODE == "required" or require_auth:
        if not token_payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid or missing token")
            logger.warning("WebSocket auth failed: user_id={}", user_id)
            return None
    
    auth_user = token_payload.get("sub") if token_payload else None
    
    if token_payload:
        token_user_id = token_payload.get("user_id")
        if token_user_id and token_user_id != user_id:
            logger.warning(
                "WebSocket user_id mismatch: token={}, url={}",
                token_user_id, user_id
            )
    
    async def ws_send_json(msg: dict):
        await websocket.send_json(msg)
    
    return WebSocketContext(
        user_id=user_id,
        tenant_id=tenant_id,
        auth_user=auth_user,
        token_payload=token_payload,
        ws_send_json=ws_send_json,
    )


def set_user_auth_info(svc: Any, ctx: WebSocketContext) -> None:
    """
    Set user authentication info in the service.
    
    Args:
        svc: The service instance
        ctx: WebSocket context
    """
    if ctx.auth_user and ctx.token_payload:
        svc.set_user_auth_info(ctx.user_id, {
            "username": ctx.auth_user,
            "user_id": ctx.token_payload.get("user_id"),
            "tenant_id": ctx.tenant_id,
            "role": ctx.token_payload.get("role"),
        })


class TenantContextManager:
    """Context manager for tenant-scoped operations."""
    
    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id
        self._token = None
    
    def __enter__(self):
        self._token = current_tenant_id.set(self._tenant_id)
        return self
    
    def __exit__(self, *args):
        current_tenant_id.reset(self._token)
    
    async def __aenter__(self):
        return self.__enter__()
    
    async def __aexit__(self, *args):
        self.__exit__(*args)


async def register_ws_sender(
    tenant_pool: Any,
    action_manager: Any,
    ctx: WebSocketContext,
) -> Any:
    """
    Register WebSocket sender for frontend actions.
    
    Args:
        tenant_pool: Tenant agent pool
        action_manager: Global action manager
        ctx: WebSocket context
        
    Returns:
        The tenant's action manager
    """
    await tenant_pool.get_or_create_loop(ctx.tenant_id)
    t_mgr = tenant_pool.get_action_manager_safe(ctx.tenant_id) or action_manager
    t_mgr.register_ws_sender(ctx.user_id, ctx.ws_send_json)
    logger.info(
        "WebSocket sender registered for user={}, tenant={}",
        ctx.user_id, ctx.tenant_id
    )
    return t_mgr


def unregister_ws_sender(action_manager: Any, user_id: str) -> None:
    """
    Unregister WebSocket sender.
    
    Args:
        action_manager: Action manager instance
        user_id: User ID
    """
    action_manager.unregister_ws_sender(user_id)


async def handle_register_tools(
    action_manager: Any,
    user_id: str,
    descriptors: list[dict],
    ws_send_json: Callable[[dict], Awaitable[None]],
) -> list[str]:
    """
    Handle register_tools message from frontend.
    
    Args:
        action_manager: Action manager instance
        user_id: User ID
        descriptors: Tool descriptors
        ws_send_json: Function to send JSON response
        
    Returns:
        List of registered tool names
    """
    registered = action_manager.register_from_descriptors(user_id, descriptors)
    logger.info(
        "Frontend registered {} tools for user={}: {}",
        len(registered), user_id, registered[:5]
    )
    await ws_send_json({"type": "tools_registered", "tools": registered})
    return registered


async def handle_action_result(
    action_manager: Any,
    data: dict,
) -> None:
    """
    Handle action_result message from frontend.
    
    Args:
        action_manager: Action manager instance
        data: Message data containing action_id, success, result
    """
    action_id = data.get("action_id", "")
    success = data.get("success", False)
    result = data.get("result", "")
    action_manager.resolve(action_id, success, result)


async def handle_context_update(
    svc: Any,
    user_id: str,
    context: dict,
) -> None:
    """
    Handle context_update message from frontend.
    
    Args:
        svc: Service instance
        user_id: User ID
        context: Tab context data
    """
    svc.update_tab_context(user_id, context)
