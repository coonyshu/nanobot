"""
Voice WebSocket Gateway Handler.

Handles WebSocket connections for real-time voice interaction,
coordinating ASR, TTS, and nanobot Agent loop integration.

Architecture:
- Receives Opus audio from client via WebSocket
- Uses VAD for voice activity detection (auto mode)
- Sends audio to ASR provider for speech-to-text
- Routes recognized text to nanobot Agent for processing
- Sends Agent response to TTS provider
- Streams TTS audio back to client via WebSocket
"""

import os
import json
import asyncio
import logging
import struct
from typing import Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect

from .session import VoiceSession, VoiceSessionManager, SessionState
from .asr import ASRProviderBase, AliyunStreamASR, ASRResult
from .tts import TTSProviderBase, AliyunStreamTTS
from .vad import SileroVAD
from .utils import OpusEncoder

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """WebSocket message types."""
    # Control messages
    HELLO = "hello"              # Connection established
    START_LISTEN = "start"       # Start listening (manual mode)
    STOP_LISTEN = "stop"         # Stop listening (manual mode)
    ABORT = "abort"              # Abort current operation
    CLOSE = "close"              # Close connection
    
    # Status messages
    LISTENING = "listening"      # Now listening for speech
    PROCESSING = "processing"    # Processing speech
    SPEAKING = "speaking"        # Playing TTS audio
    IDLE = "idle"               # Idle, ready for input
    
    # Content messages
    ASR_RESULT = "asr_result"    # Speech recognition result
    TEXT = "text"                # Text message (agent response)
    ERROR = "error"              # Error message


@dataclass
class VoiceConfig:
    """Voice gateway configuration."""
    # ASR config
    asr_provider: str = "aliyun_stream"
    asr_access_key_id: str = ""
    asr_access_key_secret: str = ""
    asr_appkey: str = ""
    asr_host: str = "nls-gateway-cn-shanghai.aliyuncs.com"
    
    # TTS config
    tts_provider: str = "aliyun_stream"
    tts_access_key_id: str = ""
    tts_access_key_secret: str = ""
    tts_appkey: str = ""
    tts_host: str = "nls-gateway-cn-beijing.aliyuncs.com"
    tts_voice: str = "xiaoyun"
    tts_volume: int = 50
    tts_speech_rate: int = 0
    
    # VAD config
    vad_enabled: bool = True
    vad_model_path: str = "models/silero-vad/silero_vad.onnx"
    vad_threshold: float = 0.5
    
    # Audio config
    sample_rate: int = 16000
    audio_format: str = "opus"  # opus or pcm


def create_asr_provider(config: VoiceConfig) -> ASRProviderBase:
    """
    Create ASR provider based on configuration.
    
    Args:
        config: Voice configuration
        
    Returns:
        Initialized ASR provider instance
    """
    provider = config.asr_provider
    
    if provider == "whisper":
        from .asr.whisper_asr import WhisperASR
        return WhisperASR({
            "model_size": os.environ.get("WHISPER_MODEL_SIZE", "large-v3"),
            "device": os.environ.get("WHISPER_DEVICE", "auto"),
            "language": os.environ.get("WHISPER_LANGUAGE", "zh"),
            "compute_type": os.environ.get("WHISPER_COMPUTE_TYPE", "float16"),
            "model_path": os.environ.get("WHISPER_MODEL_PATH", ""),
            "beam_size": int(os.environ.get("WHISPER_BEAM_SIZE", "5")),
            "initial_prompt": os.environ.get("WHISPER_INITIAL_PROMPT", ""),
        })
    elif provider == "aliyun_stream":
        return AliyunStreamASR({
            "access_key_id": config.asr_access_key_id,
            "access_key_secret": config.asr_access_key_secret,
            "appkey": config.asr_appkey,
            "host": config.asr_host,
        })
    elif provider == "funasr":
        from .asr.funasr import FunASR
        return FunASR({
            "host": os.environ.get("FUNASR_HOST", "localhost"),
            "port": int(os.environ.get("FUNASR_PORT", "10095")),
            "mode": os.environ.get("FUNASR_MODE", "2pass"),
            "chunk_size": os.environ.get("FUNASR_CHUNK_SIZE", "5,10,5"),
            "itn": os.environ.get("FUNASR_ITN", "true"),
            "hotwords": os.environ.get("FUNASR_HOTWORDS", ""),
        })
    else:
        raise ValueError(f"Unknown ASR provider: {provider}")


