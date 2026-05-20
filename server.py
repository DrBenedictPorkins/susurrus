import asyncio
import json
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from config import CFG

app = FastAPI()

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_last_document: dict | None = None
_instruction_cb = None
_clear_cb = None
_qa_ask_cb = None
_qa_followup_cb = None
_term_toggle_cb = None
_clear_live_notes_cb = None
_clear_qa_cb = None
_trigger_advisor_cb = None

_stored_suggestions: list[dict] = []
_stored_instruction: str = ""
_stored_qa_threads: list[dict] = []
_stored_terms: list[dict] = []
_stored_sent_fresh: list[int] = []
_stored_sent_stale: list[int] = []
_term_lookup_enabled: bool = False

_profile_name: str = ""
_advisor_prompt_path: Path | None = None
_backstory_path: Path | None = None

_static_dir = Path(__file__).parent / "static"


def _read_prompt_file(p: Path | None) -> dict | None:
    if not p:
        return None
    try:
        if not p.exists():
            return {"path": str(p), "missing": True}
        return {"path": str(p), "text": p.read_text()}
    except Exception as e:
        return {"path": str(p), "error": str(e)}


def _write_backstory(text: str) -> dict:
    p = _backstory_path
    if not p:
        return {"ok": False, "error": "no backstory path configured for this profile"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text or "")
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(p)}


@app.get("/")
async def index():
    html = (_static_dir / "index.html").read_text()
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    if _last_document is not None:
        await ws.send_json({
            "type": "restore",
            "paragraphs": _last_document.get("paragraphs", []),
            "current": _last_document.get("current", ""),
            "suggestions": list(_stored_suggestions),
            "instruction": _stored_instruction,
            "qa_threads": list(_stored_qa_threads),
            "terms": list(_stored_terms),
            "term_lookup_enabled": _term_lookup_enabled,
            "sent_fresh": list(_stored_sent_fresh),
            "sent_stale": list(_stored_sent_stale),
            "profile": _profile_name,
        })
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")
                if msg_type == "qa_ask" and _qa_ask_cb:
                    prompt = msg.get("prompt", "").strip()
                    selection = msg.get("selection", "") or ""
                    if prompt:
                        threading.Thread(target=_qa_ask_cb, args=(prompt, selection), daemon=True).start()
                elif msg_type == "qa_followup" and _qa_followup_cb:
                    thread_id = msg.get("thread_id", "")
                    prompt = msg.get("prompt", "").strip()
                    selection = msg.get("selection", "") or ""
                    if thread_id and prompt:
                        threading.Thread(target=_qa_followup_cb, args=(thread_id, prompt, selection), daemon=True).start()
                elif msg_type == "set_term_lookup_enabled":
                    on = bool(msg.get("enabled"))
                    set_term_lookup_enabled(on)
                    if _term_toggle_cb:
                        _term_toggle_cb(on)
                    await ws.send_json({"type": "term_lookup_enabled", "enabled": on})
                elif msg_type == "set_instruction" and _instruction_cb:
                    _instruction_cb(msg.get("text", ""))
                elif msg_type == "clear_session" and _clear_cb:
                    threading.Thread(target=_clear_cb, daemon=True).start()
                elif msg_type == "clear_live_notes":
                    _stored_suggestions.clear()
                    await _broadcast({"type": "live_notes_cleared"})
                    if _clear_live_notes_cb:
                        threading.Thread(target=_clear_live_notes_cb, daemon=True).start()
                elif msg_type == "clear_qa":
                    if _clear_qa_cb:
                        _clear_qa_cb()
                    await _broadcast({"type": "qa_cleared"})
                elif msg_type == "trigger_advisor":
                    if _trigger_advisor_cb:
                        threading.Thread(target=_trigger_advisor_cb, daemon=True).start()
                        await ws.send_json({"type": "advisor_triggered"})
                elif msg_type == "get_prompts":
                    payload = {
                        "type": "prompts_data",
                        "profile": _profile_name,
                        "advisor": _read_prompt_file(_advisor_prompt_path),
                        "backstory": _read_prompt_file(_backstory_path),
                    }
                    await ws.send_json(payload)
                elif msg_type == "save_backstory":
                    text = msg.get("text", "")
                    result = _write_backstory(text)
                    await ws.send_json({"type": "backstory_saved", **result})
            except Exception as e:
                print(f"[server] ws message error: {type(e).__name__}: {e}", file=sys.stderr)
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


async def _broadcast(msg: dict) -> None:
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


def push_raw(text: str) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast({"type": "raw", "text": text}), _loop)


def push_document(state: dict) -> None:
    global _last_document
    msg = {"type": "document", "paragraphs": state["paragraphs"], "current": state["current"]}
    _last_document = msg
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(msg), _loop)


def push_log(event: dict) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast({"type": "log", **event}), _loop)


def push_summary(text: str) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "summary", "text": text}),
        _loop,
    )


def push_summarized_marker(up_to_idx: int) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "summarized_marker", "up_to_idx": up_to_idx}),
        _loop,
    )


