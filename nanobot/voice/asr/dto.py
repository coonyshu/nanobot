"""
Data Transfer Objects for ASR module.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class InterfaceType(Enum):
    """ASR interface type."""
    NON_STREAM = "non_stream"  # File-based batch recognition
    STREAM = "stream"          # Real-time streaming recognition


@dataclass
class ASRResult:
    """ASR recognition result."""
    text: str
    is_final: bool = True
    confidence: float = 1.0
    language: Optional[str] = None
    speaker: Optional[str] = None
    emotion: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"content": self.text, "is_final": self.is_final}
        if self.confidence < 1.0:
            result["confidence"] = self.confidence
        if self.language:
            result["language"] = self.language
        if self.speaker:
            result["speaker"] = self.speaker
        if self.emotion:
            result["emotion"] = self.emotion
        return result


@dataclass 
class ASRConfig:
    """ASR provider configuration."""
    access_key_id: str
    access_key_secret: str
    appkey: str
    host: str = "nls-gateway-cn-shanghai.aliyuncs.com"
    max_sentence_silence: int = 800  # ms
    enable_punctuation: bool = True
    enable_itn: bool = True  # Inverse Text Normalization
    sample_rate: int = 16000
    audio_format: str = "pcm"
