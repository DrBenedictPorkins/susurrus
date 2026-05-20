"""Term lookup — async pipeline that detects interesting terms in finalized
paragraphs and looks each one up with Claude (+ web_search when factual).

Two background workers:
  - extractor: per paragraph, ask Claude for a list of NEW interesting terms
    given the running set already known.
  - lookup: per term, ask Claude (with web_search tool) for a concise definition
    + sources.

Pushes via WS:
  - term_lookup_start: {term} — about to look up
  - term_lookup_done:  {term, definition, sources} — completed
  - term_lookup_skipped: {term} — extracted but rejected (already known, etc.)

Toggleable via set_enabled(). Persisted via the advisor's save_session callback.
"""
from __future__ import annotations
import json
import queue
import re
import sys
import threading
import time

import anthropic
import prompt_loader
import server
from config import CFG
from document import Document
from steps.base import Step, PipelineContext


WEB_SEARCH_TOOL = {"name": "web_search", "type": "web_search_20250305"}


class TermLookupStep(Step):
    def __init__(
        self,
        extractor_prompt_name: str,
        backstory_name: str | None = None,
        doc: Document | None = None,
        save_cb=None,
        enabled: bool = False,
    ) -> None:
        prompt_loader.load(extractor_prompt_name)  # fail loudly if missing
        self._extractor_prompt_name = extractor_prompt_name
        self._backstory_name = backstory_name
        self._enabled = enabled
        self._client = anthropic.Anthropic()
        self._doc = doc
        self._save_cb = save_cb
        self._terms: dict[str, dict] = {}   # lowercased term key -> entry
        self._lock = threading.Lock()
        self._extract_q: queue.Queue = queue.Queue()
        self._lookup_q: queue.Queue = queue.Queue()
        threading.Thread(target=self._extractor_worker, daemon=True, name="term_extractor").start()
        threading.Thread(target=self._lookup_worker, daemon=True, name="term_lookup").start()

    # ── pipeline hook ─────────────────────────────────────────────────────────
    def run(self, ctx: PipelineContext) -> None:
        if not self._enabled:
            return
        for _idx, text in ctx.data.get("finalized_paragraphs", []):
            self._extract_q.put(text)

    # ── public API ────────────────────────────────────────────────────────────
    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        print(f"[term_lookup] enabled={self._enabled}", file=sys.stderr, flush=True)

    def set_save_cb(self, cb) -> None:
        self._save_cb = cb

    def restore(self, terms: list[dict]) -> None:
        with self._lock:
            self._terms = {}
            for t in terms:
                key = (t.get("term") or "").strip().lower()
                if key:
                    self._terms[key] = t
        server.update_terms(list(self._terms.values()))

    def clear(self) -> None:
        with self._lock:
            self._terms = {}
        server.update_terms([])

    # ── workers ───────────────────────────────────────────────────────────────
    def _extractor_worker(self) -> None:
        while True:
            paragraph = self._extract_q.get()
            try:
                self._extract_from(paragraph)
            except Exception as e:
                print(f"[term_lookup] extract error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                self._extract_q.task_done()

    def _lookup_worker(self) -> None:
        while True:
            term, surface = self._lookup_q.get()
            try:
                self._lookup_term(term, surface)
            except Exception as e:
                print(f"[term_lookup] lookup error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                self._lookup_q.task_done()

    # ── extraction ────────────────────────────────────────────────────────────
    def _known_terms_block(self) -> str:
        with self._lock:
            terms = [t.get("term", "") for t in self._terms.values() if t.get("term")]
        if not terms:
            return "(none yet)"
        return ", ".join(sorted(terms))

    def _backstory_text(self) -> str:
        if not self._backstory_name:
            return ""
        return prompt_loader.load_optional(self._backstory_name)

    def _build_extractor_system(self) -> str:
        focus = prompt_loader.load(self._extractor_prompt_name)
        return f"{focus}\n\n{prompt_loader.load('term_extractor_format')}"

    def _extract_from(self, paragraph: str) -> None:
        if not paragraph.strip():
            return
        user_blocks = []
        bs = self._backstory_text()
        if bs:
            user_blocks.append({
                "type": "text",
                "text": f"[Backstory — for context]\n{bs}",
                "cache_control": {"type": "ephemeral"},
            })
        user_blocks.append({
            "type": "text",
            "text": (
                f"Already-known terms (do NOT extract these again):\n{self._known_terms_block()}\n\n"
                f"New paragraph:\n{paragraph}"
            ),
        })
        try:
            resp = self._client.messages.create(
                model=CFG.anthropic_model,
                max_tokens=512,
                system=[{"type": "text", "text": self._build_extractor_system(),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_blocks}],
            )
        except Exception as e:
            print(f"[term_lookup] extractor API error: {e}", file=sys.stderr)
            return
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        text = text.strip()
        # tolerate code-fenced JSON
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        try:
            items = json.loads(text or "[]")
        except Exception:
            print(f"[term_lookup] extractor: invalid JSON: {text[:200]!r}", file=sys.stderr)
            return
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            term = (item.get("term") or "").strip()
            surface = (item.get("surface") or term).strip()
            if not term:
                continue
            key = term.lower()
            with self._lock:
                if key in self._terms:
                    continue
                # tentatively reserve the slot so duplicate extractions don't queue twice
                self._terms[key] = {
                    "term": term, "surface": surface, "definition": "",
                    "sources": [], "ts": "", "pending": True,
                }
            self._lookup_q.put((term, surface))

    # ── lookup ────────────────────────────────────────────────────────────────
    def _lookup_term(self, term: str, surface: str) -> None:
        server.push_term_lookup_start(term)
        user_text = (
            f"Term to define: {term}\n"
            f"(Surface form as heard: \"{surface}\")\n\n"
            "Give a concise 1-3 sentence definition. Use the web_search tool only if needed for current/factual info."
        )
        definition = ""
        sources: list[dict] = []
        try:
            resp = self._client.messages.create(
                model=CFG.anthropic_model,
                max_tokens=512,
                system=[{"type": "text", "text": prompt_loader.load("term_lookup"),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_text}],
                tools=[WEB_SEARCH_TOOL],
            )
            seen_urls: set[str] = set()
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    definition += block.text
                    for cit in (getattr(block, "citations", None) or []):
                        url = getattr(cit, "url", None)
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            sources.append({"title": getattr(cit, "title", None) or url, "url": url})
            definition = definition.strip()
        except Exception as e:
            print(f"[term_lookup] lookup API error for {term!r}: {e}", file=sys.stderr)
            definition = f"[error: {e}]"

        ts = time.strftime("%I:%M %p").lstrip("0")
        entry = {
            "term": term, "surface": surface,
            "definition": definition, "sources": sources, "ts": ts,
        }
        with self._lock:
            self._terms[term.lower()] = entry
        server.push_term_lookup_done(entry)
        self._snapshot_and_save()

    def _snapshot_and_save(self) -> None:
        with self._lock:
            snapshot = [dict(v) for v in self._terms.values() if not v.get("pending")]
        server.update_terms(snapshot)
        if self._save_cb:
            try:
                self._save_cb()
            except Exception as e:
                print(f"[term_lookup] save_cb error: {e}", file=sys.stderr)
