"""
Voice module configuration.

Provides configuration loading and validation for voice services.
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _load_dotenv():
    """Load .env file if found in current working directory."""
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        logger.info(f"Loading environment from: {env_path}")
        load_dotenv(dotenv_path=env_path, override=False, encoding='utf-8')
    else:
        logger.debug("No .env file found, using system environment variables")


# Load .env on module import
_load_dotenv()


@dataclass
class ASRConfig:
    """ASR (Automatic Speech Recognition) configuration."""
    provider: str = "aliyun_stream"
    access_key_id: str = ""
    access_key_secret: str = ""
    appkey: str = ""
    host: str = "nls-gateway-cn-shanghai.aliyuncs.com"
    max_sentence_silence: int = 1500  # ms (句子静音阈值，越长越不容易切断)
    
    # 热词配置：提升特定词汇的识别准确率
    hot_words: list = None  # 格式: [{"word": "安检", "weight": 10}, ...]
    
    # Whisper 本地 ASR 配置
    whisper_model_size: str = "large-v3"  # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"  # cpu/cuda/auto
    whisper_language: str = "zh"  # zh/en/auto
    whisper_compute_type: str = "float16"  # int8/float16/float32
    whisper_model_path: str = ""  # 自定义模型路径（留空则自动下载）
    whisper_beam_size: int = 5
    whisper_initial_prompt: str = ""  # 可选初始提示词
    
    # FunASR 本地流式 ASR 配置
    funasr_host: str = "localhost"
    funasr_port: int = 10095
    funasr_mode: str = "2pass"  # online/offline/2pass
    funasr_chunk_size: str = "5,10,5"  # encoder,fsmn,decoder
    funasr_itn: bool = True  # 逆文本归一化
    funasr_hotwords: str = ""  # 热词（空格分隔）
    
    def __post_init__(self):
        """Initialize empty hot words if not provided."""
        if self.hot_words is None:
            self.hot_words = []
    
    @classmethod
    def from_env(cls) -> "ASRConfig":
        """Load configuration from environment variables."""
        # 从环境变量加载热词（JSON格式）
        hot_words_json = os.getenv("VOICE_ASR_HOT_WORDS", "").strip()
        logger.info(f"[ASR Config] Raw hot words from env: {repr(hot_words_json[:100] if hot_words_json else 'empty')}...")
        
        # 移除可能的外层单引号（环境变量格式）
        if hot_words_json.startswith("'") and hot_words_json.endswith("'"):
            hot_words_json = hot_words_json[1:-1]
            logger.info(f"[ASR Config] After removing quotes: {repr(hot_words_json[:100])}...")
        
        hot_words = None
        if hot_words_json:  # 只有当非空字符串时才解析
            try:
                import json
                hot_words = json.loads(hot_words_json)
                logger.info(f"[ASR Config] Hot words parsed successfully: {len(hot_words)} words")
            except Exception as e:
                logger.warning(f"Failed to parse hot words from env: {e}")
                logger.debug(f"[ASR Config] Problematic JSON: {repr(hot_words_json)}")
        
        return cls(
            provider=os.getenv("VOICE_ASR_PROVIDER", "aliyun_stream"),
            access_key_id=os.getenv("ALIYUN_ACCESS_KEY_ID", ""),
            access_key_secret=os.getenv("ALIYUN_ACCESS_KEY_SECRET", ""),
            appkey=os.getenv("ALIYUN_NLS_APPKEY", ""),
            host=os.getenv("ALIYUN_NLS_ASR_HOST", "nls-gateway-cn-shanghai.aliyuncs.com"),
            max_sentence_silence=int(os.getenv("VOICE_ASR_SILENCE_MS", "1500")),
            hot_words=hot_words,
            # Whisper 配置
            whisper_model_size=os.getenv("WHISPER_MODEL_SIZE", "large-v3"),
            whisper_device=os.getenv("WHISPER_DEVICE", "auto"),
            whisper_language=os.getenv("WHISPER_LANGUAGE", "zh"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
            whisper_model_path=os.getenv("WHISPER_MODEL_PATH", ""),
            whisper_beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "5")),
            whisper_initial_prompt=os.getenv("WHISPER_INITIAL_PROMPT", ""),
            # FunASR 配置
            funasr_host=os.getenv("FUNASR_HOST", "localhost"),
            funasr_port=int(os.getenv("FUNASR_PORT", "10095")),
            funasr_mode=os.getenv("FUNASR_MODE", "2pass"),
            funasr_chunk_size=os.getenv("FUNASR_CHUNK_SIZE", "5,10,5"),
            funasr_itn=os.getenv("FUNASR_ITN", "true").lower() == "true",
            funasr_hotwords=os.getenv("FUNASR_HOTWORDS", ""),
        )


@dataclass
class TTSConfig:
    """TTS (Text-to-Speech) configuration."""
    provider: str = "aliyun_stream"
    access_key_id: str = ""
    access_key_secret: str = ""
    appkey: str = ""
    host: str = "nls-gateway-cn-beijing.aliyuncs.com"
    voice: str = "xiaoyun"
    volume: int = 50
    speech_rate: int = 0
    pitch_rate: int = 0
    format: str = "pcm"
    sample_rate: int = 16000
    
    # Edge TTS 配置
    edge_voice: str = "zh-CN-XiaoxiaoNeural"
    edge_rate: str = "+0%"
    edge_volume: str = "+0%"
    edge_pitch: str = "+0Hz"
    
    # Piper TTS 配置
    piper_model_path: str = ""  # .onnx 模型文件路径
    piper_config_path: str = ""  # .onnx.json 配置文件路径（留空自动检测）
    piper_speaker_id: int = 0
    piper_length_scale: float = 1.0  # 语速：<1.0 更快，>1.0 更慢
    piper_noise_scale: float = 0.667
    piper_noise_w: float = 0.8
    piper_sentence_silence: float = 0.3  # 句间停顿（秒）
    
    # CosyVoice TTS 配置
    cosyvoice_model_path: str = ""  # 模型目录路径
    cosyvoice_speaker: str = "中文女"  # 预置说话人
    cosyvoice_mode: str = "sft"  # sft/zero_shot/cross_lingual
    cosyvoice_ref_audio_path: str = ""  # 零样本克隆参考音频
    cosyvoice_ref_text: str = ""  # 零样本克隆参考文本
    cosyvoice_sample_rate: int = 22050
    
    @classmethod
    def from_env(cls) -> "TTSConfig":
        """Load configuration from environment variables."""
        return cls(
            provider=os.getenv("VOICE_TTS_PROVIDER", "aliyun_stream"),
            access_key_id=os.getenv("ALIYUN_ACCESS_KEY_ID", ""),
            access_key_secret=os.getenv("ALIYUN_ACCESS_KEY_SECRET", ""),
            appkey=os.getenv("ALIYUN_NLS_APPKEY", ""),
            host=os.getenv("ALIYUN_NLS_TTS_HOST", "nls-gateway-cn-beijing.aliyuncs.com"),
            voice=os.getenv("VOICE_TTS_VOICE", "xiaoyun"),
            volume=int(os.getenv("VOICE_TTS_VOLUME", "50")),
            speech_rate=int(os.getenv("VOICE_TTS_SPEECH_RATE", "0")),
            pitch_rate=int(os.getenv("VOICE_TTS_PITCH_RATE", "0")),
            format=os.getenv("VOICE_TTS_FORMAT", "pcm"),
            sample_rate=int(os.getenv("VOICE_SAMPLE_RATE", "16000")),
            # Edge TTS
            edge_voice=os.getenv("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            edge_rate=os.getenv("EDGE_TTS_RATE", "+0%"),
            edge_volume=os.getenv("EDGE_TTS_VOLUME", "+0%"),
            edge_pitch=os.getenv("EDGE_TTS_PITCH", "+0Hz"),
            # Piper TTS
            piper_model_path=os.getenv("PIPER_MODEL_PATH", ""),
            piper_config_path=os.getenv("PIPER_CONFIG_PATH", ""),
            piper_speaker_id=int(os.getenv("PIPER_SPEAKER_ID", "0")),
            piper_length_scale=float(os.getenv("PIPER_LENGTH_SCALE", "1.0")),
            piper_noise_scale=float(os.getenv("PIPER_NOISE_SCALE", "0.667")),
            piper_noise_w=float(os.getenv("PIPER_NOISE_W", "0.8")),
            piper_sentence_silence=float(os.getenv("PIPER_SENTENCE_SILENCE", "0.3")),
            # CosyVoice TTS
            cosyvoice_model_path=os.getenv("COSYVOICE_MODEL_PATH", ""),
            cosyvoice_speaker=os.getenv("COSYVOICE_SPEAKER", "中文女"),
            cosyvoice_mode=os.getenv("COSYVOICE_MODE", "sft"),
            cosyvoice_ref_audio_path=os.getenv("COSYVOICE_REF_AUDIO_PATH", ""),
            cosyvoice_ref_text=os.getenv("COSYVOICE_REF_TEXT", ""),
            cosyvoice_sample_rate=int(os.getenv("COSYVOICE_SAMPLE_RATE", "22050")),
        )


@dataclass
class VADConfig:
    """VAD (Voice Activity Detection) configuration."""
    enabled: bool = True
    model_path: str = str(Path(__file__).parent.parent / "data" / "models" / "silero-vad" / "silero_vad.onnx")
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 300
    
    @classmethod
    def from_env(cls) -> "VADConfig":
        """Load configuration from environment variables."""
        return cls(
            enabled=os.getenv("VOICE_VAD_ENABLED", "true").lower() == "true",
            model_path=os.getenv("VOICE_VAD_MODEL_PATH", str(Path(__file__).parent.parent / "data" / "models" / "silero-vad" / "silero_vad.onnx")),
            threshold=float(os.getenv("VOICE_VAD_THRESHOLD", "0.5")),
            min_speech_duration_ms=int(os.getenv("VOICE_VAD_MIN_SPEECH_MS", "250")),
            min_silence_duration_ms=int(os.getenv("VOICE_VAD_MIN_SILENCE_MS", "300")),
        )


@dataclass
class VoiceModuleConfig:
    """Complete voice module configuration."""
    enabled: bool = True
    sample_rate: int = 16000
    audio_format: str = "opus"  # opus or pcm
    session_timeout: int = 3600  # seconds
    
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    
    @classmethod
    def from_env(cls) -> "VoiceModuleConfig":
        """Load complete configuration from environment variables."""
        return cls(
            enabled=os.getenv("VOICE_ENABLED", "true").lower() == "true",
            sample_rate=int(os.getenv("VOICE_SAMPLE_RATE", "16000")),
            audio_format=os.getenv("VOICE_AUDIO_FORMAT", "opus"),
            session_timeout=int(os.getenv("VOICE_SESSION_TIMEOUT", "3600")),
            asr=ASRConfig.from_env(),
            tts=TTSConfig.from_env(),
            vad=VADConfig.from_env(),
        )

    @classmethod
    def from_config(cls, voice_dict: dict) -> "VoiceModuleConfig":
        """Build from config.json voice section; uncovered fields fall back to env/defaults."""
        # Start with env-based baseline (covers all fields including non-common ones)
        base = cls.from_env()

        # Override top-level common fields from config.json
        for key in ("enabled", "sample_rate", "audio_format", "session_timeout"):
            if key in voice_dict:
                setattr(base, key, voice_dict[key])

        # Override ASR common fields
        asr = voice_dict.get("asr", {})
        for key in ("provider", "access_key_id", "access_key_secret", "appkey", "host"):
            if asr.get(key):
                setattr(base.asr, key, asr[key])

        # Override TTS common fields
        tts = voice_dict.get("tts", {})
        for key in ("provider", "access_key_id", "access_key_secret", "appkey", "host", "voice"):
            if tts.get(key):
                setattr(base.tts, key, tts[key])

        # Override VAD
        vad = voice_dict.get("vad", {})
        if "enabled" in vad:
            base.vad.enabled = vad["enabled"]

        return base
    
    def validate(self) -> bool:
        """Validate configuration."""
        if not self.enabled:
            return True
        
        errors = []
        
        # Check ASR config based on provider
        if self.asr.provider == "aliyun_stream":
            if not self.asr.access_key_id or not self.asr.access_key_secret:
                errors.append("ASR: ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET required")
            if not self.asr.appkey:
                errors.append("ASR: ALIYUN_NLS_APPKEY required")
        elif self.asr.provider == "whisper":
            logger.info(f"ASR: Using Whisper (model={self.asr.whisper_model_size}, device={self.asr.whisper_device})")
        elif self.asr.provider == "funasr":
            logger.info(f"ASR: Using FunASR (host={self.asr.funasr_host}:{self.asr.funasr_port}, mode={self.asr.funasr_mode})")
        
        # Check TTS config based on provider
        if self.tts.provider == "aliyun_stream":
            if not self.tts.access_key_id or not self.tts.access_key_secret:
                errors.append("TTS: ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET required")
            if not self.tts.appkey:
                errors.append("TTS: ALIYUN_NLS_APPKEY required")
        elif self.tts.provider == "edge":
            logger.info(f"TTS: Using Edge TTS (voice={self.tts.edge_voice})")
        elif self.tts.provider == "piper":
            if not self.tts.piper_model_path:
                errors.append("TTS: PIPER_MODEL_PATH required for Piper TTS")
            logger.info(f"TTS: Using Piper (model={self.tts.piper_model_path})")
        elif self.tts.provider == "cosyvoice":
            if not self.tts.cosyvoice_model_path:
                errors.append("TTS: COSYVOICE_MODEL_PATH required for CosyVoice TTS")
            logger.info(f"TTS: Using CosyVoice (model={self.tts.cosyvoice_model_path})")
        
        # Check VAD config
        if self.vad.enabled and not os.path.exists(self.vad.model_path):
            logger.warning(f"VAD model not found at {self.vad.model_path}, will try to download")
        
        if errors:
            for error in errors:
                logger.error(f"Voice config validation failed: {error}")
            return False
        
        return True


def get_voice_config(config=None) -> VoiceModuleConfig:
    """Get voice module configuration.

    If a nanobot Config object is provided, reads from its ``voice`` section
    (config.json values override env defaults).  Otherwise falls back to
    pure environment-variable loading.
    """
    if config is not None:
        voice_dict = config.voice.model_dump(by_alias=False)
        result = VoiceModuleConfig.from_config(voice_dict)
    else:
        result = VoiceModuleConfig.from_env()
    result.validate()
    return result
