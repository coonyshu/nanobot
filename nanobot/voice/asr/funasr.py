"""
FunASR Paraformer 流式 ASR Provider.

实现本地流式语音识别，支持：
- 边说边出结果（实时流式）
- 2-pass 句尾修正（先快后准）
- 热词增强
- 标点恢复
- ITN（逆文本归一化）

FunASR 项目: https://github.com/modelscope/FunASR
"""

import json
import uuid
import asyncio
import logging
from typing import Optional, Callable, List

from .base import ASRProviderBase
from .dto import InterfaceType, ASRResult

logger = logging.getLogger(__name__)


class FunASR(ASRProviderBase):
    """
    FunASR Paraformer 流式 ASR Provider.
    
    使用 FunASR WebSocket 服务进行本地流式语音识别。
    支持 2-pass 模式：先输出快速结果，句尾再输出修正结果。
    """
    
    def __init__(self, config: dict = None):
        """
        初始化 FunASR provider.
        
        Args:
            config: 配置字典，包含：
                - host: FunASR 服务地址（默认 localhost）
                - port: FunASR 服务端口（默认 10095）
                - mode: 识别模式 online/offline/2pass（默认 2pass）
                - chunk_size: 分块大小，逗号分隔（默认 5,10,5）
                - itn: 是否开启逆文本归一化（默认 True）
                - hotwords: 热词列表，空格分隔
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.STREAM
        
        # 服务配置
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 10095))
        self.mode = self.config.get("mode", "2pass")
        
        # 解析 chunk_size
        chunk_size_str = self.config.get("chunk_size", "5,10,5")
        self.chunk_size = [int(x) for x in chunk_size_str.split(",")]
        
        # 功能开关
        self.itn = self.config.get("itn", True)
        if isinstance(self.itn, str):
            self.itn = self.itn.lower() == "true"
        
        # 热词配置
        self.hotwords = self.config.get("hotwords", "")
        
        # WebSocket 连接
        self._ws = None
        self._session_id = None
        self._is_streaming = False
        
        # 回调函数
        self._on_result: Optional[Callable[[ASRResult], None]] = None
        self._on_error: Optional[Callable[[Exception], None]] = None
        
        # 结果收集
        self._final_text = ""
        self._receive_task: Optional[asyncio.Task] = None
        
        # Opus 解码器
        self._decoder = None
        
        logger.info(
            f"FunASR initialized: host={self.host}:{self.port}, "
            f"mode={self.mode}, chunk_size={self.chunk_size}, itn={self.itn}"
        )
    
    @property
    def ws_url(self) -> str:
        """WebSocket URL."""
        return f"ws://{self.host}:{self.port}"
    
    @property
    def decoder(self):
        """懒加载 Opus 解码器."""
        if self._decoder is None:
            try:
                import opuslib_next
                self._decoder = opuslib_next.Decoder(16000, 1)
            except ImportError:
                logger.warning("opuslib_next not installed, Opus decoding disabled")
                return None
        return self._decoder
    
    async def initialize(self):
        """初始化资源."""
        pass
    
    async def close(self):
        """清理资源."""
        await self._close_websocket()
        if self._decoder is not None:
            try:
                del self._decoder
                self._decoder = None
            except Exception:
                pass
        logger.info("FunASR closed")
    
    async def _connect_websocket(self):
        """建立 WebSocket 连接."""
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "websockets is required for FunASR. "
                "Install with: pip install websockets"
            )
        
        logger.info(f"Connecting to FunASR: {self.ws_url}")
        
        self._ws = await websockets.connect(
            self.ws_url,
            max_size=10_000_000,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        )
        
        logger.info("FunASR WebSocket connected")
    
    async def _close_websocket(self):
        """关闭 WebSocket 连接."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            self._ws = None
    
    async def _send_config(self):
        """发送初始配置消息."""
        config_msg = {
            "mode": self.mode,
            "chunk_size": self.chunk_size,
            "chunk_interval": 10,
            "wav_name": self._session_id,
            "is_speaking": True,
            "itn": self.itn,
        }
        
        # 添加热词
        if self.hotwords:
            config_msg["hotwords"] = self.hotwords
        
        await self._ws.send(json.dumps(config_msg))
        logger.debug(f"FunASR config sent: {config_msg}")
    
    async def _receive_loop(self):
        """接收识别结果循环."""
        try:
            async for message in self._ws:
                if not self._is_streaming:
                    break
                
                try:
                    result = json.loads(message)
                    await self._handle_result(result)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from FunASR: {e}")
                except Exception as e:
                    logger.error(f"Error handling FunASR result: {e}")
                    if self._on_error:
                        self._on_error(e)
        
        except asyncio.CancelledError:
            logger.debug("FunASR receive loop cancelled")
        except Exception as e:
            logger.error(f"FunASR receive loop error: {e}")
            if self._on_error:
                self._on_error(e)
    
    async def _handle_result(self, result: dict):
        """处理识别结果."""
        text = result.get("text", "")
        mode = result.get("mode", "")
        is_final = result.get("is_final", False)
        
        # 2pass 模式下的结果类型判断
        # - 2pass-online: 快速中间结果
        # - 2pass-offline: 修正后的最终结果
        if mode == "2pass-offline" or is_final:
            # 最终结果
            self._final_text = text
            logger.info(f"FunASR final result: '{text}'")
            
            if self._on_result and text:
                self._on_result(ASRResult(
                    text=text,
                    is_final=True,
                ))
        else:
            # 中间结果
            logger.debug(f"FunASR partial result: '{text}'")
            
            if self._on_result and text:
                self._on_result(ASRResult(
                    text=text,
                    is_final=False,
                ))
    
    # ==================== 流式接口 ====================
    
    async def start_streaming(
        self,
        on_result: Optional[Callable[[ASRResult], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """
        启动流式识别会话.
        
        Args:
            on_result: 识别结果回调
            on_error: 错误回调
        """
        self._on_result = on_result
        self._on_error = on_error
        self._final_text = ""
        self._session_id = uuid.uuid4().hex[:16]
        
        try:
            # 建立连接
            await self._connect_websocket()
            
            # 发送配置
            await self._send_config()
            
            # 启动接收协程
            self._is_streaming = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            logger.info(f"FunASR streaming started: session={self._session_id}")
            
        except Exception as e:
            logger.error(f"Failed to start FunASR streaming: {e}")
            await self._close_websocket()
            if self._on_error:
                self._on_error(e)
            raise
    
    async def send_audio(self, audio_chunk: bytes, is_opus: bool = True):
        """
        发送音频数据.
        
        Args:
            audio_chunk: 音频数据（Opus 或 PCM）
            is_opus: 是否为 Opus 编码
        """
        if not self._is_streaming or not self._ws:
            logger.warning("send_audio called but streaming not active")
            return
        
        try:
            # Opus 解码
            if is_opus and self.decoder:
                pcm_data = self.decoder.decode(audio_chunk, 960)
            else:
                pcm_data = audio_chunk
            
            # 发送 PCM 数据
            await self._ws.send(pcm_data)
            
        except Exception as e:
            logger.error(f"Error sending audio to FunASR: {e}")
            if self._on_error:
                self._on_error(e)
    
    async def stop_streaming(self) -> str:
        """
        停止流式识别.
        
        Returns:
            最终识别文本
        """
        if not self._is_streaming:
            return self._final_text
        
        self._is_streaming = False
        
        try:
            # 发送结束标记
            if self._ws:
                end_msg = json.dumps({"is_speaking": False})
                await self._ws.send(end_msg)
                logger.debug("FunASR end marker sent")
                
                # 等待最终结果（最多 3 秒）
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._receive_task) if self._receive_task else asyncio.sleep(0),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for final FunASR result")
        
        except Exception as e:
            logger.error(f"Error stopping FunASR streaming: {e}")
        
        finally:
            await self._close_websocket()
        
        logger.info(f"FunASR streaming stopped, final text: '{self._final_text}'")
        return self._final_text
    
    # ==================== 批量接口 ====================
    
    async def speech_to_text(
        self,
        audio_data: List[bytes],
        session_id: str,
        audio_format: str = "opus",
        artifacts = None,
    ) -> ASRResult:
        """
        批量语音识别（内部使用流式接口）.
        
        Args:
            audio_data: 音频帧列表
            session_id: 会话 ID
            audio_format: 音频格式 (opus/pcm)
            artifacts: 音频工件
            
        Returns:
            ASRResult
        """
        result_text = ""
        
        async def collect_result(result: ASRResult):
            nonlocal result_text
            if result.is_final:
                result_text = result.text
        
        try:
            await self.start_streaming(on_result=collect_result)
            
            is_opus = audio_format == "opus"
            for chunk in audio_data:
                await self.send_audio(chunk, is_opus=is_opus)
            
            await self.stop_streaming()
            
        except Exception as e:
            logger.error(f"FunASR speech_to_text failed: {e}")
            return ASRResult(text="", is_final=True)
        
        return ASRResult(
            text=result_text or self._final_text,
            is_final=True,
        )
