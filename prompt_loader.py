"""Shared prompt-loading utility. Prompt files live in prompts/<name>.md (public)
or local/prompts/<name>.md (your private overrides). Local wins if both exist.
Files are re-read on every call so they're hot-editable without a restart."""
from pathlib import Path

_ROOT = Path(__file__).parent
PROMPTS_DIR = _ROOT / "prompts"
LOCAL_PROMPTS_DIR = _ROOT / "local" / "prompts"


def path(name: str) -> Path:
    local = LOCAL_PROMPTS_DIR / f"{name}.md"
    if local.exists():
        return local
    return PROMPTS_DIR / f"{name}.md"


def exists(name: str) -> bool:
    return path(name).exists()


def load(name: str) -> str:
    """Required prompt — raises FileNotFoundError if missing."""
    return path(name).read_text().strip()


def load_optional(name: str) -> str:
    """Optional prompt — empty string if missing or unreadable."""
    p = path(name)
    if not p.exists():
        return ""
    try:
        return p.read_text().strip()
    except Exception:
        return ""
