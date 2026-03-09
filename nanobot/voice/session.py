"""
Voice session management for nanobot-service.

Manages voice interaction sessions per user, including:
- Audio buffering and state management
- Session lifecycle (create, update, destroy)
- Redis-based session persistence for multi-instance deployment
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Voice session states."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    CLOSED = "closed"


class ListenMode(Enum):
    """Listen mode for VAD control."""
    AUTO = "auto"        # VAD-based automatic detection
    MANUAL = "manual"    # Manual push-to-talk


@dataclass
class VoiceSession:
    """
    Represents a voice interaction session for a single user.
    
    Manages audio buffering, session state, and coordination between
    ASR/TTS providers and the nanobot agent loop.
    """
    user_id: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: SessionState = SessionState.IDLE
    listen_mode: ListenMode = ListenMode.AUTO
    
    # Audio buffering
    asr_audio_buffer: List[bytes] = field(default_factory=list)
    sample_rate: int = 16000
    audio_format: str = "opus"  # opus or pcm
    
    # Voice detection state
    has_voice: bool = False
    voice_stopped: bool = False
    
    # TTS queue for outgoing audio
    tts_text_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    tts_audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    
    # Control flags
    abort_flag: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    
    # Timing
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    
    # Agent context (for sharing with nanobot)
    agent_context: Dict[str, Any] = field(default_factory=dict)
    
    def reset_audio_states(self):
        """Reset audio buffering states after processing."""
        self.asr_audio_buffer = []
        self.has_voice = False
        self.voice_stopped = False
        self.last_activity = time.time()
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()
    
    def set_state(self, new_state: SessionState):
        """Update session state with logging."""
        old_state = self.state
        self.state = new_state
        logger.debug(f"Session {self.session_id} state: {old_state.value} -> {new_state.value}")
    
    def abort(self):
        """Signal abort for current operation (e.g., user interruption)."""
        self.abort_flag = True
        logger.info(f"Session {self.session_id} aborted")
    
    def reset_abort(self):
        """Reset abort flag for new operation."""
        self.abort_flag = False
    
    async def close(self):
        """Clean up session resources."""
        self.set_state(SessionState.CLOSED)
        self.stop_event.set()
        
        # Clear queues
        while not self.tts_text_queue.empty():
            try:
                self.tts_text_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        while not self.tts_audio_queue.empty():
            try:
                self.tts_audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        logger.info(f"Session {self.session_id} closed for user {self.user_id}")


class VoiceSessionManager:
    """
    Manages multiple voice sessions across users.
    
    Provides session lifecycle management with optional Redis backing
    for distributed deployment scenarios.
    """
    
    def __init__(self, redis_client=None, session_timeout: int = 3600):
        """
        Initialize session manager.
        
        Args:
            redis_client: Optional Redis client for distributed session storage
            session_timeout: Session timeout in seconds (default: 1 hour)
        """
        self._sessions: Dict[str, VoiceSession] = {}
        self._redis = redis_client
        self._session_timeout = session_timeout
        self._lock = asyncio.Lock()
    
    async def create_session(self, user_id: str, **kwargs) -> VoiceSession:
        """
        Create a new voice session for a user.
        Each connection gets an independent session keyed by session_id,
        so multiple clients (browsers/tabs) for the same user coexist without conflict.
        
        Args:
            user_id: User identifier
            **kwargs: Additional session configuration
            
        Returns:
            VoiceSession instance
        """
        async with self._lock:
            # Create new session — no longer evict existing sessions for the same user
            session = VoiceSession(user_id=user_id, **kwargs)
            self._sessions[session.session_id] = session
            
            # Persist to Redis if available
            if self._redis:
                await self._persist_session(session)
            
            logger.info(f"Created new voice session {session.session_id} for user {user_id}")
            return session
    
    async def get_session(self, session_id: str) -> Optional[VoiceSession]:
        """
        Get existing session by session_id.
        
        Args:
            session_id: Session identifier
            
        Returns:
            VoiceSession if exists and not expired, None otherwise
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            
            if session:
                # Check if session is expired
                if time.time() - session.last_activity > self._session_timeout:
                    await session.close()
                    del self._sessions[session_id]
                    logger.info(f"Session {session_id} expired for user {session.user_id}")
                    return None
                
                return session
            
            # Try to recover from Redis
            if self._redis:
                session = await self._recover_session(session_id)
                if session:
                    self._sessions[session_id] = session
                    return session
            
            return None
    
    async def get_sessions_for_user(self, user_id: str) -> list:
        """Get all active sessions for a given user (multi-device support)."""
        async with self._lock:
            return [s for s in self._sessions.values() if s.user_id == user_id]
    
    async def close_session(self, session_id: str):
        """
        Close and remove a session by session_id.
        
        Args:
            session_id: Session identifier
        """
        async with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                await session.close()
                del self._sessions[session_id]
                
                # Remove from Redis
                if self._redis:
                    await self._remove_session(session_id)
    
    async def cleanup_expired_sessions(self):
        """Clean up all expired sessions."""
        async with self._lock:
            expired_ids = []
            current_time = time.time()
            
            for session_id, session in self._sessions.items():
                if current_time - session.last_activity > self._session_timeout:
                    expired_ids.append(session_id)
            
            for session_id in expired_ids:
                session = self._sessions[session_id]
                await session.close()
                del self._sessions[session_id]
                if self._redis:
                    await self._remove_session(session_id)
                logger.info(f"Cleaned up expired session {session_id} for user {session.user_id}")
    
    @property
    def active_session_count(self) -> int:
        """Get count of active sessions."""
        return len(self._sessions)
    
    async def _persist_session(self, session: VoiceSession):
        """Persist session metadata to Redis."""
        if not self._redis:
            return
        
        try:
            key = f"voice:session:{session.session_id}"
            data = {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "state": session.state.value,
                "listen_mode": session.listen_mode.value,
                "sample_rate": session.sample_rate,
                "audio_format": session.audio_format,
                "created_at": session.created_at,
                "last_activity": session.last_activity,
            }
            await self._redis.hset(key, mapping=data)
            await self._redis.expire(key, self._session_timeout)
        except Exception as e:
            logger.error(f"Failed to persist session to Redis: {e}")
    
    async def _recover_session(self, session_id: str) -> Optional[VoiceSession]:
        """Recover session from Redis."""
        if not self._redis:
            return None
        
        try:
            key = f"voice:session:{session_id}"
            data = await self._redis.hgetall(key)
            
            if not data:
                return None
            
            session = VoiceSession(
                user_id=data.get("user_id", "unknown"),
                session_id=data.get("session_id", session_id),
                state=SessionState(data.get("state", "idle")),
                listen_mode=ListenMode(data.get("listen_mode", "auto")),
                sample_rate=int(data.get("sample_rate", 16000)),
                audio_format=data.get("audio_format", "opus"),
                created_at=float(data.get("created_at", time.time())),
                last_activity=float(data.get("last_activity", time.time())),
            )
            
            logger.info(f"Recovered session {session.session_id} for user {session.user_id} from Redis")
            return session
        except Exception as e:
            logger.error(f"Failed to recover session from Redis: {e}")
            return None
    
    async def _remove_session(self, session_id: str):
        """Remove session from Redis."""
        if not self._redis:
            return
        
        try:
            key = f"voice:session:{session_id}"
            await self._redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to remove session from Redis: {e}")
