"""
Piper TTS Provider (Local).

Implements local text-to-speech using Piper TTS.

Features:
- Fully offline, local inference
- ONNX-based, lightweight and fast
- Multiple language and voice support
- Configurable speech rate via length_scale

Piper project: https://github.com/rhasspy/piper
"""

import io
import wave
import logging
from typing import Optional, Callable, AsyncGenerator, List

from .base import TTSProviderBase
from .dto import InterfaceType, TTSResult

logger = logging.getLogger(__name__)


class PiperTTS(TTSProviderBase):
    """
    Local Piper TTS Provider.
    
    Uses Piper for fast, offline text-to-speech synthesis.
    Requires ONNX model files (.onnx + .onnx.json).
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize Piper TTS provider.
        
        Args:
            config: Configuration dictionary with keys:
                - model_path: Path to .onnx model file (required)
                - config_path: Path to .onnx.json config file (auto-detected)
                - speaker_id: Speaker ID for multi-speaker models (default: 0)
                - length_scale: Speech rate (1.0=normal, <1.0=faster, >1.0=slower)
                - noise_scale: Noise scale for variation (default: 0.667)
                - noise_w: Noise width (default: 0.8)
                - sentence_silence: Silence between sentences in seconds (default: 0.3)
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.NON_STREAM
        
        # Configuration
        self.model_path = self.config.get("model_path", "")
        self.config_path = self.config.get("config_path", "")
        self.speaker_id = int(self.config.get("speaker_id", 0))
        self.length_scale = float(self.config.get("length_scale", 1.0))
        self.noise_scale = float(self.config.get("noise_scale", 0.667))
        self.noise_w = float(self.config.get("noise_w", 0.8))
        self.sentence_silence = float(self.config.get("sentence_silence", 0.3))
        
        # Piper voice instance (lazy loaded)
        self._voice = None
        
        logger.info(
            f"PiperTTS initialized: model={self.model_path}, "
            f"speaker_id={self.speaker_id}, length_scale={self.length_scale}"
        )
    
    async def initialize(self):
        """Pre-load model to avoid first-request latency."""
        self._get_voice()
    
    async def close(self):
        """Clean up model resources."""
        self._voice = None
        logger.info("PiperTTS model unloaded")
    
    def _get_voice(self):
        """Lazy-load the Piper voice model."""
        if self._voice is not None:
            return self._voice
        
        if not self.model_path:
            raise ValueError(
                "Piper TTS requires model_path. "
                "Download models from: https://github.com/rhasspy/piper/releases"
            )
        
        try:
            from piper import PiperVoice
        except ImportError:
            raise ImportError(
                "piper-tts is required for Piper TTS. "
                "Install with: pip install piper-tts"
            )
        
        config_path = self.config_path
        if not config_path:
            # Auto-detect config file
            config_path = self.model_path + ".json"
        
        logger.info(f"Loading Piper model: {self.model_path}")
        
        self._voice = PiperVoice.load(
            self.model_path,
            config_path=config_path,
        )
        
        logger.info(f"Piper model loaded: {self.model_path}")
        return self._voice
    
    async def text_to_speech(self, text: str) -> TTSResult:
        """
        Convert text to speech using Piper.
        
        Args:
            text: Text to synthesize
            
        Returns:
            TTSResult with PCM audio data
        """
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return TTSResult(audio_data=b"", format="pcm", text=text)
        
        voice = self._get_voice()
        
        # Synthesize to WAV in memory
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            voice.synthesize(
                cleaned_text,
                wav_file,
                speaker_id=self.speaker_id,
                length_scale=self.length_scale,
                noise_scale=self.noise_scale,
                noise_w=self.noise_w,
                sentence_silence=self.sentence_silence,
            )
        
        # Extract PCM data from WAV (skip header)
        wav_buffer.seek(0)
        with wave.open(wav_buffer, "rb") as wav_file:
            pcm_data = wav_file.readframes(wav_file.getnframes())
            piper_sample_rate = wav_file.getframerate()
        
        logger.debug(
            f"PiperTTS synthesized: {len(pcm_data)} bytes PCM, "
            f"sample_rate={piper_sample_rate}"
        )
        
        return TTSResult(
            audio_data=pcm_data,
            format="pcm",
            sample_rate=piper_sample_rate,
            text=cleaned_text,
        )
    
    async def synthesize_stream(
        self,
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream-based text-to-speech synthesis.
        
        Piper doesn't natively support streaming, so we segment text
        and synthesize each segment separately for pseudo-streaming.
        
        Args:
            text: Text to synthesize
            on_audio: Optional callback for each audio chunk
            
        Yields:
            PCM audio data chunks
        """
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return
        
        # Segment text for pseudo-streaming
        segments = self.segment_text(cleaned_text, is_first=True)
        
        if not segments:
            segments = [cleaned_text]
        
        for segment in segments:
            result = await self.text_to_speech(segment)
            if result.audio_data:
                if on_audio:
                    on_audio(result.audio_data)
                yield result.audio_data
