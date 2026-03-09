"""
Whisper ASR Provider (Local).

Implements local speech recognition using faster-whisper.

Features:
- Local inference with faster-whisper (CTranslate2)
- Support for multiple model sizes (tiny/base/small/medium/large-v3)
- GPU (CUDA) and CPU support
- Configurable compute type (int8/float16/float32)
- Streaming-compatible interface (accumulates audio, batch recognition)
"""

import io
import logging
from typing import Optional, List, Callable

from .base import ASRProviderBase, AudioArtifacts
from .dto import InterfaceType, ASRResult

logger = logging.getLogger(__name__)


class WhisperASR(ASRProviderBase):
    """
    Local Whisper ASR Provider using faster-whisper.
    
    Uses CTranslate2-based faster-whisper for efficient local inference.
    Supports GPU acceleration with CUDA.
    
    Note: Whisper is not a true streaming model. This implementation
    accumulates audio data and performs batch recognition when stop_streaming
    is called.
    """
    
    def __init__(self, config: dict = None):
        """
        Initialize Whisper ASR provider.
        
        Args:
            config: Configuration dictionary with keys:
                - model_size: Model size (tiny/base/small/medium/large-v3)
                - device: Device (cpu/cuda/auto)
                - language: Language code (zh/en/auto)
                - compute_type: Compute type (int8/float16/float32)
                - model_path: Custom model path (optional)
                - beam_size: Beam size for decoding (default: 5)
                - initial_prompt: Initial prompt for context (optional)
        """
        super().__init__(config)
        
        self.interface_type = InterfaceType.NON_STREAM
        
        # Configuration
        self.model_size = self.config.get("model_size", "large-v3")
        self.device = self.config.get("device", "auto")
        self.language = self.config.get("language", "zh")
        self.compute_type = self.config.get("compute_type", "float16")
        self.model_path = self.config.get("model_path", "")
        self.beam_size = int(self.config.get("beam_size", 5))
        self.initial_prompt = self.config.get("initial_prompt", "")
        
        # Model instance (lazy loaded)
        self._model = None
        
        # Streaming state (accumulation mode)
        self._audio_buffer: List[bytes] = []
        self._is_streaming = False
        self._on_result: Optional[Callable[[ASRResult], None]] = None
        self._on_error: Optional[Callable[[Exception], None]] = None
        
        # Opus decoder (lazy initialized)
        self._decoder = None
        
        logger.info(
            f"WhisperASR initialized: model={self.model_size}, "
            f"device={self.device}, compute_type={self.compute_type}, "
            f"language={self.language}"
        )
    
    async def initialize(self):
        """Pre-load model to avoid first-request latency."""
        self._get_model()
    
    async def close(self):
        """Clean up model resources."""
        self._model = None
        self._audio_buffer = []
        self._is_streaming = False
        if self._decoder is not None:
            try:
                del self._decoder
                self._decoder = None
            except Exception:
                pass
        logger.info("WhisperASR model unloaded")
    
    @property
    def decoder(self):
        """Lazy-initialize Opus decoder."""
        if self._decoder is None:
            try:
                import opuslib_next
                self._decoder = opuslib_next.Decoder(16000, 1)
            except ImportError:
                logger.error("opuslib_next not installed")
                return None
        return self._decoder
    
    def _get_model(self):
        """Lazy-load the faster-whisper model."""
        if self._model is not None:
            return self._model
        
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "faster-whisper is required for Whisper ASR. "
                "Install with: pip install faster-whisper"
            )
        
        model_path = self.model_path or self.model_size
        
        # Auto-detect device
        device = self.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        
        # Adjust compute_type for CPU
        compute_type = self.compute_type
        if device == "cpu" and compute_type == "float16":
            compute_type = "int8"
            logger.info("CPU detected, switching compute_type to int8")
        
        logger.info(
            f"Loading Whisper model: {model_path} "
            f"(device={device}, compute_type={compute_type})"
        )
        
        self._model = WhisperModel(
            model_path,
            device=device,
            compute_type=compute_type,
        )
        
        logger.info(f"Whisper model loaded successfully: {model_path}")
        return self._model
    
    # ==================== Streaming Interface ====================
    # Note: Whisper doesn't support true streaming, so we accumulate
    # audio and perform batch recognition when stop_streaming is called.
    
    async def start_streaming(
        self,
        on_result: Optional[Callable[[ASRResult], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """
        Start streaming recognition session.
        
        Note: Whisper accumulates audio and performs batch recognition
        when stop_streaming is called.
        
        Args:
            on_result: Callback for recognition results (called on stop)
            on_error: Callback for errors
        """
        self._audio_buffer = []
        self._is_streaming = True
        self._on_result = on_result
        self._on_error = on_error
        
        # Pre-load model
        try:
            self._get_model()
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            if self._on_error:
                self._on_error(e)
            raise
        
        logger.debug("WhisperASR streaming started (accumulation mode)")
    
    async def send_audio(self, audio_chunk: bytes, is_opus: bool = True):
        """
        Send audio chunk for recognition.
        
        Accumulates audio data until stop_streaming is called.
        
        Args:
            audio_chunk: Audio data (Opus or PCM)
            is_opus: Whether audio is Opus-encoded
        """
        if not self._is_streaming:
            logger.warning("send_audio called but streaming not started")
            return
        
        try:
            if is_opus and self.decoder:
                # Decode Opus to PCM
                pcm_frame = self.decoder.decode(audio_chunk, 960)
                self._audio_buffer.append(pcm_frame)
            else:
                self._audio_buffer.append(audio_chunk)
        except Exception as e:
            logger.warning(f"Failed to process audio chunk: {e}")
    
    async def stop_streaming(self) -> str:
        """
        Stop streaming and perform batch recognition.
        
        Returns:
            Final recognized text
        """
        if not self._is_streaming:
            return ""
        
        self._is_streaming = False
        
        if not self._audio_buffer:
            logger.warning("No audio data accumulated")
            return ""
        
        try:
            import numpy as np
            
            # Combine all PCM frames
            pcm_bytes = b"".join(self._audio_buffer)
            
            if len(pcm_bytes) == 0:
                return ""
            
            # Convert PCM bytes to numpy float32 array
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            logger.info(f"WhisperASR recognizing {len(audio_array)/16000:.2f}s audio...")
            
            # Transcribe
            model = self._get_model()
            language = self.language if self.language != "auto" else None
            
            transcribe_kwargs = {
                "beam_size": self.beam_size,
                "language": language,
                "vad_filter": True,
                "vad_parameters": {
                    "min_silence_duration_ms": 300,
                },
            }
            
            if self.initial_prompt:
                transcribe_kwargs["initial_prompt"] = self.initial_prompt
            
            segments, info = model.transcribe(audio_array, **transcribe_kwargs)
            
            # Collect all segment texts
            texts = []
            for segment in segments:
                texts.append(segment.text.strip())
            
            result_text = "".join(texts)
            
            logger.info(
                f"Whisper ASR result: '{result_text}' "
                f"(language={info.language}, prob={info.language_probability:.2f})"
            )
            
            # Call result callback
            if self._on_result and result_text:
                result = ASRResult(
                    text=result_text,
                    is_final=True,
                    confidence=info.language_probability,
                    language=info.language,
                )
                self._on_result(result)
            
            return result_text
            
        except Exception as e:
            logger.error(f"Whisper ASR recognition failed: {e}")
            if self._on_error:
                self._on_error(e)
            return ""
        finally:
            # Clear buffer
            self._audio_buffer = []
    
    # ==================== Batch Interface ====================
    
    async def speech_to_text(
        self,
        audio_data: List[bytes],
        session_id: str,
        audio_format: str = "opus",
        artifacts: Optional[AudioArtifacts] = None,
    ) -> ASRResult:
        """
        Convert speech audio to text using Whisper (batch mode).
        
        Args:
            audio_data: List of audio frames (Opus or PCM)
            session_id: Session identifier
            audio_format: Audio format ("opus" or "pcm")
            artifacts: Pre-processed audio artifacts
            
        Returns:
            ASRResult with recognition text
        """
        import numpy as np
        
        model = self._get_model()
        
        # Get PCM data from artifacts or decode
        if artifacts and artifacts.pcm_bytes:
            pcm_bytes = artifacts.pcm_bytes
        elif audio_format == "opus":
            pcm_frames = self.decode_opus(audio_data)
            pcm_bytes = b"".join(pcm_frames)
        else:
            pcm_bytes = b"".join(audio_data)
        
        if len(pcm_bytes) == 0:
            return ASRResult(text="", is_final=True)
        
        # Convert PCM bytes to numpy float32 array
        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Transcribe
        language = self.language if self.language != "auto" else None
        
        transcribe_kwargs = {
            "beam_size": self.beam_size,
            "language": language,
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": 300,
            },
        }
        
        if self.initial_prompt:
            transcribe_kwargs["initial_prompt"] = self.initial_prompt
        
        segments, info = model.transcribe(audio_array, **transcribe_kwargs)
        
        # Collect all segment texts
        texts = []
        for segment in segments:
            texts.append(segment.text.strip())
        
        result_text = "".join(texts)
        
        logger.info(
            f"Whisper ASR result: '{result_text}' "
            f"(language={info.language}, prob={info.language_probability:.2f})"
        )
        
        return ASRResult(
            text=result_text,
            is_final=True,
            confidence=info.language_probability,
            language=info.language,
        )
