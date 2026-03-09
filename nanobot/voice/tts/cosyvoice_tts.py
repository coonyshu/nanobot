"""
CosyVoice TTS Provider (Local).

Implements local text-to-speech using Alibaba's CosyVoice model.

Features:
- High-quality Chinese speech synthesis
- Multiple preset speakers
- Zero-shot voice cloning support
- Cross-lingual synthesis
- Streaming synthesis support

CosyVoice project: https://github.com/FunAudioLLM/CosyVoice
"""

import io
import wave
import logging
import asyncio
from typing import Optional, Callable, AsyncGenerator

from .base import TTSProviderBase
from .dto import InterfaceType, TTSResult

logger = logging.getLogger(__name__)


class CosyVoiceTTS(TTSProviderBase):
    """
    CosyVoice TTS Provider (Local).
    
    Uses Alibaba's CosyVoice for high-quality Chinese speech synthesis.
    Requires PyTorch and CosyVoice model files.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize CosyVoice TTS provider.
        
        Args:
            config: Configuration dictionary with keys:
                - model_path: Path to CosyVoice model directory (required)
                - speaker: Preset speaker name (default: "中文女")
                - mode: Synthesis mode: "sft"/"zero_shot"/"cross_lingual" (default: "sft")
                - ref_audio_path: Reference audio for zero-shot cloning (optional)
                - ref_text: Reference text for zero-shot cloning (optional)
                - sample_rate: Output sample rate (model default: 22050)
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.NON_STREAM
        
        # Configuration
        self.model_path = self.config.get("model_path", "")
        self.speaker = self.config.get("speaker", "中文女")
        self.mode = self.config.get("mode", "sft")
        self.ref_audio_path = self.config.get("ref_audio_path", "")
        self.ref_text = self.config.get("ref_text", "")
        self.sample_rate = int(self.config.get("sample_rate", 22050))
        
        # Model instance (lazy loaded)
        self._model = None
        
        logger.info(
            f"CosyVoiceTTS initialized: model={self.model_path}, "
            f"speaker={self.speaker}, mode={self.mode}"
        )
    
    async def initialize(self):
        """Pre-load model to avoid first-request latency."""
        await asyncio.get_event_loop().run_in_executor(None, self._get_model)
    
    async def close(self):
        """Clean up model resources."""
        self._model = None
        logger.info("CosyVoiceTTS model unloaded")
    
    def _get_model(self):
        """Lazy-load the CosyVoice model."""
        if self._model is not None:
            return self._model
        
        if not self.model_path:
            raise ValueError(
                "CosyVoice TTS requires model_path. "
                "Download from: https://github.com/FunAudioLLM/CosyVoice"
            )
        
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice
        except ImportError:
            raise ImportError(
                "cosyvoice is required for CosyVoice TTS. "
                "Install from: https://github.com/FunAudioLLM/CosyVoice"
            )
        
        logger.info(f"Loading CosyVoice model: {self.model_path}")
        self._model = CosyVoice(self.model_path)
        logger.info(f"CosyVoice model loaded: {self.model_path}")
        
        return self._model
    
    def _synthesize_sft(self, text: str):
        """SFT mode: Use preset speaker voices."""
        model = self._get_model()
        return model.inference_sft(text, self.speaker)
    
    def _synthesize_zero_shot(self, text: str):
        """Zero-shot mode: Clone voice from reference audio."""
        model = self._get_model()
        
        if not self.ref_audio_path or not self.ref_text:
            raise ValueError(
                "Zero-shot mode requires ref_audio_path and ref_text"
            )
        
        return model.inference_zero_shot(
            text, self.ref_text, self.ref_audio_path
        )
    
    def _synthesize_cross_lingual(self, text: str):
        """Cross-lingual mode: Synthesize in different language."""
        model = self._get_model()
        
        if not self.ref_audio_path:
            raise ValueError(
                "Cross-lingual mode requires ref_audio_path"
            )
        
        return model.inference_cross_lingual(text, self.ref_audio_path)
    
    def _synthesize(self, text: str):
        """Route to appropriate synthesis mode."""
        if self.mode == "zero_shot":
            return self._synthesize_zero_shot(text)
        elif self.mode == "cross_lingual":
            return self._synthesize_cross_lingual(text)
        else:
            return self._synthesize_sft(text)
    
    async def text_to_speech(self, text: str) -> TTSResult:
        """
        Convert text to speech using CosyVoice.
        
        Args:
            text: Text to synthesize
            
        Returns:
            TTSResult with PCM audio data
        """
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return TTSResult(audio_data=b"", format="pcm", text=text)
        
        try:
            import numpy as np
        except ImportError:
            raise ImportError("numpy is required for CosyVoice TTS")
        
        # Run synthesis in executor to not block event loop
        loop = asyncio.get_event_loop()
        output_generator = await loop.run_in_executor(
            None, self._synthesize, cleaned_text
        )
        
        # Collect all audio chunks
        pcm_chunks = []
        for chunk in output_generator:
            # CosyVoice returns dict with 'tts_speech' tensor
            audio_tensor = chunk.get("tts_speech", chunk)
            
            # Convert tensor to numpy
            if hasattr(audio_tensor, "numpy"):
                audio_np = audio_tensor.numpy()
            else:
                audio_np = np.array(audio_tensor)
            
            # Flatten and convert to 16-bit PCM
            audio_np = audio_np.flatten()
            audio_int16 = (audio_np * 32767).astype(np.int16)
            pcm_chunks.append(audio_int16.tobytes())
        
        pcm_data = b"".join(pcm_chunks)
        
        logger.debug(
            f"CosyVoiceTTS synthesized: {len(pcm_data)} bytes PCM, "
            f"sample_rate={self.sample_rate}"
        )
        
        return TTSResult(
            audio_data=pcm_data,
            format="pcm",
            sample_rate=self.sample_rate,
            text=cleaned_text,
        )
    
    async def synthesize_stream(
        self,
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream-based text-to-speech synthesis.
        
        CosyVoice supports streaming via generator output.
        Each yield provides a chunk of audio.
        
        Args:
            text: Text to synthesize
            on_audio: Optional callback for each audio chunk
            
        Yields:
            PCM audio data chunks
        """
        cleaned_text = self.clean_text(text)
        if not cleaned_text:
            return
        
        try:
            import numpy as np
        except ImportError:
            raise ImportError("numpy is required for CosyVoice TTS")
        
        # Segment text for streaming
        segments = self.segment_text(cleaned_text, is_first=True)
        if not segments:
            segments = [cleaned_text]
        
        for segment in segments:
            loop = asyncio.get_event_loop()
            output_generator = await loop.run_in_executor(
                None, self._synthesize, segment
            )
            
            for chunk in output_generator:
                audio_tensor = chunk.get("tts_speech", chunk)
                
                if hasattr(audio_tensor, "numpy"):
                    audio_np = audio_tensor.numpy()
                else:
                    audio_np = np.array(audio_tensor)
                
                audio_np = audio_np.flatten()
                audio_int16 = (audio_np * 32767).astype(np.int16)
                pcm_data = audio_int16.tobytes()
                
                if pcm_data:
                    if on_audio:
                        on_audio(pcm_data)
                    yield pcm_data
