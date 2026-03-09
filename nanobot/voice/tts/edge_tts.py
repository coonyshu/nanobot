"""
Edge TTS Provider (Free).

Implements text-to-speech using Microsoft Edge TTS service.

Features:
- Free, no API key required
- High-quality neural voices
- Multiple languages and voices
- Streaming synthesis support

Reference: xiaozhi-esp32-server core/providers/tts/edge.py
"""

import io
import logging
from typing import Optional, Callable, AsyncGenerator

from .base import TTSProviderBase
from .dto import InterfaceType, TTSResult

logger = logging.getLogger(__name__)


class EdgeTTS(TTSProviderBase):
    """
    Microsoft Edge TTS Provider.
    
    Uses the edge-tts library for free, high-quality text-to-speech synthesis.
    No API key required.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize Edge TTS provider.
        
        Args:
            config: Configuration dictionary with keys:
                - voice: Voice name (default: zh-CN-XiaoxiaoNeural)
                - rate: Speech rate adjustment (e.g., "+10%", "-5%")
                - volume: Volume adjustment (e.g., "+20%", "-10%")
                - pitch: Pitch adjustment (e.g., "+5Hz", "-10Hz")
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.NON_STREAM
        self.audio_format = "mp3"  # Edge TTS outputs MP3 format
        
        # Configuration
        self.voice = self.config.get("voice", "zh-CN-XiaoxiaoNeural")
        self.rate = self.config.get("rate", "+0%")
        self.volume_adjust = self.config.get("volume", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        
        logger.info(f"EdgeTTS initialized: voice={self.voice}")
    
    async def text_to_speech(self, text: str) -> TTSResult:
        """
        Convert text to speech using Edge TTS.
        
        Args:
            text: Text to synthesize
            
        Returns:
            TTSResult with audio data (MP3 format)
        """
        try:
            import edge_tts
        except ImportError:
            raise ImportError(
                "edge-tts is required for Edge TTS. "
                "Install with: pip install edge-tts"
            )
        
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return TTSResult(audio_data=b"", format="mp3", text=text)
        
        try:
            communicate = edge_tts.Communicate(
                cleaned_text,
                voice=self.voice,
                rate=self.rate,
                volume=self.volume_adjust,
                pitch=self.pitch,
            )
            
            audio_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes += chunk["data"]
            
            logger.debug(f"EdgeTTS synthesized: {len(audio_bytes)} bytes for '{cleaned_text[:50]}...'")
            
            return TTSResult(
                audio_data=audio_bytes,
                format="mp3",
                sample_rate=self.sample_rate,
                text=cleaned_text,
            )
            
        except Exception as e:
            logger.error(f"Edge TTS synthesis failed: {e}")
            raise
    
    async def synthesize_stream(
        self,
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream-based text-to-speech synthesis.
        
        Yields MP3 audio chunks as they become available from Edge TTS.
        
        Args:
            text: Text to synthesize
            on_audio: Optional callback for each audio chunk
            
        Yields:
            MP3 audio data chunks
        """
        try:
            import edge_tts
        except ImportError:
            raise ImportError(
                "edge-tts is required for Edge TTS. "
                "Install with: pip install edge-tts"
            )
        
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return
        
        try:
            communicate = edge_tts.Communicate(
                cleaned_text,
                voice=self.voice,
                rate=self.rate,
                volume=self.volume_adjust,
                pitch=self.pitch,
            )
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data = chunk["data"]
                    if on_audio:
                        on_audio(audio_data)
                    yield audio_data
                    
        except Exception as e:
            logger.error(f"Edge TTS streaming synthesis failed: {e}")
            raise
