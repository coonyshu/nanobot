"""
Aliyun NLS Streaming TTS Provider.

Implements real-time text-to-speech synthesis using Aliyun's NLS service.

Features:
- WebSocket-based streaming synthesis
- Automatic token refresh
- Multiple voice options (CosyVoice)
- Configurable speech rate, pitch, and volume

Adapted from xiaozhi-esp32-server with modifications for nanobot:
- Removed ConnectionHandler dependency
- Async generator-based streaming
- Simplified session management
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
from typing import Optional, AsyncGenerator, Callable

import requests
import websockets

from .base import TTSProviderBase
from .dto import InterfaceType, TTSResult, TTSConfig

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


class AliyunStreamTTS(TTSProviderBase):
    """
    Aliyun NLS Streaming TTS Provider.
    
    Provides real-time text-to-speech synthesis using Aliyun's streaming API.
    Supports CosyVoice and other voice models.
    """
    
    def __init__(self, config: dict):
        """
        Initialize Aliyun Stream TTS provider.
        
        Args:
            config: Configuration dictionary with keys:
                - access_key_id: Aliyun Access Key ID
                - access_key_secret: Aliyun Access Key Secret
                - appkey: NLS Application Key
                - host: NLS gateway host (default: nls-gateway-cn-beijing.aliyuncs.com)
                - voice: Voice name (default: xiaoyun)
                - volume: Volume 0-100 (default: 50)
                - speech_rate: Speech rate -500~500 (default: 0)
                - pitch_rate: Pitch rate -500~500 (default: 0)
                - format: Output format pcm/mp3/wav (default: pcm)
                - sample_rate: Sample rate (default: 16000)
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.DUAL_STREAM
        
        # Configuration
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.appkey = config.get("appkey")
        self.host = config.get("host", "nls-gateway-cn-beijing.aliyuncs.com")
        
        # Voice settings
        self.voice = config.get("voice", "xiaoyun")
        self.volume = int(config.get("volume", 50))
        self.speech_rate = int(config.get("speech_rate", 0))
        self.pitch_rate = int(config.get("pitch_rate", 0))
        self.audio_format = config.get("format", "pcm")
        self.sample_rate = int(config.get("sample_rate", 16000))
        
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
        
        # State
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.task_id = uuid.uuid4().hex
        self.last_active_time = None
    
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
            self.expire_time = expire_time.timestamp() - 60
        except Exception:
            self.expire_time = None
        
        logger.info("Aliyun TTS token refreshed")
    
    def _is_token_expired(self) -> bool:
        """Check if token is expired."""
        return self.expire_time and time.time() > self.expire_time
    
    async def _ensure_connection(self) -> websockets.WebSocketClientProtocol:
        """Ensure WebSocket connection is available."""
        if self._is_token_expired():
            logger.warning("Token expired, refreshing...")
            self._refresh_token()
        
        current_time = time.time()
        
        # Reuse connection if recent
        if self.ws and self.last_active_time and current_time - self.last_active_time < 10:
            self.task_id = uuid.uuid4().hex
            logger.debug(f"Reusing connection, task_id: {self.task_id}")
            return self.ws
        
        # Create new connection
        logger.debug("Creating new TTS connection...")
        self.ws = await websockets.connect(
            self.ws_url,
            additional_headers={"X-NLS-Token": self.token},
            ping_interval=30,
            ping_timeout=10,
            close_timeout=10,
        )
        self.task_id = uuid.uuid4().hex
        self.last_active_time = time.time()
        logger.debug(f"TTS WebSocket connected, task_id: {self.task_id}")
        
        return self.ws
    
    async def initialize(self):
        """Initialize provider resources."""
        pass
    
    async def close(self):
        """Clean up provider resources."""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.last_active_time = None
    
    async def text_to_speech(self, text: str) -> TTSResult:
        """
        Convert text to speech (batch mode).
        
        Args:
            text: Text to synthesize
            
        Returns:
            TTSResult with audio data
        """
        audio_chunks = []
        
        async for chunk in self.synthesize_stream(text):
            audio_chunks.append(chunk)
        
        return TTSResult(
            audio_data=b"".join(audio_chunks),
            format=self.audio_format,
            sample_rate=self.sample_rate,
            text=text,
        )
    
    async def synthesize_stream(
        self,
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream-based text-to-speech synthesis.
        
        Args:
            text: Text to synthesize
            on_audio: Optional callback for each audio chunk
            
        Yields:
            PCM audio chunks
        """
        # Clean text
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return
        
        ws = None
        try:
            ws = await self._ensure_connection()
            
            # Start synthesis session
            start_request = {
                "header": {
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "namespace": "FlowingSpeechSynthesizer",
                    "name": "StartSynthesis",
                    "appkey": self.appkey,
                },
                "payload": {
                    "voice": self.voice,
                    "format": self.audio_format,
                    "sample_rate": self.sample_rate,
                    "volume": self.volume,
                    "speech_rate": self.speech_rate,
                    "pitch_rate": self.pitch_rate,
                    "enable_subtitle": False,
                },
            }
            await ws.send(json.dumps(start_request))
            
            # Wait for synthesis started
            synthesis_started = False
            while not synthesis_started:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    header = data.get("header", {})
                    if header.get("name") == "SynthesisStarted":
                        synthesis_started = True
                        logger.debug("TTS synthesis started")
                    elif header.get("name") == "TaskFailed":
                        error = data.get("payload", {}).get("error_info", {})
                        raise Exception(f"TTS failed: {error.get('error_message', 'Unknown')}")
            
            # Send text
            run_request = {
                "header": {
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "namespace": "FlowingSpeechSynthesizer",
                    "name": "RunSynthesis",
                    "appkey": self.appkey,
                },
                "payload": {"text": cleaned_text},
            }
            await ws.send(json.dumps(run_request))
            
            # Send stop signal
            stop_request = {
                "header": {
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "namespace": "FlowingSpeechSynthesizer",
                    "name": "StopSynthesis",
                    "appkey": self.appkey,
                }
            }
            await ws.send(json.dumps(stop_request))
            
            # Receive audio data
            synthesis_completed = False
            while not synthesis_completed:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                
                if isinstance(msg, (bytes, bytearray)):
                    # Audio data
                    if on_audio:
                        on_audio(msg)
                    yield msg
                    
                elif isinstance(msg, str):
                    data = json.loads(msg)
                    header = data.get("header", {})
                    event_name = header.get("name")
                    
                    if event_name == "SynthesisCompleted":
                        synthesis_completed = True
                        logger.debug("TTS synthesis completed")
                    elif event_name == "TaskFailed":
                        error = data.get("payload", {}).get("error_info", {})
                        raise Exception(f"TTS failed: {error.get('error_message', 'Unknown')}")
            
            self.last_active_time = time.time()
            
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")
            # Close connection on error
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
                self.ws = None
                self.last_active_time = None
            raise
    
    async def start_session(self):
        """
        Start a streaming TTS session.
        
        For continuous text streaming, call this first, then send_text multiple times,
        then finish_session.
        """
        self.ws = await self._ensure_connection()
        
        start_request = {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": self.task_id,
                "namespace": "FlowingSpeechSynthesizer",
                "name": "StartSynthesis",
                "appkey": self.appkey,
            },
            "payload": {
                "voice": self.voice,
                "format": self.audio_format,
                "sample_rate": self.sample_rate,
                "volume": self.volume,
                "speech_rate": self.speech_rate,
                "pitch_rate": self.pitch_rate,
                "enable_subtitle": False,
            },
        }
        await self.ws.send(json.dumps(start_request))
        
        # Wait for started confirmation
        while True:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=10)
            if isinstance(msg, str):
                data = json.loads(msg)
                if data.get("header", {}).get("name") == "SynthesisStarted":
                    logger.debug("TTS session started")
                    break
                elif data.get("header", {}).get("name") == "TaskFailed":
                    raise Exception("Failed to start TTS session")
    
    async def send_text(self, text: str):
        """
        Send text for synthesis in an active session.
        
        Args:
            text: Text to synthesize
        """
        if not self.ws:
            raise RuntimeError("No active TTS session. Call start_session first.")
        
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return
        
        run_request = {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": self.task_id,
                "namespace": "FlowingSpeechSynthesizer",
                "name": "RunSynthesis",
                "appkey": self.appkey,
            },
            "payload": {"text": cleaned_text},
        }
        await self.ws.send(json.dumps(run_request))
        self.last_active_time = time.time()
    
    async def finish_session(self):
        """Finish the current TTS session."""
        if not self.ws:
            return
        
        stop_request = {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": self.task_id,
                "namespace": "FlowingSpeechSynthesizer",
                "name": "StopSynthesis",
                "appkey": self.appkey,
            }
        }
        await self.ws.send(json.dumps(stop_request))
        self.last_active_time = time.time()
        logger.debug("TTS session finished")
    
    async def receive_audio(self) -> AsyncGenerator[bytes, None]:
        """
        Receive audio data from an active session.
        
        Yields:
            Audio data chunks
        """
        if not self.ws:
            return
        
        try:
            while True:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=30)
                
                if isinstance(msg, (bytes, bytearray)):
                    yield msg
                elif isinstance(msg, str):
                    data = json.loads(msg)
                    header = data.get("header", {})
                    if header.get("name") == "SynthesisCompleted":
                        break
                    elif header.get("name") == "TaskFailed":
                        error = data.get("payload", {}).get("error_info", {})
                        logger.error(f"TTS error: {error}")
                        break
        except asyncio.TimeoutError:
            logger.warning("TTS receive timeout")
        except websockets.ConnectionClosed:
            logger.info("TTS connection closed")
