import base64
import tempfile
from pathlib import Path

from loguru import logger

from nanobot.serve.context_resolver import ContextResolver


class AgentImageProcessor:
    def __init__(self, *, provider, model: str, svc=None):
        self.provider = provider
        self.model = model
        self.svc = svc
        self._tenant_id: str | None = None

    async def process(
        self,
        user_id: str,
        message: str,
        image_b64: str,
        mime_type: str = "image/jpeg",
    ) -> tuple[str, str | None]:
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        suffix = ext_map.get(mime_type, ".jpg")
        tmp_path = None
        agent_name = None

        try:
            agent_name, session = self._resolve_agent_context(user_id)
            prompt = self._build_prompt(
                user_id=user_id,
                message=message,
                agent_name=agent_name,
                session=session,
            )
            logger.info("[ImageProcessor] agent_name={}, prompt={}", agent_name, prompt)

            image_data = base64.b64decode(image_b64)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(image_data)
                tmp_path = tmp.name

            system_prompt = (
                "你是一个图片识别助手。请根据用户的问题和要求来回复：\n"
                "- 如果用户要求简洁回答（如“只回答有人/没有人”），请严格按要求回复，"
                "不要添加额外内容\n"
                "- 如果用户要求详细描述，请提供完整的图片分析\n"
                "- 如果用户没有明确要求，请简洁准确地回答问题\n"
                "- 如果用户要求在回复末尾输出JSON代码块，请严格按照指定格式输出，确保JSON语法正确；"
                "无法识别的字段填null，布尔值用true/false，数字不加引号"
            )

            content_parts = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ]

            response = await self.provider.chat(
                messages=messages,
                tools=[],
                model=self.model,
                temperature=0.7,
            )
            result = response.content or "抱歉，我无法识别这张图片。"
            return result, agent_name
        except Exception as e:
            logger.error("Agent image error: {}", e, exc_info=True)
            return "抱歉，图片识别遇到了一些问题，请稍后再试。", agent_name
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _resolve_agent_context(self, user_id: str) -> tuple[str | None, object | None]:
        if not self.svc:
            return None, None
        resolver = ContextResolver(self.svc, getattr(self.svc, "tenant_pool", None))
        ctx = resolver.resolve(user_id)
        self._tenant_id = ctx.tenant_id
        return ctx.active_agent, ctx.session_obj

    def _build_prompt(
        self,
        *,
        user_id: str,
        message: str,
        agent_name: str | None,
        session,
    ) -> str:
        default_prompt = "请描述这张图片的内容"
        if message and message != default_prompt:
            return message

        if agent_name and "workflow" in agent_name:
            workflow_prompt = self._build_workflow_prompt(agent_name)
            if workflow_prompt:
                return workflow_prompt
        return message or default_prompt

    def _build_workflow_prompt(self, agent_name: str) -> str | None:
        try:
            from nanobot_agent_workflow_agent.tools import get_runner

            tenant_id = self._tenant_id or "default"
            runner = get_runner(tenant_id)
            node_id = getattr(runner, "current_node_id", None)
            graph = getattr(runner, "graph", None)
            if not node_id or not graph:
                return None
            node = graph.get_node(node_id)
            if not node:
                return None

            field_defs = node.field_definitions or {}
            lines = []
            ordered_fields = list(node.required_fields or []) + list(node.optional_fields or [])
            seen = set()
            for key in ordered_fields:
                if key in seen:
                    continue
                seen.add(key)
                meta = field_defs.get(key, {})
                label = meta.get("label", key)
                ftype = meta.get("type", "string")
                desc = meta.get("description", "")
                optional_text = "可选" if key in (node.optional_fields or []) else "必填"
                opts = meta.get("options", [])
                opts_text = f" 选项: [{', '.join(opts)}]" if isinstance(opts, list) and opts else ""
                lines.append(f"- {key} {label} {ftype}{opts_text}）{desc}（{optional_text}）")

            fields_block = "\n".join(lines) if lines else "- 无字段定义"
            node_name = node.name or node_id
            return (
                f"【{agent_name}拍照】当前正在检查“{node_name}”场景。请基于当前安检场景分析这张照片，判断是否符合该场景要求。\n\n"
                f"请从照片中识别以下字段信息：\n{fields_block}\n\n"
                "请在回复末尾输出如下JSON代码块（不要省略）：\n"
                "```json\n"
                "{\"photo_valid\": true, \"fields\": {\"field_key\": \"识别到的值\"}, "
                "\"reason\": \"一句话说明\"}\n"
                "```\n"
                "规则：photo_valid表示照片是否符合场景要求；无法识别的字段填null；布尔值用true/false；数字不加引号；枚举值必须从给定选项中选择。"
            )
        except Exception as e:
            logger.warning("[ImageProcessor] failed to build workflow prompt: {}", e)
            return None
