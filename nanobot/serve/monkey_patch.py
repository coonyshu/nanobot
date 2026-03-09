"""
Subagent monkey-patching and dynamic tool injection hooks.
"""

import inspect

from loguru import logger

from .state import ServiceState, is_subagent_context


def setup_monkey_patch(agent_loop, tenant_pool, action_manager, svc: ServiceState):
    """
    Patch the default *agent_loop*'s subagent manager so that:
    - Dynamic tools are inherited from the current tenant's AgentLoop.
    - The ``is_subagent_context`` ContextVar is set during subagent runs.

    Also sets up the ``before_execute_hook`` and realtime camera callback.
    """
    from nanobot.multi_tenant.agent_pool import current_tenant_id
    from .callbacks import agent_image_callback, send_subagent_message_to_user

    # -- Monkey-patch _run_subagent --------------------------------------------

    original_run_subagent = agent_loop.subagents._run_subagent

    async def patched_run_subagent(task_id, task, label, origin):
        original_method = (
            original_run_subagent.__func__
            if hasattr(original_run_subagent, "__func__")
            else original_run_subagent
        )

        from nanobot.agent.tools.registry import ToolRegistry as OriginalToolRegistry

        class PatchedToolRegistry(OriginalToolRegistry):
            def __init__(self):
                super().__init__()
                tid = current_tenant_id.get()
                active_action_mgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                active_loop = tenant_pool._pool.get(tid, agent_loop)
                if hasattr(active_loop, "tools"):
                    for tool_name in active_action_mgr._dynamic_tools.keys():
                        if tool_name in active_loop.tools._tools:
                            self._tools[tool_name] = active_loop.tools._tools[tool_name]
                            logger.debug("Injected dynamic tool '{}' into subagent (tenant={})", tool_name, tid)

        import nanobot.agent.subagent as subagent_module

        original_registry_class = subagent_module.ToolRegistry
        subagent_module.ToolRegistry = PatchedToolRegistry

        token = is_subagent_context.set(True)
        try:
            await original_method(agent_loop.subagents, task_id, task, label, origin)
        finally:
            subagent_module.ToolRegistry = original_registry_class
            is_subagent_context.reset(token)

    agent_loop.subagents._run_subagent = patched_run_subagent
    logger.info("Subagent manager patched to inject dynamic tools and set context flag")

    # -- Link ToolRegistry to default ActionManager ----------------------------

    action_manager.set_registry(agent_loop.tools)
    logger.info("ActionManager linked to AgentLoop ToolRegistry")

    # -- Realtime camera callback ----------------------------------------------

    async def on_camera_photo_realtime(user_id: str, tool_name: str, result_data: dict):
        try:
            image_b64 = result_data.get("image_b64", "")
            mime_type = result_data.get("mime_type", "image/jpeg")
            text = result_data.get("text", "")
            if not image_b64:
                return

            if not is_subagent_context.get():
                logger.debug("Skipping realtime callback for main agent call: {}", tool_name)
                return

            logger.info("Realtime camera callback (subagent): user={}, tool={}", user_id, tool_name)

            if text and "用途：" in text:
                purpose = text.split("用途：", 1)[1] if "用途：" in text else text
                prompt = f"根据任务要求分析这张照片：{purpose}"
            else:
                prompt = text or "描述这张照片"

            from nanobot.config.loader import load_config as _lc
            cfg = _lc()
            response = await agent_image_callback(
                user_id, prompt, image_b64, mime_type,
                provider=agent_loop.provider, model=cfg.agents.defaults.model,
            )
            await send_subagent_message_to_user(user_id, response, svc=svc)
        except Exception as e:
            logger.error("Realtime camera callback error: {}", e, exc_info=True)

    action_manager.set_realtime_callback(on_camera_photo_realtime)
    logger.info("ActionManager realtime callback registered for camera photos")

    # -- Before-execute hook ---------------------------------------------------

    def before_tool_execute_hook():
        try:
            for frame_info in inspect.stack():
                frame_locals = frame_info.frame.f_locals
                if "session_key" in frame_locals:
                    session_key = frame_locals["session_key"]
                    if isinstance(session_key, str) and session_key.startswith("voice:"):
                        uid = session_key.split(":", 1)[1]
                        tid = current_tenant_id.get()
                        tenant_action_mgr = tenant_pool.get_action_manager_safe(tid) or action_manager
                        tenant_action_mgr.set_user_context(uid)
                        logger.debug("Before-execute hook: updated user context to {} for tenant={}", uid, tid)
                        break
        except Exception as e:
            logger.warning("Before-execute hook failed: {}", e, exc_info=True)

    action_manager.set_before_execute_hook(before_tool_execute_hook)
    logger.info("ActionManager before-execute hook registered")
