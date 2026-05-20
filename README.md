# susurrus

A quiet shoulder-buddy for your live calls. Susurrus listens to whatever audio is on your Mac (Zoom, meetings, podcasts), transcribes it in real time with Whisper, and runs LLM-powered commentary, term lookups, and ad-hoc Q&A — all streamed into a single browser UI.

The name is Latin for *whisper / soft murmur* — what a good advisor sounds like.

Designed to run the heavy work on a GPU server while the capture side runs on a laptop, but everything can run on one machine too.

## Pipeline

```
audio capture  →  Whisper (faster-whisper)  →  paragraph reformatter
                                              →  LLM advisor (running commentary)
                                              →  term lookup (definitions + web search)
                                              →  user Q&A (ASK button)
                                              →  browser UI (WebSocket)
```

## Requirements

- Python 3.11+, `uv` for dependency management
- `ffmpeg`
- An Anthropic API key for advisor / term-lookup / Q&A (set `ANTHROPIC_API_KEY`)
- An Ollama instance for the continuation step (`qwen2.5:14b` by default)
- For CUDA-accelerated Whisper: an NVIDIA GPU; otherwise faster-whisper runs on CPU

## Quick start

```bash
git clone <repo>
cd susurrus
uv sync

# point this shell at your Ollama (if remote) and set ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY=sk-ant-...

uv run python transcribe.py demo-meeting
```

Open `http://localhost:8765` in a browser. Configure audio capture (see below), and you should start seeing transcripts.

## Convenience: your own `run.sh`

There's no shipped `run.sh` because the right environment setup depends entirely on your machine — Whisper backend (CUDA / Metal / CPU), where your API keys live, whether Ollama is local or remote, etc. Most users will want a small wrapper script so they don't have to remember the env-var dance every time.

Save it as `local/run.sh` (the `local/` directory is gitignored) and `chmod +x` it.

Here's a starter prompt you can hand to an LLM to help draft yours:

> Help me write a `local/run.sh` for the **susurrus** project. It should `exec uv run python transcribe.py "$@"` so I can call it as `./local/run.sh <profile-name>`. My setup:
>
> - Platform: **[macOS Apple Silicon / macOS Intel / Linux x86_64 / etc.]**
> - Whisper backend: **[CPU / CUDA / Metal (MPS)]** — if CUDA, I'm using **[CUDA 12 / 11]** and I may need `LD_LIBRARY_PATH` set to my Ollama CUDA libs (e.g. `/usr/local/lib/ollama/cuda_v12`)
> - `ANTHROPIC_API_KEY` lives in **[~/.secrets/api_keys.sh / a .env file / I'll paste it directly into the script (don't commit) / 1Password CLI]**
> - Ollama: **[running locally on :11434 / running on a separate host at http://...:11434]**. If remote, I'll need to set the corresponding URL in `config.py` or via an env-var override.
> - Other: anything special like activating a conda env, sourcing direnv, etc.
>
> Important: don't echo secrets, use `set -euo pipefail`, and `cd` into the script's own directory so it works no matter where it's called from.

Adjust `config.py` for the Whisper bits — `whisper_device`, `whisper_compute_type`, `whisper_model` — to match what your hardware can actually run. `large-v3` on CPU is unusably slow; try `small.en` or `distil-large-v3` on Apple Silicon CPU.

## Audio capture

Two modes, configured via `CFG.capture_mode` in `config.py`:

### `"icecast"` (default) — Audio Hijack
Point Audio Hijack's "Broadcast" output at `localhost:8000`, format Icecast MP3. Then run `./run.sh <profile>`.

### `"tcp"` — open-source, BlackHole + ffmpeg
```bash
brew install blackhole-2ch
# in Audio MIDI Setup, create a Multi-Output Device combining your speakers + BlackHole
# point your Mac and Zoom's speaker output at the Multi-Output Device
./capture.sh <host> <port>     # default: localhost 8000
```

See `capture.sh` and `CLAUDE.md` for tuning latency.

## Creating your own profile

A "profile" is what the project listens for. The shipped `demo-meeting` profile gives a neutral co-pilot suitable for any meeting. To make your own — say for a **standup** or a **D&D session** or an **investment call** — copy the demo and tweak.

A short walkthrough is below. For the full step-by-step (with LLM-drafting prompts for each file), see **[PROFILES.md](./PROFILES.md)**.

### 1. Make a local directory for your stuff

`local/` is `.gitignore`d. The project's loader checks `local/profiles/` and `local/prompts/` *before* the public versions, so your personal profile will override the demo if you reuse names.

```bash
mkdir -p local/profiles local/prompts
```

### 2. Copy the demo as a starting point

```bash
cp profiles/demo-meeting.py            local/profiles/standup.py
cp prompts/demo_meeting_advisor.md     local/prompts/standup_advisor.md
cp prompts/demo_meeting_backstory.md   local/prompts/standup_backstory.md
cp prompts/demo_meeting_terms.md       local/prompts/standup_terms.md
```

### 3. Update the profile file

Open `local/profiles/standup.py` and rename the prompt references and session file:

```python
_advisor = AdvisorStep(
    prompt_name="standup_advisor",            # ← your advisor prompt
    backstory_name="standup_backstory",       # ← your backstory
    doc=_doc,
    session_file="session_standup.json",
    log_tag="standup",
)
...
_term_lookup = TermLookupStep(
    extractor_prompt_name="standup_terms",    # ← your term focus
    backstory_name="standup_backstory",
    doc=_doc,
)
```

### 4. Edit the prompts

| File | What to write |
|---|---|
| `local/prompts/standup_advisor.md` | The advisor's persona, output format, and rules. Defines tone, what to surface, what to ignore. |
| `local/prompts/standup_backstory.md` | Free-form context about your meeting — participants, goals, terminology specific to your team. **Editable live from the UI** (click the profile name in the TRANSCRIPT header). |
| `local/prompts/standup_terms.md` | What counts as an "interesting term" for your domain. Tell it which acronyms or jargon to flag and which to ignore. |

Prompts are re-read on every LLM call, so you can iterate live without restarting.

### 5. Launch

```bash
./run.sh standup
```

The startup log will show `[profile] loaded: standup (local)`.

## What the profiles do

| Profile | LLMs in use | Use case |
|---|---|---|
| `default` | none | Just paragraph-segmented transcription, no analysis |
| `banking` | Ollama | Per-paragraph commentary via local LLM only |
| `podcast` | Ollama | Continuation merging + per-paragraph commentary |
| `demo-meeting` | Ollama + Anthropic | **Full pipeline** — advisor, term lookup, Q&A, web search |

`demo-meeting` is the one to copy.

## Browser UI

Three resizable panels:

| Panel | Content |
|---|---|
| TRANSCRIPT | Raw Whisper output, plus the live formatted document with click-to-define terms |
| LIVE NOTES | Running advisor commentary; the **INSTRUCT** button lets you adjust tone live |
| Q&A | User-initiated questions (ASK button) with selection-as-quote and inline citations |

A prompts modal (click the profile name in TRANSCRIPT) shows the active advisor + backstory and lets you edit the backstory in place.

## Architecture

See `CLAUDE.md` for the full breakdown of files, step classes, LLM call sites, and pipeline data flow.

## Deployment

The project is designed for a split between a capture-side Mac and a server-side machine with GPU. There's no shipped deploy script — write a `local/deploy.sh` that rsyncs the project to your server (excluding `.venv`, `__pycache__`, `*.log`, `session_*.json`).

On the server: `uv sync`, then `./run.sh <profile>`.
