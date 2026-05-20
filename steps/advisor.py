"""Generic LLM advisor — listens to finalized paragraphs and produces
running commentary every N paragraphs. Profile-agnostic; behavior comes
entirely from the prompt files."""
from __future__ import annotations
import json
import queue
import re
import sys
import threading
from pathlib import Path

import anthropic
import prompt_loader
import server
from config import CFG
from document import Document
from steps.base import Step, PipelineContext


class AdvisorStep(Step):
    """Sliding-window advisor. Fires on every new finalized paragraph once at
    least `min_to_fire` paragraphs exist. The window grows up to `window_max`
    then slides forward — each call sees the last `window_max` finalized
    paragraphs plus the in-progress paragraph (appended in _call_claude_auto)."""

    def __init__(
        self,
        prompt_name: str,
        backstory_name: str | None = None,
        doc: Document | None = None,
        window_max: int = 5,
        min_to_fire: int = 3,
        session_file: str | None = None,
        log_tag: str = "advisor",
    ) -> None:
        prompt_loader.load(prompt_name)  # fail loudly if missing
        self._prompt_name = prompt_name
        self._backstory_name = backstory_name
        self._log_tag = log_tag
        self._client = anthropic.Anthropic()
        self._doc = doc
        self._history: list[str] = []
        self._extra_instruction: str = ""
        self._window_max = window_max
        self._min_to_fire = min_to_fire
        self._queue: queue.Queue = queue.Queue()
        self._session_file = Path(session_file) if session_file else None
        self._sent_fresh: set[int] = set()  # paragraph indices in the most recent AUTO/NUDGE
        self._sent_stale: set[int] = set()  # paragraph indices in any earlier AUTO/NUDGE
        threading.Thread(target=self._worker, daemon=True, name=log_tag).start()

    @property
    def prompt_path(self) -> Path:
        return prompt_loader.path(self._prompt_name)

    @property
    def backstory_path(self) -> Path | None:
        return prompt_loader.path(self._backstory_name) if self._backstory_name else None

    def preload(self, data: dict) -> None:
        paragraphs = data.get("paragraphs", [])
        self._history = list(paragraphs)
        self._extra_instruction = data.get("instruction", "")
        self._sent_fresh = set(data.get("sent_fresh", []))
        self._sent_stale = set(data.get("sent_stale", []))
        server.restore_session_extras(data)
        server.push_sent_state(sorted(self._sent_fresh), sorted(self._sent_stale))

    def clear_history(self) -> None:
        self._history = []
        self._sent_fresh = set()
        self._sent_stale = set()
        if self._session_file:
            self._session_file.unlink(missing_ok=True)

    def save_session(self) -> None:
        self._save_session()

    def _save_session(self) -> None:
        if not self._session_file:
            return
        try:
            data = {
                "paragraphs": self._history,
                "instruction": self._extra_instruction,
                "sent_fresh": sorted(self._sent_fresh),
                "sent_stale": sorted(self._sent_stale),
            }
            data.update(server.get_session_extras())
            self._session_file.write_text(json.dumps(data))
        except Exception as e:
            print(f"[{self._log_tag}] session save error: {e}", file=sys.stderr)

    def trigger_now(self) -> None:
        """Manually fire the advisor with the current sliding window (last
        window_max finalized paragraphs) plus the in-progress paragraph
        (appended in _call_claude_auto). Works even when len(history) is below
        min_to_fire — manual is always allowed."""
        in_progress = ""
        if self._doc:
            in_progress = self._doc.state().get("current", "").strip()
        if not self._history and not in_progress:
            print(f"[{self._log_tag}] trigger_now: nothing to suggest yet", file=sys.stderr, flush=True)
            return
        window_size = min(self._window_max, len(self._history))
        batch = list(self._history[-window_size:])
        indices = list(range(len(self._history) - window_size, len(self._history))) if window_size > 0 else []
        print(f"[{self._log_tag}] trigger_now: window={window_size} indices={indices}", file=sys.stderr, flush=True)
        self._queue.put((batch, indices))

    def set_instruction(self, text: str) -> None:
        self._extra_instruction = text.strip()
        server.store_instruction(self._extra_instruction)
        state = "set" if self._extra_instruction else "cleared"
        print(f"[{self._log_tag}] instruction {state}: {self._extra_instruction[:80]!r}", file=sys.stderr, flush=True)

    def _build_system_prompt(self) -> str:
        text = prompt_loader.load(self._prompt_name)
        if self._extra_instruction:
            text += f"\n\nActive instruction from user: {self._extra_instruction}"
        return text

    def _backstory_block(self) -> list[dict]:
        if not self._backstory_name:
            return []
        text = prompt_loader.load_optional(self._backstory_name)
        if not text:
            return []
        return [{
            "type": "text",
            "text": f"[Backstory]\n{text}",
            "cache_control": {"type": "ephemeral"},
        }]

    def _history_block(self, limit: int | None = None) -> list[dict]:
        parts = list(self._history)
        if self._doc:
            current = self._doc.state().get("current", "").strip()
            if current:
                parts.append(current)
        if not parts:
            return []
        if limit is not None:
            full = " ".join(parts)
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', full) if s.strip()]
            text = " ".join(sentences[-limit:])
        else:
            text = "\n\n".join(parts)
        return [{
            "type": "text",
            "text": f"[Transcript]\n{text}",
            "cache_control": {"type": "ephemeral"},
        }]

    def run(self, ctx: PipelineContext) -> None:
        added = 0
        for _para_idx, text in ctx.data.get("finalized_paragraphs", []):
            self._history.append(text)
            added += 1
        if added:
            self._save_session()
            if len(self._history) >= self._min_to_fire:
                # Fire ONCE per pipeline cycle with the latest sliding window,
                # regardless of how many paragraphs were added in this cycle.
                window_size = min(self._window_max, len(self._history))
                batch = self._history[-window_size:]
                indices = list(range(len(self._history) - window_size, len(self._history)))
                self._queue.put((batch, indices))

    def _worker(self) -> None:
        while True:
            batch, indices = self._queue.get()
            try:
                self._call_claude_auto(batch, indices)
            except Exception as e:
                print(f"[{self._log_tag}] error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                self._queue.task_done()

    def _mark_sent(self, indices: list[int]) -> None:
        """Demote previously-fresh paragraphs to stale, mark this call's
        finalized indices as the new fresh set, push to UI."""
        self._sent_stale |= self._sent_fresh
        self._sent_fresh = set(indices)
        # avoid double-listing: an index can't be both
        self._sent_stale -= self._sent_fresh
        server.push_sent_state(sorted(self._sent_fresh), sorted(self._sent_stale))

    def _call_claude_auto(self, batch: list[str], indices: list[int]) -> None:
        # Always append in-progress paragraph to [Current] so the advisor sees
        # the freshest content it can react to, not just stale finalized paras.
        parts = list(batch)
        if self._doc:
            in_progress = self._doc.state().get("current", "").strip()
            if in_progress:
                parts.append(in_progress)
        if not parts:
            return
        current_text = "\n\n".join(parts)
        user_content = self._backstory_block() + self._history_block()
        user_content.append({"type": "text", "text": f"[Current]\n{current_text}"})

        # Mark fresh BEFORE the API call so the UI updates immediately.
        if indices:
            self._mark_sent(indices)
            self._save_session()

        print(f"[{self._log_tag}] AUTO({len(parts)} parts, {len(batch)} finalized): {current_text[:120]!r}", file=sys.stderr, flush=True)

        resp = self._client.messages.create(
            model=CFG.anthropic_model,
            max_tokens=500,
            system=[{
                "type": "text",
                "text": self._build_system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_content}],
        )
        if not resp.content:
            return
        result = resp.content[0].text.strip()
        print(f"[{self._log_tag}] RESULT: {result[:120]!r}", file=sys.stderr, flush=True)
        if result:
            server.push_suggestion(result)
