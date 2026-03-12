"""
前端动作管理器 - 动态版本。

前端通过 WebSocket 发送 descriptors 描述自身能力，
后端据此动态生成 Tool 注册到 nanobot AgentLoop。

参考 xiaozhi-esp32-server 的 IoT 设备动态注册机制。

协议：
  前端 → 后端:
    注册:   {"type": "register_tools", "descriptors": [...]}
    结果:   {"type": "action_result", "action_id": "...", "success": true, "result": "..."}

  后端 → 前端:
    派发:   {"type": "action", "action_id": "...", "name": "...", "params": {...}}

  descriptor 格式（参考 xiaozhi IoT descriptor）:
    {
      "name": "take_photo",
      "description": "控制设备摄像头拍照",
      "properties": {                          # 可选，可查询的属性
        "camera_facing": {
          "type": "string",
          "description": "当前摄像头方向"
        }
      },
      "methods": {                             # 可调用的方法
        "capture": {
          "description": "拍一张照片",
          "parameters": {
            "purpose": {
              "type": "string",
              "description": "拍照用途说明"
            }
          }
        }
      }
    }
"""

import asyncio
import uuid
import json
import base64
import mimetypes
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional

from loguru import logger
from nanobot.agent.tools.base import Tool


# ────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────

@dataclass
class PendingAction:
    """等待前端执行的动作。"""
    action_id: str
    name: str
    params: dict[str, Any]
    future: asyncio.Future
    user_id: str


