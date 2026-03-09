"""
Silero VAD (Voice Activity Detection) implementation.

Uses the Silero VAD model for detecting speech presence in audio streams.
This is useful for:
- Automatic speech detection (auto mode)
- Endpoint detection (when user stops speaking)
- Noise filtering

Model: https://github.com/snakers4/silero-vad
"""

import os
import logging
from typing import Optional, List, Tuple
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class SileroVAD:
    """
    Silero VAD for voice activity detection.
    
    Uses ONNX runtime for efficient inference on CPU.
    """
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold: float = 0.5,
        sample_rate: int = 16000,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 300,
    ):
        """
        Initialize Silero VAD.
        
        Args:
            model_path: Path to silero_vad.onnx model file
            threshold: Voice detection threshold (0-1)
            sample_rate: Audio sample rate (8000 or 16000)
            min_speech_duration_ms: Minimum speech duration to consider as speech
            min_silence_duration_ms: Minimum silence duration to consider as silence
        """
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        
        # Model state
        self._model = None
        self._h = None
        self._c = None
        self._model_path = model_path
        
        # Detection state
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False
        
        # Frame configuration
        # Silero VAD works with 30ms, 60ms, or 100ms frames
        self._frame_duration_ms = 60
        self._frame_samples = int(sample_rate * self._frame_duration_ms / 1000)
        
        # Audio buffer for incomplete frames
        self._audio_buffer = np.array([], dtype=np.float32)
    
    @property
    def model(self):
        """Lazy-load ONNX model."""
        if self._model is None:
            self._load_model()
        return self._model
    
    def _load_model(self):
        """Load Silero VAD ONNX model."""
        try:
            import onnxruntime as ort
        except ImportError:
            logger.error("onnxruntime not installed. Run: pip install onnxruntime")
            return
        
        model_path = self._model_path
        
        # Try default paths if not specified
        if not model_path:
            default_paths = [
                "models/silero-vad/silero_vad.onnx",
                "../models/silero-vad/silero_vad.onnx",
                os.path.expanduser("~/.cache/silero-vad/silero_vad.onnx"),
            ]
            for path in default_paths:
                if os.path.exists(path):
                    model_path = path
                    break
        
        if not model_path or not os.path.exists(model_path):
            logger.error(f"Silero VAD model not found. Please download from: "
                        "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx")
            return
        
        try:
            # Use CPU provider
            sess_options = ort.SessionOptions()
            sess_options.inter_op_num_threads = 1
            sess_options.intra_op_num_threads = 1
            
            self._model = ort.InferenceSession(
                model_path,
                sess_options=sess_options,
                providers=['CPUExecutionProvider'],
            )
            
            # Initialize hidden states
            self._reset_states()
            
            logger.info(f"Silero VAD model loaded from: {model_path}")
        except Exception as e:
            logger.error(f"Failed to load VAD model: {e}")
    
    def _reset_states(self):
        """Reset model hidden states."""
        # Silero VAD uses LSTM, initialize hidden states
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
    
    def reset(self):
        """Reset VAD state for new session."""
        self._reset_states()
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False
        self._audio_buffer = np.array([], dtype=np.float32)
    
    def process_audio(self, audio_data: bytes) -> Tuple[bool, float]:
        """
        Process audio chunk and detect voice activity.
        
        Args:
            audio_data: Raw PCM audio data (16-bit signed, little-endian)
            
        Returns:
            Tuple of (has_voice, confidence)
        """
        if not self.model:
            return False, 0.0
        
        # Convert bytes to float32 numpy array
        audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        
        # Add to buffer
        self._audio_buffer = np.concatenate([self._audio_buffer, audio_float])
        
        # Process complete frames
        has_voice = False
        max_confidence = 0.0
        
        while len(self._audio_buffer) >= self._frame_samples:
            frame = self._audio_buffer[:self._frame_samples]
            self._audio_buffer = self._audio_buffer[self._frame_samples:]
            
            confidence = self._process_frame(frame)
            max_confidence = max(max_confidence, confidence)
            
            if confidence >= self.threshold:
                has_voice = True
        
        return has_voice, max_confidence
    
    def _process_frame(self, frame: np.ndarray) -> float:
        """
        Process single audio frame.
        
        Args:
            frame: Audio frame as float32 array
            
        Returns:
            Voice probability (0-1)
        """
        if not self.model:
            return 0.0
        
        try:
            # Prepare input
            input_data = frame.reshape(1, -1)
            sr = np.array([self.sample_rate], dtype=np.int64)
            
            # Run inference
            ort_inputs = {
                'input': input_data,
                'sr': sr,
                'h': self._h,
                'c': self._c,
            }
            
            output, self._h, self._c = self.model.run(None, ort_inputs)
            confidence = float(output[0][0])
            
            return confidence
            
        except Exception as e:
            logger.warning(f"VAD inference error: {e}")
            return 0.0
    
    def detect_speech_end(self, audio_data: bytes) -> Tuple[bool, bool, float]:
        """
        Detect if speech has ended (for endpoint detection).
        
        Args:
            audio_data: Raw PCM audio data
            
        Returns:
            Tuple of (has_voice, speech_ended, confidence)
        """
        has_voice, confidence = self.process_audio(audio_data)
        
        # Calculate frame duration for state tracking
        samples_per_frame = self._frame_samples
        frame_duration_ms = self._frame_duration_ms
        
        if has_voice:
            self._speech_frames += 1
            self._silence_frames = 0
            
            # Mark as speaking after minimum speech duration
            if not self._is_speaking:
                speech_duration_ms = self._speech_frames * frame_duration_ms
                if speech_duration_ms >= self.min_speech_duration_ms:
                    self._is_speaking = True
                    logger.debug(f"Speech started (duration: {speech_duration_ms}ms)")
        else:
            self._silence_frames += 1
            
            # Check for speech end
            if self._is_speaking:
                silence_duration_ms = self._silence_frames * frame_duration_ms
                if silence_duration_ms >= self.min_silence_duration_ms:
                    self._is_speaking = False
                    self._speech_frames = 0
                    logger.debug(f"Speech ended (silence: {silence_duration_ms}ms)")
                    return False, True, confidence  # Speech ended
        
        return has_voice, False, confidence
    
    @property
    def is_speaking(self) -> bool:
        """Whether speech is currently detected."""
        return self._is_speaking
    
    def get_speech_probability(self, audio_data: bytes) -> float:
        """
        Get speech probability for audio chunk.
        
        Args:
            audio_data: Raw PCM audio data
            
        Returns:
            Speech probability (0-1)
        """
        _, confidence = self.process_audio(audio_data)
        return confidence


def download_vad_model(target_dir: str = "models/silero-vad") -> str:
    """
    Download Silero VAD model if not present.
    
    Args:
        target_dir: Directory to save model
        
    Returns:
        Path to downloaded model
    """
    import urllib.request
    
    os.makedirs(target_dir, exist_ok=True)
    model_path = os.path.join(target_dir, "silero_vad.onnx")
    
    if os.path.exists(model_path):
        logger.info(f"VAD model already exists: {model_path}")
        return model_path
    
    url = "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx"
    
    try:
        logger.info(f"Downloading VAD model from: {url}")
        urllib.request.urlretrieve(url, model_path)
        logger.info(f"VAD model downloaded to: {model_path}")
        return model_path
    except Exception as e:
        logger.error(f"Failed to download VAD model: {e}")
        raise
