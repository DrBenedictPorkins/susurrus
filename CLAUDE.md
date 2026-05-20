# susurrus — Claude Code Guide

## What this project does

Real-time audio transcription + LLM analysis pipeline for live calls (Zoom, meetings, podcasts, anything streamed as audio). Audio Hijack or `capture.sh` streams audio to a socket; faster-whisper transcribes; an LLM advisor produces running commentary; a term-lookup pipeline defines specialized terms inline; the user can ASK questions about the live transcript through a web UI.

## Hardware

- **Dev (local):** MacBook M3 Max — CPU only, no CUDA
- **Prod:** Ubuntu server `beefybox` — RTX 4090 24GB, 251GB RAM, runs faster-whisper + Ollama

## Public vs local layout

```
profiles/        ← public showcase profiles (default, banking, podcast, demo-meeting)
prompts/         ← public prompts used by the showcase profiles
local/profiles/  ← your private profiles (.gitignored)
local/prompts/   ← your private prompts (.gitignored)
local/deploy.sh  ← your deploy script (.gitignored)
```

`prompt_loader.py` and `transcribe.py:_load_profile` both check `local/` first, then fall back to public. Local always wins.

**Never deploy without the user saying "go" or "deploy" in that message.** The deploy script lives at `local/deploy.sh`.

## Key files

| File | Role |
|---|---|
| `transcribe.py` | Entry point. Loads the named profile, spawns workers, runs the main VAD+Whisper loop. |
| `config.py` | Single `Config` dataclass with all tunables. `CFG = Config()` is the singleton. |
| `audio_reader.py` | Capture receiver. Two modes via `CFG.capture_mode`: `"icecast"` (AudioHijack MP3) or `"tcp"` (raw PCM from `capture.sh`). |
| `vad_engine.py` | Silero VAD — segments the PCM stream into speech utterances. |
| `whisper_worker.py` | faster-whisper wrapper. |
| `document.py` | Thread-safe document state (`_paragraphs` finalized + `_current` in-progress). |
| `prompt_loader.py` | `load(name)` / `load_optional(name)` / `path(name)`. Re-reads on every call so prompts are hot-editable. Local-first lookup. |
| `server.py` | FastAPI + WebSocket server. Workers push via `push_document`, `push_raw`, `push_suggestion`, `push_qa_thread`, `push_term_lookup_*`. |
| `static/index.html` | Single-file browser UI: TRANSCRIPT, LIVE NOTES, Q&A panels. |
| `capture.sh` | macOS-side capture script (BlackHole → ffmpeg → TCP raw PCM). |
| `run.sh` | Server-side launch script (sets `LD_LIBRARY_PATH`, sources `~/.secrets/api_keys.sh`, execs `transcribe.py`). |

## Pipeline steps (`steps/`)

| Step | LLM | When |
|---|---|---|
| `ReformatterStep` | none | every utterance — segments into sentences, breaks paragraphs at jittered thresholds |
| `ContinuationStep` | Ollama (`qwen2.5:14b`) | async, per utterance with ≥3 sentences — merges Whisper false-splits |
| `ConversationMonitor` | Ollama | per finalized paragraph — used by `banking`/`podcast` profiles |
| `AdvisorStep` | Anthropic (`claude-sonnet-4-6`) | every 3 finalized paragraphs — running commentary into LIVE NOTES |
| `TermLookupStep` | Anthropic + `web_search` | per finalized paragraph (when enabled) — extracts and defines specialized terms |
| `QnAManager` | Anthropic + `web_search` | user-initiated — ASK button + multi-turn followups |

Every Anthropic call uses `cache_control: ephemeral` on the system prompt and backstory block for prompt-cache hits within the 5-minute TTL.

## Data flow

```
capture (Audio Hijack MP3 OR capture.sh raw PCM)
  → audio_reader.py    (socket → PCM queue)
  → vad_engine.py      (Silero VAD → utterance arrays)
  → whisper_worker.py  (faster-whisper → text)
  → pipeline steps (ReformatterStep + ContinuationStep + AdvisorStep + TermLookupStep)
  → server.push_* (WebSocket → static/index.html)
```

## Creating a profile

A profile is `profiles/<name>.py` (or `local/profiles/<name>.py`) exposing `build_steps()`, `get_doc()`, `get_advisor()`, `get_term_lookup()`. Prompts go in `prompts/<name>_*.md` (or `local/prompts/<name>_*.md`). The cleanest starting point is to copy `profiles/demo-meeting.py` and the three `prompts/demo_meeting_*.md` files into `local/`, rename, and tweak.

See `README.md` for the step-by-step.

## Config defaults (`config.py`)

```python
icecast_port      = 8000
capture_mode      = "icecast"   # or "tcp"
whisper_model     = "large-v3"
whisper_device    = "cuda"
ollama_model      = "qwen2.5:14b"
anthropic_model   = "claude-sonnet-4-6"
web_port          = 8765
```

## Dependencies (`pyproject.toml`)

`faster-whisper`, `silero-vad`, `requests`, `numpy`, `rich`, `fastapi`, `uvicorn`, `websockets`, `anthropic`, `nltk`

## Common gotchas

- Ollama must be running before starting the server (`ollama serve`); pull the model with `ollama pull qwen2.5:14b`.
- `ANTHROPIC_API_KEY` must be in the environment — `run.sh` sources `~/.secrets/api_keys.sh`.
- Term lookup is **off by default**; toggle from the FORMATTED panel header.
- `reformatter.log` accumulates indefinitely — truncate manually if it gets large.
- `server.py` replays the last document state to any newly-connecting WebSocket client.
- `session_*.json` files persist per-profile state across restarts; deleting them clears history.