def push_suggestion(text: str, source: str = "auto", prompt: str = "") -> None:
    import time
    ts_ms = int(time.time() * 1000)
    _stored_suggestions.append({
        "text": text, "source": source,
        "ts": time.strftime("%I:%M %p").lstrip("0"),
        "ts_ms": ts_ms,
        "prompt": prompt,
    })
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "suggestion", "text": text, "source": source, "ts_ms": ts_ms}),
        _loop,
    )


def _run_server() -> None:
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    config = uvicorn.Config(app, host="0.0.0.0", port=CFG.web_port, loop="none", log_level="warning")
    server = uvicorn.Server(config)
    _loop.run_until_complete(server.serve())


def register_instruction_cb(cb) -> None:
    global _instruction_cb
    _instruction_cb = cb


def register_clear_cb(cb) -> None:
    global _clear_cb
    _clear_cb = cb


def register_clear_live_notes_cb(cb) -> None:
    global _clear_live_notes_cb
    _clear_live_notes_cb = cb


def register_clear_qa_cb(cb) -> None:
    global _clear_qa_cb
    _clear_qa_cb = cb


def register_trigger_advisor_cb(cb) -> None:
    global _trigger_advisor_cb
    _trigger_advisor_cb = cb


def register_qa_ask_cb(cb) -> None:
    global _qa_ask_cb
    _qa_ask_cb = cb


def register_qa_followup_cb(cb) -> None:
    global _qa_followup_cb
    _qa_followup_cb = cb


def set_profile_info(name: str, advisor_path: Path | None = None, backstory_path: Path | None = None) -> None:
    global _profile_name, _advisor_prompt_path, _backstory_path
    _profile_name = name or ""
    _advisor_prompt_path = advisor_path
    _backstory_path = backstory_path


def push_qa_thread(thread_id: str, turn: dict, new_thread: bool, start_ts: str | None = None) -> None:
    msg = {
        "type": "qa_thread",
        "thread_id": thread_id,
        "turn": turn,
        "new": new_thread,
    }
    if start_ts:
        msg["start_ts"] = start_ts
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(msg), _loop)


def update_qa_threads(threads: list[dict]) -> None:
    global _stored_qa_threads
    _stored_qa_threads = list(threads)


def register_term_toggle_cb(cb) -> None:
    global _term_toggle_cb
    _term_toggle_cb = cb


def push_term_lookup_start(term: str) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "term_lookup_start", "term": term}), _loop)


def push_term_lookup_done(entry: dict) -> None:
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "term_lookup_done", "entry": entry}), _loop)


def update_terms(terms: list[dict]) -> None:
    global _stored_terms
    _stored_terms = list(terms)


def set_term_lookup_enabled(on: bool) -> None:
    global _term_lookup_enabled
    _term_lookup_enabled = bool(on)


def get_session_extras() -> dict:
    return {
        "suggestions": list(_stored_suggestions),
        "instruction": _stored_instruction,
        "qa_threads": list(_stored_qa_threads),
        "terms": list(_stored_terms),
        "term_lookup_enabled": _term_lookup_enabled,
    }


def restore_session_extras(data: dict) -> None:
    global _stored_suggestions, _stored_instruction, _stored_qa_threads, _stored_terms, _term_lookup_enabled
    # filter out legacy ask suggestions — they predate the qa_threads model
    suggestions = data.get("suggestions", [])
    _stored_suggestions = [s for s in suggestions if s.get("source") != "ask"]
    _stored_instruction = data.get("instruction", "")
    _stored_terms = list(data.get("terms", []))
    _term_lookup_enabled = bool(data.get("term_lookup_enabled", False))
    # unified qa_threads, with backwards-compat: merge legacy ask_threads + web_threads
    qa = list(data.get("qa_threads", []))
    seen_ids = {t.get("id") for t in qa if t.get("id")}
    for legacy_key in ("ask_threads", "web_threads"):
        for t in data.get(legacy_key, []):
            tid = t.get("id")
            if tid and tid not in seen_ids:
                qa.append(t)
                seen_ids.add(tid)
    qa.sort(key=lambda t: t.get("ts", ""))
    _stored_qa_threads = qa


def store_instruction(text: str) -> None:
    global _stored_instruction
    _stored_instruction = text


def push_cleared() -> None:
    global _last_document, _stored_suggestions, _stored_instruction, _stored_qa_threads, _stored_terms
    global _stored_sent_fresh, _stored_sent_stale
    _last_document = {"type": "document", "paragraphs": [], "current": ""}
    _stored_suggestions = []
    _stored_instruction = ""
    _stored_qa_threads = []
    _stored_terms = []
    _stored_sent_fresh = []
    _stored_sent_stale = []
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast({"type": "cleared"}), _loop)


def push_sent_state(fresh: list[int], stale: list[int]) -> None:
    global _stored_sent_fresh, _stored_sent_stale
    _stored_sent_fresh = list(fresh)
    _stored_sent_stale = list(stale)
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast({"type": "sent_state", "fresh": list(fresh), "stale": list(stale)}),
        _loop,
    )


def start_server() -> None:
    t = threading.Thread(target=_run_server, daemon=True, name="web-server")
    t.start()
