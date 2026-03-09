"""
Pydantic request / response models for the serve HTTP API.
"""

from typing import Optional
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"


class ChatResponse(BaseModel):
    response: str
    user_id: str


class ImageChatResponse(BaseModel):
    response: str
    user_id: str


class HealthResponse(BaseModel):
    status: str
    version: str
    voice_enabled: bool
    agent_type: str
    active_sessions: int


class LoginRequest(BaseModel):
    username: str
    password: str
    tenant_id: str = "default"


class RegisterRequest(BaseModel):
    username: str
    password: str
    tenant_id: str = "default"


class LoginResponse(BaseModel):
    success: bool
    access_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = 3600
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    role: Optional[str] = None
    message: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
