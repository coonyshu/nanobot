"""
Base ASR (Automatic Speech Recognition) provider.

Adapted from xiaozhi-esp32-server with modifications for nanobot integration:
- Removed ConnectionHandler dependency
- Uses VoiceSession for state management
- Simplified for nanobot Agent loop integration
"""

import io
import os
import wave
import uuid
import json
import time
import asyncio
import tempfile
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List, NamedTuple, Callable

from .dto import InterfaceType, ASRResult

logger = logging.getLogger(__name__)


class AudioArtifacts(NamedTuple):
    """Audio processing artifacts."""
    pcm_frames: List[bytes]
    pcm_bytes: bytes
    file_path: Optional[str]
    temp_path: Optional[str]


class ASRProviderBase(ABC):
    """
    Base class for ASR (Automatic Speech Recognition) providers.
    
    Provides common functionality for audio processing, Opus decoding,
    and speech recognition. Subclasses implement specific provider APIs.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize ASR provider.
        
        Args:
            config: Provider configuration dictionary
        """
        self.config = config or {}
        self.interface_type = InterfaceType.NON_STREAM
        self.output_dir = self.config.get("output_dir", "./audio_output")
        self._current_artifacts: Optional[AudioArtifacts] = None
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
    
    async def initialize(self):
        """Initialize provider resources. Override in subclasses if needed."""
        pass
    
    async def close(self):
        """Clean up provider resources. Override in subclasses if needed."""
        pass
    
    @abstractmethod
    async def speech_to_text(
        self,
        audio_data: List[bytes],
        session_id: str,
        audio_format: str = "opus",
        artifacts: Optional[AudioArtifacts] = None,
    ) -> ASRResult:
        """
        Convert speech audio to text.
        
        Args:
            audio_data: List of audio frames (Opus or PCM)
            session_id: Session identifier
            audio_format: Audio format ("opus" or "pcm")
            artifacts: Pre-processed audio artifacts
            
        Returns:
            ASRResult with recognition text and metadata
        """
        pass
    
    async def process_audio(
        self,
        audio_data: List[bytes],
        session_id: str,
        audio_format: str = "opus",
    ) -> ASRResult:
        """
        Process audio and return recognition result.
        
        This is the main entry point for ASR processing. It handles:
        - Opus decoding if needed
        - Audio artifact preparation
        - Calling the provider-specific speech_to_text method
        
        Args:
            audio_data: List of audio frames
            session_id: Session identifier
            audio_format: Audio format ("opus" or "pcm")
            
        Returns:
            ASRResult with recognition text
        """
        try:
            # Decode Opus to PCM if needed
            if audio_format == "opus":
                pcm_frames = self.decode_opus(audio_data)
            else:
                pcm_frames = audio_data
            
            # Combine PCM frames
            pcm_bytes = b"".join(pcm_frames)
            
            if len(pcm_bytes) == 0:
                logger.warning("Empty audio data, skipping ASR")
                return ASRResult(text="", is_final=True)
            
            # Build artifacts
            file_path = None
            temp_path = None
            
            if self.requires_file():
                if self.prefers_temp_file():
                    temp_path = self._build_temp_file(pcm_bytes)
                else:
                    file_path = self._save_audio_file(pcm_frames, session_id)
            
            artifacts = AudioArtifacts(
                pcm_frames=pcm_frames,
                pcm_bytes=pcm_bytes,
                file_path=file_path,
                temp_path=temp_path,
            )
            self._current_artifacts = artifacts
            
            # Call provider implementation
            result = await self.speech_to_text(
                audio_data, session_id, audio_format, artifacts
            )
            
            return result
            
        except Exception as e:
            logger.error(f"ASR processing failed: {e}")
            return ASRResult(text="", is_final=True)
        finally:
            # Cleanup temp files
            self._cleanup_temp_files()
    
    def requires_file(self) -> bool:
        """Whether this provider requires file input. Override if needed."""
        return False
    
    def prefers_temp_file(self) -> bool:
        """Whether to use temporary files. Override if needed."""
        return False
    
    def get_current_artifacts(self) -> Optional[AudioArtifacts]:
        """Get current audio artifacts."""
        return self._current_artifacts
    
    @staticmethod
    def decode_opus(opus_data: List[bytes]) -> List[bytes]:
        """
        Decode Opus audio data to PCM.
        
        Args:
            opus_data: List of Opus-encoded audio frames
            
        Returns:
            List of PCM audio frames
        """
        try:
            import opuslib_next
        except ImportError:
            logger.error("opuslib_next not installed. Run: pip install opuslib-next")
            return []
        
        decoder = None
        try:
            decoder = opuslib_next.Decoder(16000, 1)
            pcm_data = []
            buffer_size = 960  # 60ms at 16kHz
            
            for i, opus_packet in enumerate(opus_data):
                try:
                    if not opus_packet or len(opus_packet) == 0:
                        continue
                    
                    pcm_frame = decoder.decode(opus_packet, buffer_size)
                    if pcm_frame and len(pcm_frame) > 0:
                        pcm_data.append(pcm_frame)
                        
                except Exception as e:
                    logger.warning(f"Opus decode error at packet {i}: {e}")
            
            return pcm_data
            
        except Exception as e:
            logger.error(f"Opus decoding failed: {e}")
            return []
        finally:
            if decoder is not None:
                try:
                    del decoder
                except Exception:
                    pass
    
    @staticmethod
    def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
        """
        Convert PCM data to WAV format.
        
        Args:
            pcm_data: Raw PCM audio bytes
            sample_rate: Sample rate in Hz
            
        Returns:
            WAV-formatted audio bytes
        """
        if len(pcm_data) == 0:
            logger.warning("Empty PCM data")
            return b""
        
        # Ensure even length for 16-bit audio
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]
        
        wav_buffer = io.BytesIO()
        try:
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_data)
            
            wav_buffer.seek(0)
            return wav_buffer.read()
        except Exception as e:
            logger.error(f"WAV conversion failed: {e}")
            return b""
    
    def _build_temp_file(self, pcm_bytes: bytes) -> Optional[str]:
        """Build temporary WAV file from PCM data."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            
            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm_bytes)
            
            return temp_path
        except Exception as e:
            logger.error(f"Temp file creation failed: {e}")
            return None
    
    def _save_audio_file(self, pcm_frames: List[bytes], session_id: str) -> str:
        """Save PCM data as WAV file."""
        file_name = f"asr_{session_id}_{uuid.uuid4().hex[:8]}.wav"
        file_path = os.path.join(self.output_dir, file_name)
        
        with wave.open(file_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"".join(pcm_frames))
        
        return file_path
    
    def _cleanup_temp_files(self):
        """Clean up temporary files."""
        if self._current_artifacts:
            if self._current_artifacts.temp_path:
                try:
                    if os.path.exists(self._current_artifacts.temp_path):
                        os.unlink(self._current_artifacts.temp_path)
                except Exception as e:
                    logger.warning(f"Temp file cleanup failed: {e}")
            self._current_artifacts = None
