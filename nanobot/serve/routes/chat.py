"""
Chat and image endpoints.
"""

import base64

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request

from ..models import ChatRequest, ChatResponse, ImageChatResponse

router = APIRouter(prefix="/api/v1")


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    """Text chat endpoint."""
    from ..callbacks import agent_callback

    svc = request.app.state.svc
    tenant_pool = request.app.state.tenant_pool
    action_manager = request.app.state.action_manager

    response, agent_name = await agent_callback(
        req.user_id, req.message,
        svc=svc, tenant_pool=tenant_pool, action_manager=action_manager,
    )
    return ChatResponse(response=response, user_id=req.user_id, agent_name=agent_name)


@router.post("/image", response_model=ImageChatResponse)
async def image_chat(
    request: Request,
    file: UploadFile = File(...),
    message: str = Form(default="请描述这张图片的内容"),
    user_id: str = Form(default="default"),
):
    """Image recognition chat endpoint."""
    from ..callbacks import agent_image_callback

    image_data = await file.read()
    if len(image_data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    image_b64 = base64.b64encode(image_data).decode("utf-8")
    mime_type = file.content_type or "image/jpeg"

    provider = request.app.state.provider
    model = request.app.state.nanobot_config.agents.defaults.model
    svc = request.app.state.svc

    response, agent_name = await agent_image_callback(
        user_id, message, image_b64, mime_type,
        provider=provider, model=model, svc=svc,
    )
    return ImageChatResponse(response=response, user_id=user_id, agent_name=agent_name)
