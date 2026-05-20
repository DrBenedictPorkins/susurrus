import importlib.util
import json
import queue
import signal
import sys
import threading
from collections import deque
from pathlib import Path


def _load_dotenv() -> None:
    """Load env vars from local/.env or .env at project root (in that order).
    Must run BEFORE any module that instantiates an SDK client reading env at
    construction time (e.g. anthropic). Existing shell env vars always win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).parent
    for candidate in (root / "local" / ".env", root / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            print(f"[env] loaded {candidate.relative_to(root)}", flush=True)
            return


_load_dotenv()

import display
import server
from audio_reader import start_audio_reader
from config import CFG
from steps.base import PipelineContext
from steps.qa import QnAManager
from vad_engine import vad_segments
from whisper_worker import load_model, transcribe_utterance

_pipeline_queue: queue.Queue = queue.Queue()


def _scan_profiles(d: Path) -> list[str]:
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.py") if not p.name.startswith("_"))


def _list_profiles() -> None:
    root = Path(__file__).parent
    local_names = _scan_profiles(root / "local" / "profiles")
    public_names = _scan_profiles(root / "profiles")
    print("Available profiles:", file=sys.stderr)
    if local_names:
        print(f"  local (override public): {', '.join(local_names)}", file=sys.stderr)
    if public_names:
        print(f"  public:                  {', '.join(public_names)}", file=sys.stderr)
    if not local_names and not public_names:
        print("  (none found)", file=sys.stderr)


def _load_profile(name: str):
    root = Path(__file__).parent
    local = root / "local" / "profiles" / f"{name}.py"
    public = root / "profiles" / f"{name}.py"
    path = local if local.exists() else public
    if not path.exists():
        print(f"[profile] not found: {name}", file=sys.stderr)
        _list_profiles()
        sys.exit(1)
    spec = importlib.util.spec_from_file_location(f"profiles.{name}", path)
    if spec is None or spec.loader is None:
        print(f"[profile] failed to load spec: {path}", file=sys.stderr)
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    origin = "local" if path == local else "public"
    print(f"[profile] loaded: {name} ({origin})", flush=True)
    return mod


def _pipeline_worker(steps: list, context_words: int) -> None:
    history: deque[str] = deque()
    history_word_count = 0
    while True:
        utterance = _pipeline_queue.get()
        if utterance is None:
            return
        words = utterance.split()
        history.append(utterance)
        history_word_count += len(words)
        while history_word_count > context_words and len(history) > 1:
            oldest = history.popleft()
            history_word_count -= len(oldest.split())
        ctx = PipelineContext(utterance=utterance, text=utterance, history=history)
        for step in steps:
            step.run(ctx)
        _pipeline_queue.task_done()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: transcribe.py <profile-name>", file=sys.stderr)
        _list_profiles()
        sys.exit(1)
    profile_name = sys.argv[1]
    profile = _load_profile(profile_name)
    context_words = getattr(profile, "context_words", 500)
    steps = profile.build_steps()

    model = load_model()
    server.start_server()
    display.server_ready(CFG.web_port)

    # session restore
    _get_advisor = getattr(profile, 'get_advisor', None)
    _get_doc = getattr(profile, 'get_doc', None)
    session_advisor = _get_advisor() if _get_advisor else None
    session_doc = _get_doc() if _get_doc else None
    session_file = session_advisor._session_file if session_advisor else None

    # publish profile info for the UI (profile name + prompt file paths)
    _advisor_prompt_name = getattr(session_advisor, "_prompt_name", None) if session_advisor else None
    _backstory_name = getattr(session_advisor, "_backstory_name", None) if session_advisor else None
    _advisor_prompt_path = getattr(session_advisor, "prompt_path", None) if session_advisor else None
    _backstory_path = getattr(session_advisor, "backstory_path", None) if session_advisor else None
    server.set_profile_info(profile_name, _advisor_prompt_path, _backstory_path)

    # unified Q&A manager — wired for all profiles
    qa_manager = QnAManager(
        doc=session_doc,
        advisor_prompt_name=_advisor_prompt_name,
        backstory_name=_backstory_name,
    )
    if session_advisor:
        qa_manager.set_save_cb(session_advisor.save_session)
    server.register_qa_ask_cb(qa_manager.ask)
    server.register_qa_followup_cb(qa_manager.followup)

    # term-lookup wiring (profile-provided step, persisted via the advisor's save)
    _get_term_lookup = getattr(profile, 'get_term_lookup', None)
    term_lookup = _get_term_lookup() if _get_term_lookup else None
    if term_lookup:
        if session_advisor:
            term_lookup.set_save_cb(session_advisor.save_session)
        server.register_term_toggle_cb(term_lookup.set_enabled)

    if session_file and session_file.exists() and session_advisor and session_doc:
        try:
            data = json.loads(session_file.read_text())
            paragraphs = data.get("paragraphs", [])
            if paragraphs:
                session_doc.restore_paragraphs(paragraphs)
            session_advisor.preload(data)
            qa_manager.restore_threads(server.get_session_extras().get("qa_threads", []))
            if term_lookup:
                term_lookup.restore(data.get("terms", []))
                term_lookup.set_enabled(bool(data.get("term_lookup_enabled", False)))
                server.set_term_lookup_enabled(bool(data.get("term_lookup_enabled", False)))
            if paragraphs:
                server.push_document(session_doc.state())
                display.loading(f"Restored {len(paragraphs)} paragraphs from previous session.")
        except Exception as e:
            print(f"[session] restore failed: {e}", file=sys.stderr)

    def _on_clear_live_notes():
        # _stored_suggestions already wiped by server WS handler — just persist
        if session_advisor:
            session_advisor.save_session()

    def _on_clear_qa():
        qa_manager.clear()
        if session_advisor:
            session_advisor.save_session()

    server.register_clear_live_notes_cb(_on_clear_live_notes)
    server.register_clear_qa_cb(_on_clear_qa)

    if session_advisor and hasattr(session_advisor, "trigger_now"):
        server.register_trigger_advisor_cb(session_advisor.trigger_now)

    def _on_clear_session():
        if session_doc:
            session_doc.clear()
        if session_advisor:
            session_advisor.clear_history()
        if term_lookup:
            term_lookup.clear()
        qa_manager.clear()
        server.push_cleared()

    server.register_clear_cb(_on_clear_session)

    threading.Thread(
        target=_pipeline_worker,
        args=(steps, context_words),
        daemon=True,
        name="pipeline-worker",
    ).start()

    proc = None

    def _shutdown(_signum, _frame):
        if proc is not None:
            proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        display.loading("Waiting for broadcaster...")
        proc, chunk_queue = start_audio_reader()
        display.ready(CFG.icecast_port)
        for utterance in vad_segments(chunk_queue):
            duration = len(utterance) / CFG.sample_rate
            display.transcribing(duration)
            segments = transcribe_utterance(model, utterance)
            if segments:
                text = " ".join(segments)
                display.print_transcript(text)
                _pipeline_queue.put(text)
                server.push_raw(text)
        proc.terminate()
        display.loading("Broadcaster disconnected.")


if __name__ == "__main__":
    main()
