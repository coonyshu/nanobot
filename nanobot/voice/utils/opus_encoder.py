"""
Opus audio encoder/decoder utilities.

Provides encoding and decoding of Opus audio format for WebSocket transmission.
"""

import logging
from typing import Callable, Optional, List

logger = logging.getLogger(__name__)


class OpusEncoder:
    """
    Opus audio encoder for real-time audio streaming.
    
    Encodes PCM audio to Opus format for efficient WebSocket transmission.
    """
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_size_ms: int = 60,
        bitrate: int = 32000,
    ):
        """
        Initialize Opus encoder.
        
        Args:
            sample_rate: Audio sample rate in Hz (8000, 12000, 16000, 24000, 48000)
            channels: Number of audio channels (1 or 2)
            frame_size_ms: Frame size in milliseconds (2.5, 5, 10, 20, 40, 60)
            bitrate: Target bitrate in bits per second
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size_ms = frame_size_ms
        self.bitrate = bitrate
        
        # Calculate frame size in samples
        self.frame_size = int(sample_rate * frame_size_ms / 1000)
        
        # Lazy initialization
        self._encoder = None
        self._decoder = None
        
        # Buffer for incomplete frames
        self._encode_buffer = b""
    
    @property
    def encoder(self):
        """Lazy-initialize Opus encoder."""
        if self._encoder is None:
            try:
                import opuslib_next
                self._encoder = opuslib_next.Encoder(
                    self.sample_rate, 
                    self.channels, 
                    opuslib_next.APPLICATION_AUDIO
                )
                self._encoder.bitrate = self.bitrate
            except ImportError:
                logger.error("opuslib_next not installed. Run: pip install opuslib-next")
                return None
        return self._encoder
    
    @property
    def decoder(self):
        """Lazy-initialize Opus decoder."""
        if self._decoder is None:
            try:
                import opuslib_next
                self._decoder = opuslib_next.Decoder(self.sample_rate, self.channels)
            except ImportError:
                logger.error("opuslib_next not installed. Run: pip install opuslib-next")
                return None
        return self._decoder
    
    def encode(self, pcm_data: bytes) -> List[bytes]:
        """
        Encode PCM data to Opus frames.
        
        Args:
            pcm_data: Raw PCM audio data (16-bit signed, little-endian)
            
        Returns:
            List of Opus-encoded frames
        """
        if not self.encoder:
            return []
        
        frames = []
        
        # Add to buffer
        self._encode_buffer += pcm_data
        
        # Process complete frames
        bytes_per_frame = self.frame_size * 2 * self.channels  # 16-bit = 2 bytes
        
        while len(self._encode_buffer) >= bytes_per_frame:
            frame_data = self._encode_buffer[:bytes_per_frame]
            self._encode_buffer = self._encode_buffer[bytes_per_frame:]
            
            try:
                opus_frame = self.encoder.encode(frame_data, self.frame_size)
                frames.append(opus_frame)
            except Exception as e:
                logger.warning(f"Opus encode error: {e}")
        
        return frames
    
    def encode_stream(
        self,
        pcm_data: bytes,
        end_of_stream: bool = False,
        callback: Optional[Callable[[bytes], None]] = None,
    ) -> List[bytes]:
        """
        Encode PCM data to Opus with streaming support.
        
        Args:
            pcm_data: Raw PCM audio data
            end_of_stream: Whether this is the last chunk
            callback: Optional callback for each encoded frame
            
        Returns:
            List of Opus-encoded frames
        """
        frames = self.encode(pcm_data)
        
        # Flush remaining buffer at end of stream
        if end_of_stream and self._encode_buffer:
            # Pad with silence
            bytes_per_frame = self.frame_size * 2 * self.channels
            padding_needed = bytes_per_frame - len(self._encode_buffer)
            if padding_needed > 0:
                self._encode_buffer += b"\x00" * padding_needed
            
            try:
                opus_frame = self.encoder.encode(self._encode_buffer, self.frame_size)
                frames.append(opus_frame)
            except Exception as e:
                logger.warning(f"Opus encode error (final): {e}")
            
            self._encode_buffer = b""
        
        # Call callback for each frame
        if callback:
            for frame in frames:
                callback(frame)
        
        return frames
    
    def decode(self, opus_data: bytes) -> bytes:
        """
        Decode Opus frame to PCM data.
        
        Args:
            opus_data: Opus-encoded audio frame
            
        Returns:
            Raw PCM audio data
        """
        if not self.decoder:
            return b""
        
        try:
            pcm_data = self.decoder.decode(opus_data, self.frame_size)
            return pcm_data
        except Exception as e:
            logger.warning(f"Opus decode error: {e}")
            return b""
    
    def decode_frames(self, opus_frames: List[bytes]) -> bytes:
        """
        Decode multiple Opus frames to PCM data.
        
        Args:
            opus_frames: List of Opus-encoded frames
            
        Returns:
            Combined raw PCM audio data
        """
        pcm_chunks = []
        for frame in opus_frames:
            pcm = self.decode(frame)
            if pcm:
                pcm_chunks.append(pcm)
        return b"".join(pcm_chunks)
    
    def reset(self):
        """Reset encoder state and buffer."""
        self._encode_buffer = b""
        if self._encoder:
            # Reset encoder state by recreating
            self._encoder = None
        if self._decoder:
            self._decoder = None
    
    def close(self):
        """Clean up encoder resources."""
        self._encode_buffer = b""
        if self._encoder:
            try:
                del self._encoder
            except Exception:
                pass
            self._encoder = None
        if self._decoder:
            try:
                del self._decoder
            except Exception:
                pass
            self._decoder = None