def create_tts_provider(config: VoiceConfig) -> TTSProviderBase:
    """
    Create TTS provider based on configuration.
    
    Args:
        config: Voice configuration
        
    Returns:
        Initialized TTS provider instance
    """
    provider = config.tts_provider
    
    if provider == "edge":
        from .tts.edge_tts import EdgeTTS
        return EdgeTTS({
            "voice": os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            "rate": os.environ.get("EDGE_TTS_RATE", "+0%"),
            "volume": os.environ.get("EDGE_TTS_VOLUME", "+0%"),
            "pitch": os.environ.get("EDGE_TTS_PITCH", "+0Hz"),
            "sample_rate": config.sample_rate,
        })
    elif provider == "piper":
        from .tts.piper_tts import PiperTTS
        return PiperTTS({
            "model_path": os.environ.get("PIPER_MODEL_PATH", ""),
            "config_path": os.environ.get("PIPER_CONFIG_PATH", ""),
            "speaker_id": int(os.environ.get("PIPER_SPEAKER_ID", "0")),
            "length_scale": float(os.environ.get("PIPER_LENGTH_SCALE", "1.0")),
            "noise_scale": float(os.environ.get("PIPER_NOISE_SCALE", "0.667")),
            "noise_w": float(os.environ.get("PIPER_NOISE_W", "0.8")),
            "sentence_silence": float(os.environ.get("PIPER_SENTENCE_SILENCE", "0.3")),
            "sample_rate": config.sample_rate,
        })
    elif provider == "cosyvoice":
        from .tts.cosyvoice_tts import CosyVoiceTTS
        return CosyVoiceTTS({
            "model_path": os.environ.get("COSYVOICE_MODEL_PATH", ""),
            "speaker": os.environ.get("COSYVOICE_SPEAKER", "中文女"),
            "mode": os.environ.get("COSYVOICE_MODE", "sft"),
            "ref_audio_path": os.environ.get("COSYVOICE_REF_AUDIO_PATH", ""),
            "ref_text": os.environ.get("COSYVOICE_REF_TEXT", ""),
            "sample_rate": int(os.environ.get("COSYVOICE_SAMPLE_RATE", "22050")),
        })
    elif provider == "aliyun_stream":
        return AliyunStreamTTS({
            "access_key_id": config.tts_access_key_id,
            "access_key_secret": config.tts_access_key_secret,
            "appkey": config.tts_appkey,
            "host": config.tts_host,
            "voice": config.tts_voice,
            "volume": config.tts_volume,
            "speech_rate": config.tts_speech_rate,
            "sample_rate": config.sample_rate,
        })
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


