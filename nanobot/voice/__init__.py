"""
Voice interaction module for nanobot-service.

This module provides ASR (Automatic Speech Recognition) and TTS (Text-to-Speech) 
capabilities for the personal AI assistant, enabling voice-based interaction.

Components:
- asr: Speech-to-text providers (Aliyun streaming)
- tts: Text-to-speech providers (Aliyun streaming)  
- vad: Voice Activity Detection (Silero VAD)
- utils: Audio encoding/decoding utilities
- session: Voice session management
- gateway: WebSocket voice gateway handler
"""

from .session import VoiceSession, VoiceSessionManager, SessionState, ListenMode
from .config import (
    VoiceModuleConfig, ASRConfig, TTSConfig, VADConfig, 
    get_voice_config
)
from .gateway import VoiceWebSocketHandler, VoiceConfig, MessageType

__all__ = [
    # Session management
    "VoiceSession",
    "VoiceSessionManager",
    "SessionState",
    "ListenMode",
    # Configuration
    "VoiceModuleConfig",
    "ASRConfig",
    "TTSConfig",
    "VADConfig",
    "get_voice_config",
    # Gateway
    "VoiceWebSocketHandler",
    "VoiceConfig",
    "MessageType",
]

__version__ = "0.1.0"
