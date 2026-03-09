"""
TTS (Text-to-Speech) providers for voice module.

Supported providers:
- AliyunStreamTTS: Aliyun NLS streaming TTS
- EdgeTTS: Microsoft Edge TTS (free, no API key)
- PiperTTS: Local Piper TTS (ONNX-based)
- CosyVoiceTTS: Local CosyVoice TTS (high-quality Chinese)
"""

from .base import TTSProviderBase
from .aliyun_stream import AliyunStreamTTS

__all__ = [
    "TTSProviderBase",
    "AliyunStreamTTS",
]

# Lazy imports for optional providers
def get_edge_tts():
    """Get EdgeTTS class (requires edge-tts)."""
    from .edge_tts import EdgeTTS
    return EdgeTTS

def get_piper_tts():
    """Get PiperTTS class (requires piper-tts)."""
    from .piper_tts import PiperTTS
    return PiperTTS

def get_cosyvoice_tts():
    """Get CosyVoiceTTS class (requires cosyvoice)."""
    from .cosyvoice_tts import CosyVoiceTTS
    return CosyVoiceTTS
