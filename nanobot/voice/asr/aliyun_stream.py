"""
Aliyun NLS Streaming ASR Provider.

Implements real-time speech recognition using Aliyun's NLS (Natural Language Service).

Features:
- WebSocket-based streaming recognition
- Automatic token refresh
- Real-time intermediate results
- Sentence-level final results

Adapted from xiaozhi-esp32-server with modifications for nanobot:
- Removed ConnectionHandler dependency
- Uses async/await patterns for nanobot integration
- Simplified state management
"""

import json
import time
import uuid
import hmac
import base64
import hashlib
import asyncio
import logging
from urllib import parse
from datetime import datetime
from typing import Optional, List, Callable

import requests
import websockets

from .base import ASRProviderBase, AudioArtifacts
from .dto import InterfaceType, ASRResult, ASRConfig

logger = logging.getLogger(__name__)


class AliyunAccessToken:
    """Aliyun NLS Access Token generator."""
    
    @staticmethod
    def _encode_text(text: str) -> str:
        encoded = parse.quote_plus(text)
        return encoded.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")
    
    @staticmethod
    def _encode_dict(params: dict) -> str:
        sorted_params = [(k, params[k]) for k in sorted(params.keys())]
        encoded = parse.urlencode(sorted_params)
        return encoded.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")
    
    @staticmethod
    def create_token(access_key_id: str, access_key_secret: str) -> tuple:
        """
        Create NLS access token.
        
        Args:
            access_key_id: Aliyun Access Key ID
            access_key_secret: Aliyun Access Key Secret
            
        Returns:
            Tuple of (token, expire_time) or (None, None) on failure
        """
        parameters = {
            "AccessKeyId": access_key_id,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": "cn-shanghai",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid1()),
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Version": "2019-02-28",
        }
        
        query_string = AliyunAccessToken._encode_dict(parameters)
        string_to_sign = (
            "GET" + "&" + 
            AliyunAccessToken._encode_text("/") + "&" + 
            AliyunAccessToken._encode_text(query_string)
        )
        
        signature = hmac.new(
            bytes(access_key_secret + "&", encoding="utf-8"),
            bytes(string_to_sign, encoding="utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(signature)
        signature = AliyunAccessToken._encode_text(signature)
        
        url = f"http://nls-meta.cn-shanghai.aliyuncs.com/?Signature={signature}&{query_string}"
        
        try:
            response = requests.get(url, timeout=10)
            if response.ok:
                data = response.json()
                if "Token" in data:
                    return data["Token"]["Id"], data["Token"]["ExpireTime"]
        except Exception as e:
            logger.error(f"Failed to create Aliyun token: {e}")
        
        return None, None


class AliyunStreamASR(ASRProviderBase):
    """
    Aliyun NLS Streaming ASR Provider.
    
    Provides real-time speech recognition using Aliyun's streaming API.
    Supports both automatic VAD-based and manual push-to-talk modes.
    """
    
    def __init__(self, config: dict):
        """
        Initialize Aliyun Stream ASR provider.
        
        Args:
            config: Configuration dictionary with keys:
                - access_key_id: Aliyun Access Key ID
                - access_key_secret: Aliyun Access Key Secret
                - appkey: NLS Application Key
                - host: NLS gateway host (default: nls-gateway-cn-shanghai.aliyuncs.com)
                - max_sentence_silence: Silence threshold in ms (default: 800)
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.STREAM
        
        # Configuration
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.appkey = config.get("appkey")
        self.host = config.get("host", "nls-gateway-cn-shanghai.aliyuncs.com")
        self.max_sentence_silence = config.get("max_sentence_silence", 800)
        self.hot_words = config.get("hot_words", [])  # 热词配置
        
        # 打印热词配置
        if self.hot_words:
            logger.info(f"[ASR] Hot words enabled: {len(self.hot_words)} words")
            logger.debug(f"[ASR] Hot words detail: {self.hot_words}")
        else:
            logger.info("[ASR] Hot words disabled (empty or None)")
        
        # WebSocket URL
        if "-internal." in self.host:
            self.ws_url = f"ws://{self.host}/ws/v1"
        else:
            self.ws_url = f"wss://{self.host}/ws/v1"
        
        # Token management
        self.token = config.get("token")
        self.expire_time = None
        
        if self.access_key_id and self.access_key_secret:
            self._refresh_token()
        elif not self.token:
            raise ValueError("Must provide access_key_id+access_key_secret or token")
        
        # State management
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.task_id = uuid.uuid4().hex
        self.is_processing = False
        self.server_ready = False
        self.recognized_text = ""
        
        # Opus decoder (lazy initialized)
        self._decoder = None
        
        # Callbacks
        self._on_result: Optional[Callable[[ASRResult], None]] = None
        self._on_error: Optional[Callable[[Exception], None]] = None
    
    def _refresh_token(self):
        """Refresh access token."""
        self.token, expire_time_str = AliyunAccessToken.create_token(
            self.access_key_id, self.access_key_secret
        )
        
        if not self.token:
            raise ValueError("Failed to obtain valid access token")
        
        try:
            expire_str = str(expire_time_str).strip()
            if expire_str.isdigit():
                expire_time = datetime.fromtimestamp(int(expire_str))
            else:
                expire_time = datetime.strptime(expire_str, "%Y-%m-%dT%H:%M:%SZ")
            self.expire_time = expire_time.timestamp() - 60  # 1 minute buffer
        except Exception:
            self.expire_time = None
        
        logger.info("Aliyun ASR token refreshed")
    
    def _is_token_expired(self) -> bool:
        """Check if token is expired."""
        return self.expire_time and time.time() > self.expire_time
    
    @property
    def decoder(self):
        """Lazy-initialize Opus decoder."""
        if self._decoder is None:
            try:
                import opuslib_next
                self._decoder = opuslib_next.Decoder(16000, 1)
            except ImportError:
                logger.error("opuslib_next not installed")
                return None
        return self._decoder
    
    async def initialize(self):
        """Initialize provider resources."""
        pass
    
    async def close(self):
        """Clean up provider resources."""
        await self._cleanup()
        if self._decoder is not None:
            try:
                del self._decoder
                self._decoder = None
            except Exception:
                pass
    
    async def start_streaming(
        self,
        on_result: Optional[Callable[[ASRResult], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """
        Start streaming recognition session.
        
        Args:
            on_result: Callback for recognition results
            on_error: Callback for errors
        """
        self._on_result = on_result
        self._on_error = on_error
        
        try:
            if self._is_token_expired():
                self._refresh_token()
            
            # Connect WebSocket
            headers = {"X-NLS-Token": self.token}
            self.ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                max_size=10_000_000,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
            )
            
            self.task_id = uuid.uuid4().hex
            self.is_processing = True
            self.server_ready = False
            self.recognized_text = ""
            
            logger.debug(f"ASR WebSocket connected, task_id: {self.task_id}")
            
            # Send start request
            start_request = {
                "header": {
                    "namespace": "SpeechTranscriber",
                    "name": "StartTranscription",
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "appkey": self.appkey,
                },
                "payload": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "enable_intermediate_result": True,
                    "enable_punctuation_prediction": True,
                    "enable_inverse_text_normalization": True,
                    "max_sentence_silence": self.max_sentence_silence,
                    "enable_voice_detection": False,
                    # 热词配置（从配置文件加载）
                    "vocabulary_id": "",
                    "customization_id": "",
                    "special_word": self.hot_words,
                }
            }
            logger.info(f"[ASR] Sending start request with hot_words: {len(self.hot_words) if self.hot_words else 0} words")
            logger.debug(f"[ASR] Start request payload: {start_request['payload']}")
            await self.ws.send(json.dumps(start_request, ensure_ascii=False))
            logger.debug("ASR start request sent")
            
            # Start result listener
            asyncio.create_task(self._listen_results())
            
        except Exception as e:
            logger.error(f"Failed to start ASR streaming: {e}")
            if self._on_error:
                self._on_error(e)
            await self._cleanup()
            raise
    
    async def send_audio(self, audio_chunk: bytes, is_opus: bool = True):
        """
        Send audio chunk for recognition.
        
        Args:
            audio_chunk: Audio data (Opus or PCM)
            is_opus: Whether audio is Opus-encoded
        """
        if not self.ws or not self.is_processing:
            return
        
        if not self.server_ready:
            logger.debug("Server not ready, buffering audio")
            return
        
        try:
            if is_opus and self.decoder:
                pcm_frame = self.decoder.decode(audio_chunk, 960)
            else:
                pcm_frame = audio_chunk
            
            await self.ws.send(pcm_frame)
        except Exception as e:
            logger.warning(f"Failed to send audio: {e}")
    
    async def stop_streaming(self) -> str:
        """
        Stop streaming and get final result.
        
        Returns:
            Final recognized text
        """
        if self.ws and self.is_processing:
            try:
                stop_msg = {
                    "header": {
                        "namespace": "SpeechTranscriber",
                        "name": "StopTranscription",
                        "message_id": uuid.uuid4().hex,
                        "task_id": self.task_id,
                        "appkey": self.appkey,
                    }
                }
                await self.ws.send(json.dumps(stop_msg, ensure_ascii=False))
                logger.debug("ASR stop request sent")
                
                # Wait briefly for final results
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to stop ASR streaming: {e}")
        
        result = self.recognized_text
        await self._cleanup()
        return result
    
    async def _listen_results(self):
        """Listen for recognition results."""
        try:
            while self.is_processing and self.ws:
                try:
                    response = await asyncio.wait_for(self.ws.recv(), timeout=30)
                    result = json.loads(response)
                    
                    header = result.get("header", {})
                    payload = result.get("payload", {})
                    message_name = header.get("name", "")
                    status = header.get("status", 0)
                    
                    # Handle errors
                    if status != 20000000:
                        if status in [40000004, 40010003, 40010004]:
                            logger.warning(f"ASR connection issue, status: {status}")
                            break
                        elif status in [40270002, 40270003]:
                            logger.warning(f"Audio processing issue, status: {status}")
                            continue
                        else:
                            logger.error(f"ASR error, status: {status}, message: {header.get('status_text', '')}")
                            continue
                    
                    # Handle messages
                    if message_name == "TranscriptionStarted":
                        self.server_ready = True
                        logger.debug("ASR server ready")
                    
                    elif message_name == "TranscriptionResultChanged":
                        # Intermediate result
                        text = payload.get("result", "")
                        if text and self._on_result:
                            self._on_result(ASRResult(text=text, is_final=False))
                    
                    elif message_name == "SentenceEnd":
                        # Final sentence result
                        text = payload.get("result", "")
                        if text:
                            self.recognized_text = text
                            logger.info(f"ASR recognized: {text}")
                            if self._on_result:
                                self._on_result(ASRResult(text=text, is_final=True))
                    
                    elif message_name == "TranscriptionCompleted":
                        logger.debug("ASR transcription completed")
                        break
                
                except asyncio.TimeoutError:
                    logger.warning("ASR receive timeout")
                    break
                except websockets.ConnectionClosed:
                    logger.info("ASR WebSocket closed")
                    break
                    
        except Exception as e:
            logger.error(f"ASR listener error: {e}")
            if self._on_error:
                self._on_error(e)
        finally:
            self.is_processing = False
    
    async def _cleanup(self):
        """Clean up resources."""
        self.is_processing = False
        self.server_ready = False
        
        if self.ws:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=2.0)
            except Exception as e:
                logger.warning(f"WebSocket close error: {e}")
            finally:
                self.ws = None
        
        logger.debug("ASR cleanup completed")
    
    async def speech_to_text(
        self,
        audio_data: List[bytes],
        session_id: str,
        audio_format: str = "opus",
        artifacts: Optional[AudioArtifacts] = None,
    ) -> ASRResult:
        """
        Batch speech-to-text recognition (non-streaming fallback).
        
        For streaming scenarios, use start_streaming/send_audio/stop_streaming instead.
        """
        # For batch processing, start streaming, send all audio, then stop
        self.recognized_text = ""
        
        try:
            await self.start_streaming()
            
            # Wait for server ready
            for _ in range(50):  # 5 seconds max
                if self.server_ready:
                    break
                await asyncio.sleep(0.1)
            
            # Send all audio
            for chunk in audio_data:
                await self.send_audio(chunk, is_opus=(audio_format == "opus"))
                await asyncio.sleep(0.01)  # Small delay to prevent overwhelming
            
            # Stop and get result
            text = await self.stop_streaming()
            return ASRResult(text=text, is_final=True)
            
        except Exception as e:
            logger.error(f"Batch ASR failed: {e}")
            return ASRResult(text="", is_final=True)
