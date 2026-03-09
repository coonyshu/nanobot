"""
Generic reverse-proxy route.

Forwards /api/v1/workflow/* to the NestJS business backend,
keeping nanobots-ai free of business logic.

Backend URL is resolved in order:
  1. NANOBOT_BACKEND_URL environment variable
  2. config.backend_url field (if present)
  3. Default: http://localhost:3001
"""

import os

import httpx
from fastapi import APIRouter, Request, Response
from loguru import logger

router = APIRouter()



def _backend_url(request: Request) -> str:
    """Resolve backend base URL from env, config, or default."""
    env = os.getenv("NANOBOT_BACKEND_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        cfg = request.app.state.nanobot_config
        url = getattr(cfg, "backend_url", None)
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:3000"


@router.api_route(
    "/api/v1/workflow/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_workflow(path: str, request: Request) -> Response:
    """Proxy /api/v1/workflow/* → NestJS backend."""
    body_bytes = await request.body()
    content_len = len(body_bytes)
    if "photo" in path:
        logger.info("Proxy [photo] {} /api/v1/workflow/{} body_len={}", request.method, path, content_len)
    # Re-attach body for _forward to read
    async def _body_override() -> bytes:
        return body_bytes
    request._body = body_bytes  # starlette caches body after first read
    return await _forward(f"/api/v1/workflow/{path}", request)


async def _forward(target_path: str, request: Request) -> Response:
    """Forward *request* to the NestJS backend at *target_path*."""
    base = _backend_url(request)
    url = f"{base}{target_path}"
    qs = request.url.query
    if qs:
        url = f"{url}?{qs}"

    # Forward headers, strip hop-by-hop and host
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"host", "connection", "transfer-encoding", "te", "trailer", "upgrade"}
    }

    try:
        body = await request.body()
    except Exception:
        body = b""

    logger.debug("Proxy → {} {} → {}", request.method, request.url.path, url)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
        # Strip hop-by-hop response headers
        resp_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in {"transfer-encoding", "connection"}
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type"),
        )
    except httpx.ConnectError as e:
        logger.warning("Proxy connect error to {}: {}", url, e)
        return Response(
            content=b'{"success":false,"error":"Business backend unavailable"}',
            status_code=503,
            media_type="application/json",
        )
    except Exception as e:
        logger.error("Proxy error for {}: {}", url, e)
        return Response(
            content=b'{"success":false,"error":"Proxy error"}',
            status_code=502,
            media_type="application/json",
        )
