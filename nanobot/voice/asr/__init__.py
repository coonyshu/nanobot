"""
ASR (Automatic Speech Recognition) providers for voice module.

Supported providers:
- AliyunStreamASR: Aliyun NLS streaming ASR
- WhisperASR: Local Whisper ASR via faster-whisper
- FunASR: Local FunASR Paraformer streaming ASR
"""

from .base import ASRProviderBase
from .aliyun_stream import AliyunStreamASR
from .dto import ASRResult, InterfaceType

__all__ = [
    "ASRProviderBase",
    "AliyunStreamASR",
    "ASRResult",
    "InterfaceType",
]

# Lazy imports for optional providers
def get_whisper_asr():
    """Get WhisperASR class (requires faster-whisper)."""
    from .whisper_asr import WhisperASR
    return WhisperASR

def get_funasr():
    """Get FunASR class (requires websockets)."""
    from .funasr import FunASR
    return FunASR
