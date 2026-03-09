"""
Data Transfer Objects for TTS module.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class InterfaceType(Enum):
    """TTS interface type."""
    NON_STREAM = "non_stream"     # Batch synthesis, return complete audio
    STREAM = "stream"             # Streaming output
    DUAL_STREAM = "dual_stream"   # Streaming input and output


class SentenceType(Enum):
    """Sentence position type for TTS queue management."""
    FIRST = "first"    # First sentence, triggers session start
    MIDDLE = "middle"  # Middle sentence
    LAST = "last"      # Last sentence, triggers session end


class ContentType(Enum):
    """Content type for TTS input."""
    TEXT = "text"      # Text content to synthesize
    FILE = "file"      # Pre-synthesized audio file


@dataclass
class TTSConfig:
    """TTS provider configuration."""
    access_key_id: str
    access_key_secret: str
    appkey: str
    host: str = "nls-gateway-cn-beijing.aliyuncs.com"
    voice: str = "xiaoyun"       # Default voice
    format: str = "pcm"          # Output format: pcm, mp3, wav
    sample_rate: int = 16000     # Sample rate
    volume: int = 50             # Volume (0-100)
    speech_rate: int = 0         # Speech rate (-500 to 500)
    pitch_rate: int = 0          # Pitch (-500 to 500)


@dataclass
class TTSResult:
    """TTS synthesis result."""
    audio_data: bytes
    format: str = "pcm"
    sample_rate: int = 16000
    text: str = ""
    
    @property
    def duration_ms(self) -> int:
        """Estimate audio duration in milliseconds."""
        if self.format == "pcm":
            # 16-bit PCM: 2 bytes per sample
            samples = len(self.audio_data) // 2
            return int(samples * 1000 / self.sample_rate)
        return 0
