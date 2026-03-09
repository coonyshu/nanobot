"""
Base TTS (Text-to-Speech) provider.

Adapted from xiaozhi-esp32-server with modifications for nanobot integration:
- Removed ConnectionHandler dependency
- Simplified queue management for async patterns
- Direct audio streaming support
"""

import os
import re
import uuid
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Callable, AsyncGenerator, List

from .dto import InterfaceType, SentenceType, TTSResult

logger = logging.getLogger(__name__)


class TTSProviderBase(ABC):
    """
    Base class for TTS (Text-to-Speech) providers.
    
    Provides common functionality for text processing, audio output,
    and streaming synthesis. Subclasses implement specific provider APIs.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize TTS provider.
        
        Args:
            config: Provider configuration dictionary
        """
        self.config = config or {}
        self.interface_type = InterfaceType.NON_STREAM
        self.output_dir = self.config.get("output_dir", "./audio_output")
        self.audio_format = self.config.get("format", "pcm")
        self.sample_rate = self.config.get("sample_rate", 16000)
        
        # Text processing
        self.punctuations = ("。", "？", "?", "！", "!", "；", ";", "：")
        self.first_sentence_punctuations = (
            "，", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "："
        )
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
    
    async def initialize(self):
        """Initialize provider resources. Override in subclasses if needed."""
        pass
    
    async def close(self):
        """Clean up provider resources. Override in subclasses if needed."""
        pass
    
    @abstractmethod
    async def text_to_speech(self, text: str) -> TTSResult:
        """
        Convert text to speech audio.
        
        Args:
            text: Text to synthesize
            
        Returns:
            TTSResult with audio data
        """
        pass
    
    async def synthesize_stream(
        self, 
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream-based text-to-speech synthesis.
        
        For providers that support streaming, yields audio chunks as they
        become available. For non-streaming providers, yields the complete
        audio in one chunk.
        
        Args:
            text: Text to synthesize
            on_audio: Optional callback for each audio chunk
            
        Yields:
            Audio data chunks
        """
        # Default implementation for non-streaming providers
        result = await self.text_to_speech(text)
        if result.audio_data:
            if on_audio:
                on_audio(result.audio_data)
            yield result.audio_data
    
    def generate_filename(self, extension: str = ".wav") -> str:
        """Generate unique filename for audio output."""
        return os.path.join(
            self.output_dir,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex[:8]}{extension}",
        )
    
    @staticmethod
    def clean_text(text: str) -> str:
        """
        Clean text for TTS synthesis.
        
        Removes markdown formatting, emojis, extra whitespace, and normalizes text.
        
        Args:
            text: Input text
            
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        # Remove markdown formatting
        # Bold/italic
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
        
        # Headers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Links [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Images ![alt](url) -> alt
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        
        # Code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Lists
        text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Blockquotes
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        
        # Horizontal rules
        text = re.sub(r'^[-*_]{3,}$', '', text, flags=re.MULTILINE)
        
        # Remove emojis and other symbol characters
        import unicodedata
        # Characters to always strip: variation selectors and zero-width joiner (used in compound emojis)
        _emoji_extras = {'\ufe0e', '\ufe0f', '\u200d', '\u20e3'}
        cleaned_chars = []
        for char in text:
            if char in _emoji_extras:
                continue
            cat = unicodedata.category(char)
            # Skip So (Other Symbol) - covers most emojis/icons
            if cat == 'So':
                continue
            cleaned_chars.append(char)
        text = ''.join(cleaned_chars)
        
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = text.strip()
        
        return text
    
    def segment_text(self, text: str, is_first: bool = True) -> List[str]:
        """
        Segment text into TTS-friendly chunks.
        
        Splits text at natural boundaries (punctuation) for better synthesis.
        
        Args:
            text: Input text
            is_first: Whether this is the first segment (uses different punctuation set)
            
        Returns:
            List of text segments
        """
        if not text:
            return []
        
        # Choose punctuation set
        puncts = self.first_sentence_punctuations if is_first else self.punctuations
        
        # Build regex pattern
        pattern = f"([{''.join(re.escape(p) for p in puncts)}])"
        
        # Split and rejoin with punctuation
        parts = re.split(pattern, text)
        segments = []
        current = ""
        
        for part in parts:
            if not part:
                continue
            current += part
            if part in puncts:
                if current.strip():
                    segments.append(current.strip())
                current = ""
        
        # Add remaining text
        if current.strip():
            segments.append(current.strip())
        
        return segments
    
    @staticmethod
    def remove_punctuation(text: str) -> str:
        """Remove punctuation and emojis from text."""
        import unicodedata
        
        result = []
        for char in text:
            category = unicodedata.category(char)
            # Skip punctuation (P*) and symbols (S*)
            if not category.startswith('P') and not category.startswith('S'):
                result.append(char)
        
        return ''.join(result).strip()
