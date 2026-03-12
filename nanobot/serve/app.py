"""
FastAPI application factory and lifespan management.

``create_app()`` supports two modes:

* **Embedded** (called from ``nanobot gateway``): receives pre-created
  *agent_loop*, *bus*, *provider*, *config*.  The gateway command owns
  the event loop and starts uvicorn as an ``asyncio.Server``.

* **Standalone** (``python -m nanobot.serve.app``): creates everything
  internally.
"""

import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from loguru import logger

from .middleware import setup_logging
from .state import ServiceState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(config):
    """Create a LiteLLM provider from *config*."""
    from nanobot.providers.litellm_provider import LiteLLMProvider

    model = config.agents.defaults.model
    p = config.get_provider(model)
    return LiteLLMProvider(
        api_key=p.api_key if p else os.getenv("OPENAI_API_KEY"),
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(model),
    )


def _create_agent_loop(bus, provider, config, workspace, agents_dirs=None):
    """Create the default AgentLoop."""
    from nanobot.agent.agent_context import AgentContext
    from nanobot.agent.loop import AgentLoop

    agent_context = AgentContext(
        provider=provider,
        workspace=workspace,
        bus=bus,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=(
            config.tools.web.search.api_key
            if hasattr(config.tools.web, "search")
            else None
        ),
        web_proxy=config.tools.web.proxy if hasattr(config.tools.web, "proxy") else None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=(
            config.tools.mcp_servers
            if hasattr(config.tools, "mcp_servers")
            else None
        ),
        agents_dirs=agents_dirs,
    )

    return AgentLoop(
        agent_context=agent_context,
        channels_config=config.channels,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(
    *,
    agent_loop=None,
    bus=None,
    provider=None,
    config=None,
    workspace: Optional[Path] = None,
    skills_dir: Optional[Path] = None,
):
    """
    Build and return a fully-configured FastAPI application.

    Parameters that are *None* will be created internally (standalone mode).
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import RedirectResponse, FileResponse

    # -- Logging ---------------------------------------------------------------
    setup_logging()

    # -- Config ----------------------------------------------------------------
    if config is None:
        from nanobot.config.loader import load_config
        from nanobot.config.schema import Config

        try:
            config = load_config()
            logger.info("Loaded nanobot config, model: {}", config.agents.defaults.model)
        except Exception as e:
            logger.warning("Failed to load nanobot config: {}, using defaults", e)
            config = Config()

    # -- Data dir (must be early – used by skills_dir default) ----------------
    data_dir = Path.home() / ".nanobots"

    # -- Bus / Provider / AgentLoop -------------------------------------------
    if bus is None:
        from nanobot.bus.queue import MessageBus
        bus = MessageBus()

    if provider is None:
        provider = _make_provider(config)

    if workspace is None:
        workspace = Path(__file__).parent.parent  # nanobot-fork/nanobot/
    if skills_dir is None:
        skills_dir = data_dir / "skills"
    
    global_agents_dir = workspace.parent.parent / "agents"
    logger.debug("global_agents_dir: {} (is_dir={})", global_agents_dir, global_agents_dir.is_dir() if global_agents_dir.exists() else "N/A")
    if not global_agents_dir.is_dir():
        global_agents_dir = None

    if agent_loop is None:
        logger.debug("Creating AgentLoop with agents_dirs={}", [global_agents_dir] if global_agents_dir else None)
        agent_loop = _create_agent_loop(bus, provider, config, workspace, 
                                        agents_dirs=[global_agents_dir] if global_agents_dir else None)
        logger.info("AgentLoop initialized with model: {}", config.agents.defaults.model)

    # -- Multi-tenant infra ----------------------------------------------------
    from nanobot.tenant.workspace_resolver import WorkspaceResolver
    from nanobot.tenant.tenant_store import TenantStore
    from nanobot.tenant.user_store import UserStore
    from nanobot.tenant.agent_pool import TenantAgentPool
    from nanobot.tenant.migration import (
        needs_migration, run_migration,
        needs_tenants_dir_migration, run_tenants_dir_migration,
        needs_service_data_migration, run_service_data_migration,
    )
    from nanobot.service_tools import action_manager
    resolver = WorkspaceResolver(data_dir)
    tenant_store = TenantStore(data_dir / "tenants.json", resolver)
    user_store = UserStore(data_dir / "users.json", resolver)

    # Migrations
    old_service_data = Path(__file__).parent.parent.parent / "nanobot-service" / "data"
    if needs_service_data_migration(data_dir, old_service_data):
        run_service_data_migration(data_dir, old_service_data)
        logger.info("Service data migration to ~/.nanobots completed")
    if needs_migration(data_dir):
        run_migration(data_dir, workspace, resolver, tenant_store, user_store)
        logger.info("Legacy data migration completed")
    if needs_tenants_dir_migration(data_dir):
        run_tenants_dir_migration(data_dir, resolver)
        logger.info("Tenants directory migration completed")

    # Tenant pool
    tenant_pool = TenantAgentPool(
        provider=provider,
        bus=bus,
        global_config=config,
        tenant_store=tenant_store,
        resolver=resolver,
        global_skills_dir=skills_dir,
        global_agents_dir=global_agents_dir,
    )
    tenant_pool.register_loop("default", agent_loop, action_manager)
    logger.info("TenantAgentPool created, default tenant loop registered")

    # -- Monkey-patching -------------------------------------------------------
    svc = ServiceState()
    # Allow TenantAgentPool to re-register ws_senders when creating new tenant loops
    tenant_pool._session_registry = svc.active_voice_sessions
    # Set tenant_pool and action_manager references for global access
    svc.tenant_pool = tenant_pool
    svc.action_manager = action_manager
    # Set global instance
    ServiceState.set_instance(svc)

    from .monkey_patch import setup_monkey_patch
    setup_monkey_patch(agent_loop, tenant_pool, action_manager, svc)

    # -- Voice module ----------------------------------------------------------
    voice_handler = None
    voice_config = None
    session_manager = None

    try:
        from nanobot.voice import (
            VoiceWebSocketHandler,
            VoiceConfig,
            VoiceSessionManager,
            get_voice_config,
        )

        voice_config = get_voice_config(config)
        session_manager = VoiceSessionManager(session_timeout=voice_config.session_timeout)

        if voice_config.enabled and voice_config.validate():
            # Build session register/unregister callbacks that close over svc
            def _make_register_cb():
                from nanobot.tenant.agent_pool import current_tenant_id as _cti

                def register_cb(user_id, session_id, websocket, session):
                    tid = _cti.get()
                    amgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                    svc.register_voice_session(user_id, session_id, websocket, session, tid, amgr)

                def unregister_cb(user_id, session_id):
                    sid_data = svc.active_voice_sessions.get(session_id, {})
                    tid = sid_data.get("tenant_id", "default")
                    amgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                    svc.unregister_voice_session(user_id, session_id, amgr)

                return register_cb, unregister_cb

            reg_cb, unreg_cb = _make_register_cb()

            # Extra WS message handler
            async def _handle_extra_ws_message(user_id, data):
                from nanobot.tenant.agent_pool import current_tenant_id as _cti

                msg_type = data.get("type", "")
                sid, sd = svc.get_user_session(user_id)
                tid = sd.get("tenant_id", "default") if sd else _cti.get()

                logger.debug("Extra WS message: type={}, user_id={}, tenant_id={}, session_found={}, session_id={}",
                             msg_type, user_id, tid, sd is not None, sid)

                if msg_type == "register_tools":
                    await tenant_pool.get_or_create_loop(tid)
                    t_mgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                    descriptors = data.get("descriptors", [])
                    registered = t_mgr.register_from_descriptors(user_id, descriptors)
                    logger.info("Frontend registered tools for tenant={}: {}", tid, registered)
                    if sd and "ws_send_json" in sd:
                        sender = sd["ws_send_json"]
                        t_mgr.register_ws_sender(sid or user_id, sender)
                        t_mgr.register_ws_sender(user_id, sender)
                        logger.info("Re-registered ws_sender to tenant action_manager: user_id={}, session_id={}, tenant={}",
                                    user_id, sid, tid)
                    else:
                        logger.warning("register_tools: session data not found or missing ws_send_json for user_id={}, "
                                       "active_sessions={}", user_id, list(svc.active_voice_sessions.keys()))
                    # 发送确认
                    if sd and "ws_send_json" in sd:
                        try:
                            await sd["ws_send_json"]({"type": "tools_registered", "tools": registered})
                        except Exception as _e:
                            logger.warning("Failed to send tools_registered confirmation: {}", _e)
                    return

                t_mgr = tenant_pool.get_action_manager_safe(tid) or action_manager

                if msg_type == "action_result":
                    t_mgr.resolve(data.get("action_id", ""), data.get("success", False), data.get("result", ""))
                elif msg_type == "context_update":
                    svc.update_tab_context(user_id, data.get("context", {}))

            # Build agent_callback / agent_image_callback wrappers
            from .callbacks import (
                agent_callback as _agent_cb,
                agent_image_callback as _agent_img_cb,
            )

            async def voice_agent_cb(user_id, message, enable_streaming=True):
                return await _agent_cb(
                    user_id, message,
                    svc=svc, tenant_pool=tenant_pool, action_manager=action_manager,
                    enable_streaming=enable_streaming,
                )

            async def voice_agent_img_cb(user_id, message, image_b64, mime_type="image/jpeg"):
                return await _agent_img_cb(
                    user_id, message, image_b64, mime_type,
                    provider=provider, model=config.agents.defaults.model,
                )

            voice_handler = VoiceWebSocketHandler(
                config=VoiceConfig(
                    asr_provider=voice_config.asr.provider,
                    tts_provider=voice_config.tts.provider,
                    asr_access_key_id=voice_config.asr.access_key_id,
                    asr_access_key_secret=voice_config.asr.access_key_secret,
                    asr_appkey=voice_config.asr.appkey,
                    asr_host=voice_config.asr.host,
                    tts_access_key_id=voice_config.tts.access_key_id,
                    tts_access_key_secret=voice_config.tts.access_key_secret,
                    tts_appkey=voice_config.tts.appkey,
                    tts_host=voice_config.tts.host,
                    tts_voice=voice_config.tts.voice,
                    tts_volume=voice_config.tts.volume,
                    tts_speech_rate=voice_config.tts.speech_rate,
                    vad_enabled=voice_config.vad.enabled,
                    vad_model_path=voice_config.vad.model_path,
                    vad_threshold=voice_config.vad.threshold,
                    sample_rate=voice_config.sample_rate,
                    audio_format=voice_config.audio_format,
                ),
                session_manager=session_manager,
                agent_callback=voice_agent_cb,
                agent_image_callback=voice_agent_img_cb,
                extra_message_handler=_handle_extra_ws_message,
                session_register_callback=reg_cb,
                session_unregister_callback=unreg_cb,
            )
            logger.info("Voice WebSocket handler initialized")
        else:
            logger.warning("Voice module disabled or configuration invalid")
    except ImportError as e:
        logger.info("Voice module not available (missing dependencies): {}", e)

    # -- Lifespan --------------------------------------------------------------
    @asynccontextmanager
    async def lifespan(app):
        logger.info("Starting nanobot serve (model={})", config.agents.defaults.model)

        from .outbound import consume_outbound_messages
        outbound_task = asyncio.create_task(consume_outbound_messages(bus, svc))

        yield

        logger.info("Shutting down...")
        outbound_task.cancel()
        try:
            await outbound_task
        except asyncio.CancelledError:
            pass
        if session_manager:
            await session_manager.cleanup_expired_sessions()
        await tenant_pool.close_all()

    # -- Build FastAPI app -----------------------------------------------------
    app = FastAPI(
        title="nanobot-service",
        description="AI 智能助理服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static files
    static_path = Path(__file__).parent.parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Root route
    @app.get("/")
    async def root():
        html_path = static_path / "index.html"
        if html_path.exists():
            return FileResponse(str(html_path), media_type="text/html")
        return {"status": "ok", "service": "nanobot-service"}

    # -- Store everything on app.state -----------------------------------------
    app.state.svc = svc
    app.state.bus = bus
    app.state.provider = provider
    app.state.nanobot_config = config
    app.state.agent_loop = agent_loop
    app.state.tenant_pool = tenant_pool
    app.state.tenant_store = tenant_store
    app.state.user_store = user_store
    app.state.resolver = resolver
    app.state.action_manager = action_manager
    app.state.voice_handler = voice_handler
    app.state.voice_config = voice_config
    app.state.session_manager = session_manager
    app.state.data_dir = data_dir
    app.state.skills_dir = skills_dir

    # -- Register routers ------------------------------------------------------
    from .routes.health import router as health_router
    from .routes.auth import router as auth_router
    from .routes.chat import router as chat_router
    from .routes.proxy import router as proxy_router
    from .websocket.voice_ws import router as voice_ws_router
    from .websocket.chat_ws import router as chat_ws_router

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    # Proxy business routes to NestJS backend (must be registered before plugin routes)
    app.include_router(proxy_router)
    app.include_router(voice_ws_router)
    app.include_router(chat_ws_router)

    # -- Load plugin routes (all tenants + system-level) -----------------------
    from nanobot.agent.plugins import PluginLoader
    plugin_route_dirs = []
    # Include every tenant's skills directory
    tenants_root = data_dir / "tenants"
    if tenants_root.is_dir():
        for entry in sorted(tenants_root.iterdir()):
            t_skills = entry / "skills"
            if entry.is_dir() and t_skills.is_dir():
                plugin_route_dirs.append(t_skills)
    # System-level skills last (lowest priority)
    plugin_route_dirs.append(skills_dir)
    plugin_loader = PluginLoader(plugin_route_dirs)
    loaded_routes = plugin_loader.load_routes(app)
    if loaded_routes:
        logger.info("Plugin routes loaded: {}", loaded_routes)

    return app


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from pathlib import Path as _P
    from nanobot.config.loader import load_config

    setup_logging()

    _cfg = load_config()
    app = create_app(config=_cfg)
    web_cfg = _cfg.gateway.web

    host = os.getenv("HOST", "0.0.0.0")
    port = web_cfg.port
    use_https = web_cfg.https
    ssl_certfile = web_cfg.ssl_certfile or ""
    ssl_keyfile = web_cfg.ssl_keyfile or ""

    # Default cert paths under nanobot/data/certs/
    if use_https and not ssl_certfile:
        default_certs = _P(__file__).parent.parent / "data" / "certs"
        ssl_certfile = str(default_certs / "cert.pem")
        ssl_keyfile = str(default_certs / "key.pem")

    uvi_kwargs = dict(host=host, port=port)
    if use_https:
        uvi_kwargs["ssl_certfile"] = str(_P(ssl_certfile).expanduser())
        uvi_kwargs["ssl_keyfile"] = str(_P(ssl_keyfile).expanduser())

    uvicorn.run(app, **uvi_kwargs)
