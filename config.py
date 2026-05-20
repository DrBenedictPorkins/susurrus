import os
import sys
from dataclasses import dataclass, fields


def _parse(raw: str, target_type: type):
    if target_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return target_type(raw)


@dataclass
class Config:
    """Tunables for the whole pipeline.

    Every field can be overridden at runtime by setting an env var named
    `SUSURRUS_<FIELD_UPPER>`. For example:
        SUSURRUS_OLLAMA_URL=http://my-host:11434/api/chat
        SUSURRUS_WHISPER_DEVICE=cpu
        SUSURRUS_WEB_PORT=9000

    Env vars are loaded from local/.env or .env by transcribe.py before
    the Config is instantiated. Existing shell env vars always win."""
    icecast_port: int = 8000
    capture_mode: str = "icecast"          # "icecast" = MP3 from AudioHijack; "tcp" = raw PCM from capture.sh
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str = "en"
    whisper_beam_size: int = 5
    vad_threshold: float = 0.5
    vad_silence_duration_ms: int = 600
    vad_min_speech_ms: int = 300
    vad_lookback_ms: int = 500
    vad_max_utterance_s: float = 30.0
    vad_soft_split_s: float = 10.0          # after this, split at next breath
    vad_soft_split_threshold: float = 0.7   # "breath" = prob drops below this
    sample_rate: int = 16000
    vad_chunk_samples: int = 512
    ollama_url: str = "http://localhost:11434/api/chat"
    ollama_model: str = "qwen2.5:14b"
    ollama_no_think: bool = False
    anthropic_model: str = "claude-sonnet-4-6"
    web_port: int = 8765
    # Paragraph break tuning (steps/reformatter.py)
    para_break_base: int = 6                # finalize a paragraph after this many counted sentences…
    para_break_jitter: int = 3              # …plus a uniform-random 0..jitter, so breaks don't feel mechanical
    para_break_min_words: int = 5           # sentences shorter than this don't count toward the break

    def __post_init__(self) -> None:
        for f in fields(self):
            env_key = f"SUSURRUS_{f.name.upper()}"
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            current = getattr(self, f.name)
            try:
                parsed = _parse(raw, type(current))
                setattr(self, f.name, parsed)
                print(f"[config] override: {f.name}={parsed!r} (from {env_key})", file=sys.stderr)
            except (ValueError, TypeError) as e:
                print(f"[config] could not parse {env_key}={raw!r}: {e}", file=sys.stderr)


CFG = Config()
