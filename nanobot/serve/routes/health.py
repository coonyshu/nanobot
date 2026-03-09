"""
Health check endpoint.
"""

from fastapi import APIRouter, Request

from ..models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    """Health check endpoint."""
    svc = request.app.state.svc
    voice_handler = request.app.state.voice_handler
    session_manager = request.app.state.session_manager

    return HealthResponse(
        status="healthy",
        version="0.1.0",
        voice_enabled=voice_handler is not None,
        agent_type="AgentLoop",
        active_sessions=session_manager.active_session_count if session_manager else 0,
    )
