"""JWT authentication with tenant support."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

# ── configuration ──

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("JWT_SECRET", "nanobot-web-secret"))
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
WS_AUTH_MODE = os.getenv("WS_AUTH_MODE", "optional")

security = HTTPBearer(auto_error=False)


# ── token helpers ──

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT with *tenant_id* in the payload."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify a JWT. Returns payload dict or ``None``."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid token: {}", e)
        return None


# ── FastAPI dependencies ──

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """HTTP dependency: extract and verify JWT, returning the full payload."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def verify_websocket_token(websocket: WebSocket) -> Optional[Dict[str, Any]]:
    """Extract JWT from WebSocket query-param or Authorization header."""
    token = websocket.query_params.get("token")
    logger.debug("WebSocket token from query_params: present={}", bool(token))
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        logger.warning("Token verification failed")
    return payload


def extract_tenant_id(token_payload: dict | None) -> str:
    """Return tenant_id from a JWT payload, defaulting to ``'default'``."""
    if token_payload:
        return token_payload.get("tenant_id", "default")
    return "default"