class VoiceWebSocketHandler:
    """
    WebSocket handler for voice interaction.
    
    Manages the full voice interaction loop:
    1. Receive audio from client
    2. Detect voice activity (VAD)
    3. Send to ASR for recognition
    4. Process with Agent
    5. Generate TTS response
    6. Send audio back to client
    """
    
    def __init__(
        self,
        config: VoiceConfig,
        session_manager: VoiceSessionManager,
        agent_callback: Optional[Callable[[str, str], Any]] = None,
        agent_image_callback: Optional[Callable[[str, str, str, str], Any]] = None,
        extra_message_handler: Optional[Callable[[str, dict], Any]] = None,
        session_register_callback: Optional[Callable[[str, str, Any, Any], None]] = None,
        session_unregister_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Initialize voice handler.
        
        Args:
            config: Voice configuration
            session_manager: Session manager instance
            agent_callback: Callback to process text with Agent
                           Signature: async def callback(user_id, text) -> str
            agent_image_callback: Callback to process image with Agent
                           Signature: async def callback(user_id, text, image_b64, mime_type) -> str
            extra_message_handler: Callback for unrecognized JSON message types
                           Signature: async def handler(user_id, data) -> None
            session_register_callback: Callback when session is created
                           Signature: def callback(user_id, session_id, websocket, session) -> None
            session_unregister_callback: Callback when session is closed
                           Signature: def callback(user_id, session_id) -> None
        """
        self.config = config
        self.session_manager = session_manager
        self.agent_callback = agent_callback
        self.agent_image_callback = agent_image_callback
        self.extra_message_handler = extra_message_handler
        self.session_register_callback = session_register_callback
        self.session_unregister_callback = session_unregister_callback
        
        # Create providers (will be initialized per-session)
        self._asr_config = {
            "access_key_id": config.asr_access_key_id,
            "access_key_secret": config.asr_access_key_secret,
            "appkey": config.asr_appkey,
            "host": config.asr_host,
        }
        
        self._tts_config = {
            "access_key_id": config.tts_access_key_id,
            "access_key_secret": config.tts_access_key_secret,
            "appkey": config.tts_appkey,
            "host": config.tts_host,
            "voice": config.tts_voice,
            "volume": config.tts_volume,
            "speech_rate": config.tts_speech_rate,
            "sample_rate": config.sample_rate,
        }
    
    async def handle_connection(self, websocket: WebSocket, user_id: str):
        """
        Handle WebSocket connection for voice interaction.
        
        Args:
            websocket: FastAPI WebSocket connection
            user_id: User identifier
        """
        session = None
        asr = None
        tts = None
        vad = None
        opus_encoder = None
        
        try:
            # Create session
            session = await self.session_manager.create_session(user_id)
            session.sample_rate = self.config.sample_rate
            session.audio_format = self.config.audio_format
            
            # 注册会话（如果有回调），传递 session_id 以支持多端并发
            if self.session_register_callback:
                self.session_register_callback(user_id, session.session_id, websocket, session)
            
            # Initialize providers
            asr = create_asr_provider(self.config)
            tts = create_tts_provider(self.config)
            
            if self.config.vad_enabled:
                vad = SileroVAD(
                    model_path=self.config.vad_model_path,
                    threshold=self.config.vad_threshold,
                    sample_rate=self.config.sample_rate,
                )
            
            opus_encoder = OpusEncoder(
                sample_rate=self.config.sample_rate,
                channels=1,
            )
            
            # Send hello
            await self._send_message(websocket, MessageType.HELLO, {
                "session_id": session.session_id,
                "sample_rate": self.config.sample_rate,
                "audio_format": self.config.audio_format,
            })
            
            # Start receive and send tasks
            receive_task = asyncio.create_task(
                self._receive_loop(websocket, session, asr, vad, opus_encoder)
            )
            send_task = asyncio.create_task(
                self._send_loop(websocket, session, tts, opus_encoder)
            )
            
            # Wait for tasks
            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for user {user_id}")
        except Exception as e:
            logger.error(f"Voice handler error: {e}")
            try:
                await self._send_message(websocket, MessageType.ERROR, {"error": str(e)})
            except Exception:
                pass
        finally:
            # Cleanup
            if asr:
                await asr.close()
            if tts:
                await tts.close()
            if opus_encoder:
                opus_encoder.close()
            if session:
                await self.session_manager.close_session(session.session_id)
            
            # 注销会话（如果有回调）
            if self.session_unregister_callback and session:
                self.session_unregister_callback(user_id, session.session_id)
    
    async def _receive_loop(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        asr: ASRProviderBase,
        vad: Optional[SileroVAD],
        opus_encoder: OpusEncoder,
    ):
        """Receive audio from client and process with ASR."""
        asr_started = False
        agent_task: Optional[asyncio.Task] = None
        
        try:
            while not session.stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        websocket.receive(),
                        timeout=60,
                    )
                except asyncio.TimeoutError:
                    # Send keepalive
                    continue
                except RuntimeError as e:
                    # WebSocket already disconnected
                    if "disconnect" in str(e).lower():
                        logger.info("WebSocket disconnected gracefully")
                        break
                    raise
                
                if "bytes" in message:
                    # Audio data
                    audio_data = message["bytes"]
                    session.update_activity()
                    logger.debug(f"Received audio: {len(audio_data)} bytes, format={session.audio_format}")
                    
                    # Decode Opus if needed
                    if session.audio_format == "opus":
                        pcm_data = opus_encoder.decode(audio_data)
                    else:
                        pcm_data = audio_data
                    
                    # VAD detection
                    has_voice = True
                    speech_ended = False
                    
                    if vad and session.listen_mode.value == "auto":
                        has_voice, speech_ended, _ = vad.detect_speech_end(pcm_data)
                    
                    # Start ASR when voice detected (or manual mode)
                    if has_voice and not asr_started:
                        logger.info(f"Starting ASR stream, mode={session.listen_mode.value}")
                        session.set_state(SessionState.LISTENING)
                        await self._send_message(websocket, MessageType.LISTENING)
                        
                        await asr.start_streaming(
                            on_result=lambda r: asyncio.create_task(
                                self._handle_asr_result(websocket, session, r)
                            )
                        )
                        asr_started = True
                    
                    # Send audio to ASR
                    if asr_started:
                        await asr.send_audio(audio_data, is_opus=(session.audio_format == "opus"))
                    
                    # Handle speech end
                    if speech_ended and asr_started:
                        text = await asr.stop_streaming()
                        asr_started = False
                        
                        if text:
                            # 后台运行 Agent 处理，不阻塞 _receive_loop
                            agent_task = asyncio.create_task(
                                self._process_text(websocket, session, text)
                            )
                        
                        if vad:
                            vad.reset()
                
                elif "text" in message:
                    # Control message
                    result = await self._handle_control_message(
                        websocket, session, message["text"], asr, asr_started
                    )
                    if result is not None:
                        asr_started = result
                    
        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.error(f"Receive loop error: {e}", exc_info=True)
        finally:
            if asr_started:
                await asr.stop_streaming()
            if agent_task and not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except asyncio.CancelledError:
                    pass
    
    async def _send_loop(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        tts: TTSProviderBase,
        opus_encoder: OpusEncoder,
    ):
        """Send TTS audio to client."""
        try:
            while not session.stop_event.is_set():
                try:
                    text = await asyncio.wait_for(
                        session.tts_text_queue.get(),
                        timeout=1,
                    )
                except asyncio.TimeoutError:
                    continue
                
                if session.abort_flag:
                    session.reset_abort()
                    continue
                
                session.set_state(SessionState.SPEAKING)
                await self._send_message(websocket, MessageType.SPEAKING)
                
                # Collect all audio chunks
                audio_chunks = []
                async for audio_chunk in tts.synthesize_stream(text):
                    if session.abort_flag:
                        break
                    audio_chunks.append(audio_chunk)
                
                if audio_chunks and not session.abort_flag:
                    # Combine all audio data
                    audio_data = b"".join(audio_chunks)
                    
                    # Check TTS output format
                    tts_format = getattr(tts, 'audio_format', 'pcm')
                    
                    if tts_format == 'mp3':
                        # Edge TTS outputs MP3, browser can play directly
                        await websocket.send_bytes(audio_data)
                        logger.debug(f"Sent MP3 audio: {len(audio_data)} bytes")
                    else:
                        # PCM format, convert to WAV for browser playback
                        wav_data = self._pcm_to_wav(audio_data, session.sample_rate)
                        await websocket.send_bytes(wav_data)
                        logger.debug(f"Sent WAV audio: {len(wav_data)} bytes")
                
                session.set_state(SessionState.IDLE)
                await self._send_message(websocket, MessageType.IDLE)
                
        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.error(f"Send loop error: {e}")
    
    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
        """Convert raw PCM data to WAV format."""
        data_size = len(pcm_data)
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8
        
        # WAV header (44 bytes)
        header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF',                      # ChunkID
            36 + data_size,               # ChunkSize
            b'WAVE',                      # Format
            b'fmt ',                      # Subchunk1ID
            16,                           # Subchunk1Size (PCM)
            1,                            # AudioFormat (PCM = 1)
            channels,                     # NumChannels
            sample_rate,                  # SampleRate
            byte_rate,                    # ByteRate
            block_align,                  # BlockAlign
            bits_per_sample,              # BitsPerSample
            b'data',                      # Subchunk2ID
            data_size,                    # Subchunk2Size
        )
        
        return header + pcm_data
    
    async def _handle_asr_result(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        result: ASRResult,
    ):
        """Handle ASR recognition result."""
        await self._send_message(websocket, MessageType.ASR_RESULT, {
            "text": result.text,
            "is_final": result.is_final,
        })
    
    async def _handle_control_message(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        message: str,
        asr: ASRProviderBase,
        asr_started: bool = False,
    ) -> Optional[bool]:
        """
        Handle control messages from client.
        
        Returns:
            New asr_started state if changed, None otherwise
        """
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")
            logger.debug(f"Control message: {msg_type}, data={data}")
            
            if msg_type == "start":
                # Manual mode: start listening
                session.listen_mode = session.listen_mode.MANUAL
                
                # 支持客户端指定音频格式
                audio_format = data.get("format", session.audio_format)
                if audio_format in ("pcm", "opus"):
                    session.audio_format = audio_format
                    logger.info(f"Audio format set to: {audio_format}")
                
                session.set_state(SessionState.LISTENING)
                await self._send_message(websocket, MessageType.LISTENING)
                return None  # ASR will be started when audio arrives
                
            elif msg_type == "stop":
                # Manual mode: stop listening and get result
                logger.info(f"Stop requested, asr_started={asr_started}, state={session.state}")
                if asr_started:
                    text = await asr.stop_streaming()
                    logger.info(f"ASR result: {text}")
                    if text:
                        # 后台运行 Agent 处理，不阻塞消息接收
                        asyncio.create_task(
                            self._process_text(websocket, session, text)
                        )
                    return False  # ASR stopped
                        
            elif msg_type == "abort":
                # Abort current operation
                session.abort()
                
            elif msg_type == "send_text":
                # 直接发送文本消息（非语音），支持流式输出
                text = data.get("text", "")
                show_thinking = data.get("show_thinking", True)  # 默认显示思考过程
                if text:
                    logger.info(f"Received text message via WebSocket: {text[:50]}...")
                    # 保存配置到 session
                    session.show_thinking = show_thinking
                    # 后台运行 Agent 处理，不阻塞消息接收
                    asyncio.create_task(
                        self._process_text(websocket, session, text)
                    )
            
            elif msg_type == "send_image":
                # 图片消息：携带 base64 图片数据 + 可选文字
                image_data = data.get("image", "")  # data:image/jpeg;base64,xxx
                text = data.get("text", "") or "请描述这张图片的内容"
                show_thinking = data.get("show_thinking", True)
                if image_data:
                    # 解析 data URL: data:<mime>;base64,<b64>
                    mime_type = "image/jpeg"
                    image_b64 = image_data
                    if image_data.startswith("data:"):
                        try:
                            header, image_b64 = image_data.split(",", 1)
                            mime_type = header.split(":")[1].split(";")[0]
                        except Exception:
                            pass
                    logger.info(f"Received image message via WebSocket: mime={mime_type}, text={text[:50]}")
                    session.show_thinking = show_thinking
                    asyncio.create_task(
                        self._process_image(websocket, session, text, image_b64, mime_type)
                    )
                
            elif msg_type == "close":
                # Close connection
                session.stop_event.set()
            
            elif msg_type == "register_tools":
                # Register frontend tools
                descriptors = data.get("descriptors", [])
                if self.extra_message_handler:
                    # Forward to extra handler (typically app.py's register_tools handler)
                    await self.extra_message_handler(session.user_id, data)
                logger.info(f"Voice WebSocket: registered {len(descriptors)} frontend tools for user={session.user_id}")
            
            else:
                # Forward unrecognized messages to extra handler
                if self.extra_message_handler:
                    await self.extra_message_handler(session.user_id, data)
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid control message: {message}")
        
        return None
    
    async def _process_text(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        text: str,
    ):
        """Process recognized text with Agent."""
        session.set_state(SessionState.PROCESSING)
        await self._send_message(websocket, MessageType.PROCESSING)
        
        # Call agent callback
        if self.agent_callback:
            try:
                result = await self.agent_callback(session.user_id, text)
                
                # Handle both tuple (response, agent_name) and string response
                if isinstance(result, tuple):
                    response, agent_name = result
                else:
                    response, agent_name = result, None
                    
                if response:
                    # 检查是否已通过流式输出发送过文本
                    streaming_sent = getattr(session, '_streaming_sent', False)
                    response_via_bus = getattr(session, '_response_via_bus', False)
                    
                    if hasattr(session, '_streaming_sent'):
                        delattr(session, '_streaming_sent')
                    if hasattr(session, '_response_via_bus'):
                        delattr(session, '_response_via_bus')
                    
                    # If SubAgent is active (agent_name is not None), response was already sent via bus
                    # Only send via gateway if it's MainAgent response (agent_name is None)
                    if not streaming_sent and not response_via_bus and not agent_name:
                        await self._send_message(websocket, MessageType.TEXT, {"text": response, "agent_name": agent_name})
                        await session.tts_text_queue.put(response)
            except Exception as e:
                await self._send_message(websocket, MessageType.ERROR, {"error": str(e)})
        else:
            # Echo mode for testing
            await self._send_message(websocket, MessageType.TEXT, {"text": f"你说: {text}"})
            await session.tts_text_queue.put(f"你说: {text}")
    
    async def _process_image(
        self,
        websocket: WebSocket,
        session: VoiceSession,
        text: str,
        image_b64: str,
        mime_type: str,
    ):
        """Process image message with Agent image callback."""
        session.set_state(SessionState.PROCESSING)
        await self._send_message(websocket, MessageType.PROCESSING)

        if self.agent_image_callback:
            try:
                response = await self.agent_image_callback(session.user_id, text, image_b64, mime_type)
                if response:
                    # Get active agent name from session
                    agent_name = getattr(session, 'active_agent_name', None)
                    await self._send_message(websocket, MessageType.TEXT, {"text": response, "agent_name": agent_name})
                    await session.tts_text_queue.put(response)
            except Exception as e:
                logger.error(f"Agent image callback error: {e}")
                await self._send_message(websocket, MessageType.ERROR, {"error": str(e)})
        else:
            # Fallback: treat as text-only
            await self._send_message(websocket, MessageType.TEXT, {"text": "图片识别功能未启用"})

        session.set_state(SessionState.IDLE)
        await self._send_message(websocket, MessageType.IDLE)

    async def _send_message(
        self,
        websocket: WebSocket,
        msg_type: MessageType,
        data: dict = None,
    ):
        """Send JSON message to client."""
        message = {"type": msg_type.value}
        if data:
            message.update(data)
        await websocket.send_json(message)
