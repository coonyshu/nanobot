"""
Auth endpoints — login / register / refresh / me.
"""

from datetime import timedelta
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request

from loguru import logger

from nanobot.tenant.auth import (
    create_access_token,
    get_current_user,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
)
from ..models import (
    LoginRequest, LoginResponse,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/api/v1/auth")


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """User login — returns a JWT token."""
    user_store = request.app.state.user_store
    user = await user_store.authenticate(req.username, req.password, None)

    if not user:
        return LoginResponse(success=False, message="用户名或密码错误")

    access_token_expires = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": user.username,
            "user_id": user.user_id,
            "tenant_id": user.tenant_id,
            "role": user.role,
        },
        expires_delta=access_token_expires,
    )

    logger.info("User logged in: {} (tenant={})", user.username, user.tenant_id)

    return LoginResponse(
        success=True,
        access_token=access_token,
        token_type="bearer",
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        role=user.role,
        message="登录成功",
    )


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    """Register a new user under the given tenant."""
    tenant_store = request.app.state.tenant_store
    user_store = request.app.state.user_store

    tenant = tenant_store.get(req.tenant_id)
    if not tenant or not tenant.enabled:
        raise HTTPException(status_code=400, detail=f"租户 '{req.tenant_id}' 不存在或已禁用")

    try:
        user = await user_store.register(
            username=req.username,
            password=req.password,
            tenant_id=req.tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info("User registered: {} (tenant={})", user.username, user.tenant_id)
    return {"success": True, "user_id": user.user_id, "message": "注册成功"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Refresh access token."""
    access_token_expires = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": current_user["sub"],
            "user_id": current_user.get("user_id"),
            "tenant_id": current_user.get("tenant_id", "default"),
            "role": current_user.get("role", "user"),
        },
        expires_delta=access_token_expires,
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me")
async def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get current logged-in user info."""
    return {
        "username": current_user.get("sub"),
        "user_id": current_user.get("user_id"),
        "tenant_id": current_user.get("tenant_id", "default"),
        "role": current_user.get("role"),
        "exp": current_user.get("exp"),
    }