class DynamicFrontendTool(Tool):
    """
    由前端描述符动态生成的工具。

    每个 descriptor method 会生成一个 Tool 实例，
    execute 时通过 ActionManager 派发到前端。
    """

    def __init__(
        self,
        tool_name: str,
        tool_description: str,
        tool_parameters: dict[str, Any],
        action_manager: "ActionManager",
    ):
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters
        self._action_manager = action_manager
        self._user_id: str = "default"
        # Hook 函数，在执行前调用
        self._before_execute_hook: Optional[Callable[[], None]] = None

    # ── Tool 基类抽象属性 ──

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    # ── 执行 ──

    def set_user_context(self, user_id: str) -> None:
        self._user_id = user_id

    def set_before_execute_hook(self, hook: Callable[[], None]) -> None:
        """设置执行前的 hook 函数"""
        self._before_execute_hook = hook

    async def execute(self, **kwargs: Any) -> str:
        # 执行前调用 hook（用于更新用户上下文等）
        if self._before_execute_hook:
            try:
                self._before_execute_hook()
            except Exception as e:
                logger.warning(f"Before execute hook failed: {e}")
        
        raw = await self._action_manager.dispatch(
            user_id=self._user_id,
            action_name=self._name,
            params=kwargs,
        )
        # 尝试解析 JSON 格式的结果（前端可能返回带图片的结构化数据）
        result = self._process_result(raw)
        
        # 检查是否需要实时通知（由调用者通过参数控制）
        realtime_notify = kwargs.get("realtime_notify", False)
        
        if realtime_notify and self._action_manager._realtime_callback:
            try:
                # 解析结果，检查是否包含图片等多模态数据
                result_data = json.loads(result)
                if isinstance(result_data, dict) and result_data.get("__multimodal__"):
                    logger.info(f"Triggering realtime callback for {self._name} (user={self._user_id})")
                    # 异步触发回调，不阻塞工具执行
                    asyncio.create_task(
                        self._action_manager._realtime_callback(
                            self._user_id,
                            self._name,
                            result_data
                        )
                    )
                else:
                    logger.debug(f"Result is not multimodal, skipping realtime callback")
            except Exception as e:
                logger.warning(f"Realtime callback failed: {e}", exc_info=True)
        
        return result

    def _process_result(self, raw: str) -> str:
        """
        处理前端返回的结果。
        
        如果结果是 JSON 且包含 image 字段，将图片保存为临时文件，
        返回多模态格式的 content（list），使 LLM 能看到图片。
        
        普通文本结果直接返回。
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

        if not isinstance(data, dict) or "image" not in data:
            return raw

        text = data.get("text", "操作完成")
        image_b64 = data["image"]
        mime_type = data.get("mime_type", "image/jpeg")

        # 返回包含图片的 JSON 标记，供 agent loop 的 add_tool_result 识别
        return json.dumps({
            "__multimodal__": True,
            "text": text,
            "image_b64": image_b64,
            "mime_type": mime_type,
        }, ensure_ascii=False)


# ────────────────────────────────────────────────────
# ActionManager
# ────────────────────────────────────────────────────

class ActionManager:
    """
    前端动作管理器。

    职责：
    1. 接收前端 descriptors → 动态生成 Tool → 注册到 ToolRegistry
    2. 派发动作到前端（WebSocket）
    3. 管理 pending futures，等待前端结果
    4. 前端断开时自动注销工具
    """

    def __init__(self, timeout: float = 30.0):
        self._pending: dict[str, PendingAction] = {}
        self._ws_senders: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._timeout = timeout
        # user_id → 该用户注册的动态工具名列表
        self._user_tools: dict[str, list[str]] = {}
        # 所有动态工具实例（工具名 → Tool）
        self._dynamic_tools: dict[str, DynamicFrontendTool] = {}
        # 外部注入的 ToolRegistry 引用
        self._registry: Optional[Any] = None
        # 实时回调函数（用于 subagent 场景下的实时通知）
        self._realtime_callback: Optional[Callable[[str, str, dict], Awaitable[None]]] = None
        # 工具执行前 hook（用于更新用户上下文）
        self._before_execute_hook: Optional[Callable[[], None]] = None

    def set_registry(self, registry) -> None:
        """注入 nanobot ToolRegistry，用于动态注册/注销工具。"""
        self._registry = registry

    def set_realtime_callback(self, callback: Callable[[str, str, dict], Awaitable[None]]) -> None:
        """
        设置实时回调函数，在工具执行完成时触发。
        
        Args:
            callback: 回调函数，签名为 async def(user_id: str, tool_name: str, result_data: dict)
        """
        self._realtime_callback = callback

    def set_before_execute_hook(self, hook: Callable[[], None]) -> None:
        """
        为所有动态工具设置执行前 hook。
        
        这个 hook 会在每个工具执行前调用，用于更新上下文等操作。
        特别适用于 subagent 场景，确保工具能访问到正确的用户上下文。
        
        Args:
            hook: Hook 函数，签名为 def() -> None
        """
        self._before_execute_hook = hook
        # 为已存在的工具设置 hook
        for tool in self._dynamic_tools.values():
            tool.set_before_execute_hook(hook)

    # ── WebSocket 管理 ──

    def register_ws_sender(self, user_id: str, sender: Callable[[dict], Awaitable[None]]) -> None:
        self._ws_senders[user_id] = sender
        logger.info(f"ActionManager: registered WS sender for user={user_id}")

    def unregister_ws_sender(self, user_id: str) -> None:
        self._ws_senders.pop(user_id, None)
        # 取消 pending 动作
        for aid, action in list(self._pending.items()):
            if action.user_id == user_id and not action.future.done():
                action.future.set_result("错误：用户已断开连接")
                del self._pending[aid]
        # 注销该用户注册的所有工具
        self._unregister_user_tools(user_id)
        logger.info(f"ActionManager: unregistered WS sender for user={user_id}")

    # ── 动态工具注册 ──

    def register_from_descriptors(
        self,
        user_id: str,
        descriptors: list[dict[str, Any]],
    ) -> list[str]:
        """
        根据前端发送的 descriptors 动态生成 Tool 并注册。

        descriptor 格式:
          {
            "name": "camera",
            "description": "摄像头控制",
            "agent": "workflow-inspector",  // 可选，指定工具只对该 subagent 可见
            "methods": {
              "take_photo": {
                "description": "拍照",
                "parameters": {
                  "purpose": {"type":"string","description":"拍照用途"}
                }
              }
            }
          }

        生成的工具名格式: {device_name}_{method_name}
        如: camera_take_photo

        Returns:
            注册的工具名列表
        """
        if not self._registry:
            logger.error("ActionManager: ToolRegistry not set, cannot register tools")
            return []

        # 先清理该用户之前注册的工具
        self._unregister_user_tools(user_id)

        registered: list[str] = []
        agent_tools: dict[str, list[str]] = {}  # agent_name -> tool_names

        for desc in descriptors:
            device_name = desc.get("name", "").lower()
            device_desc = desc.get("description", device_name)
            agent_name = desc.get("agent")  # 可选：指定工具只对该 agent 可见

            # 注册方法型工具（控制）
            methods = desc.get("methods", {})
            for method_name, method_info in methods.items():
                tool_name = f"{device_name}_{method_name}".lower()
                tool_description = f"{device_desc} - {method_info.get('description', method_name)}"

                # 构建参数 schema
                raw_params = method_info.get("parameters", {})
                properties = {}
                required = []
                for param_name, param_info in raw_params.items():
                    properties[param_name] = {
                        "type": param_info.get("type", "string"),
                        "description": param_info.get("description", param_name),
                    }
                    if "enum" in param_info:
                        properties[param_name]["enum"] = param_info["enum"]
                    if param_info.get("required", False):
                        required.append(param_name)

                tool_parameters = {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }

                tool = DynamicFrontendTool(
                    tool_name=tool_name,
                    tool_description=tool_description,
                    tool_parameters=tool_parameters,
                    action_manager=self,
                )
                tool.set_user_context(user_id)
                
                # 设置执行前 hook（如果已配置）
                if self._before_execute_hook:
                    tool.set_before_execute_hook(self._before_execute_hook)

                # 注册到 ToolRegistry
                self._registry.register(tool)
                self._dynamic_tools[tool_name] = tool
                registered.append(tool_name)
                
                # 如果指定了 agent，记录到 agent_tools
                if agent_name:
                    agent_tools.setdefault(agent_name, []).append(tool_name)

            # 注册属性查询工具
            properties_def = desc.get("properties", {})
            for prop_name, prop_info in properties_def.items():
                tool_name = f"get_{device_name}_{prop_name}".lower()
                tool_description = f"查询{device_desc}的{prop_info.get('description', prop_name)}"

                tool_parameters = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }

                tool = DynamicFrontendTool(
                    tool_name=tool_name,
                    tool_description=tool_description,
                    tool_parameters=tool_parameters,
                    action_manager=self,
                )
                tool.set_user_context(user_id)
                
                # 设置执行前 hook（如果已配置）
                if self._before_execute_hook:
                    tool.set_before_execute_hook(self._before_execute_hook)

                self._registry.register(tool)
                self._dynamic_tools[tool_name] = tool
                registered.append(tool_name)
                
                # 如果指定了 agent，记录到 agent_tools
                if agent_name:
                    agent_tools.setdefault(agent_name, []).append(tool_name)

        # 隐藏指定了 agent 的工具，使其只对 subagent 可见
        all_agent_tools = []
        for agent_name, tool_names in agent_tools.items():
            all_agent_tools.extend(tool_names)
        
        if all_agent_tools and self._registry:
            self._registry.hide_from_llm(*all_agent_tools)
            logger.info(
                f"ActionManager: hidden {len(all_agent_tools)} tools from main agent (visible only to subagents): {all_agent_tools}"
            )

        self._user_tools[user_id] = registered
        logger.info(
            f"ActionManager: registered {len(registered)} tools for user={user_id}: {registered}"
        )
        return registered

    def _unregister_user_tools(self, user_id: str) -> None:
        """注销指定用户注册的所有动态工具。"""
        tool_names = self._user_tools.pop(user_id, [])
        for name in tool_names:
            self._dynamic_tools.pop(name, None)
            if self._registry:
                self._registry.unregister(name)
        if tool_names:
            logger.info(
                f"ActionManager: unregistered {len(tool_names)} tools for user={user_id}"
            )

    def set_user_context(self, user_id: str) -> None:
        """更新所有动态工具的用户上下文。"""
        for tool in self._dynamic_tools.values():
            tool.set_user_context(user_id)

    # ── 动作派发 ──

    async def dispatch(self, user_id: str, action_name: str, params: dict[str, Any]) -> str:
        sender = self._ws_senders.get(user_id)
        if not sender:
            logger.warning("ActionManager.dispatch: no ws_sender for user={}, action={}, registered_senders={}",
                           user_id, action_name, list(self._ws_senders.keys()))
            return f"错误：用户 {user_id} 未连接，无法执行 {action_name}"

        action_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        self._pending[action_id] = PendingAction(
            action_id=action_id,
            name=action_name,
            params=params,
            future=future,
            user_id=user_id,
        )

        message = {
            "type": "action",
            "action_id": action_id,
            "name": action_name,
            "params": params,
        }
        try:
            await sender(message)
            logger.info(f"Action dispatched: {action_name}({action_id}) → user={user_id}")
        except Exception as e:
            self._pending.pop(action_id, None)
            return f"错误：发送动作失败: {e}"

        try:
            result = await asyncio.wait_for(future, timeout=self._timeout)
            logger.info(f"Action completed: {action_name}({action_id})")
            return result
        except asyncio.TimeoutError:
            self._pending.pop(action_id, None)
            return f"错误：{action_name} 执行超时（{self._timeout}秒）"
        except Exception as e:
            self._pending.pop(action_id, None)
            return f"错误：{action_name} 执行失败: {e}"

    def resolve(self, action_id: str, success: bool, result: str) -> bool:
        pending = self._pending.pop(action_id, None)
        if not pending:
            logger.warning(f"Action not found: {action_id}")
            return False
        if pending.future.done():
            return False
        if success:
            pending.future.set_result(result)
        else:
            pending.future.set_result(f"执行失败: {result}")
        return True

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def registered_tool_names(self) -> list[str]:
        return list(self._dynamic_tools.keys())


# 全局单例
action_manager = ActionManager(timeout=30.0)
