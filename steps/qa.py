"""Unified Q&A manager — replaces the old separate ASK + RESEARCH paths.

A single user-initiated Q&A flow:
- Always includes backstory file + full live transcript as cached context blocks.
- Includes the advisor's profile prompt (without INSTRUCT) so the model retains its persona.
- Always exposes the web_search tool; Claude decides if/when to call it.
- Selection (highlighted text) is sent in <highlighted_by_user> XML tags for emphasis.
- Inline citation markers `[N]` are spliced into the answer string at the boundary
  of each cited text block; sources list is built once and indexed 1-based.
- Threads support follow-ups (multi-turn conversation per topic).
"""
from __future__ import annotations
import sys
import threading
import time
import uuid
from pathlib import Path

import anthropic
import prompt_loader
import server
from config import CFG
from document import Document


WEB_SEARCH_TOOL = {"name": "web_search", "type": "web_search_20250305"}


QA_SYSTEM_ADDENDUM = (
    "---\n"
    "This is a direct Q&A from the user — not an automatic comment. "
    "Ignore any structured output format described above (PARTY/HAROLD/OOC, SUMMARY/QUESTION/OOC, etc.). "
    "Answer the user's question directly in plain prose, in the persona the prompt establishes.\n\n"
    "LENGTH AND STYLE — IMPORTANT: be succinct without losing substance.\n"
    "- Lead with the answer. No preamble like \"Here's what I found:\" or \"Great question!\".\n"
    "- Don't restate the user's question back at them.\n"
    "- One idea per paragraph. When comparing multiple items, use a list or table instead of prose.\n"
    "- Cut hedging filler: \"it's worth noting\", \"keep in mind\", \"generally speaking\", \"essentially\", \"in summary\", \"to wrap up\".\n"
    "- Skip closing summaries unless the answer was genuinely long (>5 paragraphs).\n"
    "- KEEP the interesting bits: numbers, dates, named entities, specific terms, contradictions, red flags, surprising connections, anything that would be lost if paraphrased generically. These are load-bearing — never trim them for brevity.\n"
    "- Aim for the shortest answer that still carries every load-bearing detail. One sentence is fine if it's enough. Three paragraphs is fine if the details warrant it. Never pad to look thorough.\n\n"
    "SPEAKER ATTRIBUTION — IMPORTANT: NEVER assume who said what. Whisper does not separate speakers; "
    "the transcript is one continuous stream. Default to neutral phrasing. Getting a name right looks "
    "polished; getting it wrong looks broken — so when in doubt, do NOT use a name.\n"
    "  WRONG: \"Josh is pitching...\" / \"Joshua is asking what legal entity...\" / \"Jeff confirmed...\"\n"
    "  RIGHT: \"The pitch describes...\" / \"A question came up about what legal entity...\" / \"It was confirmed that...\"\n"
    "The ONLY exception: a name may be used when the transcript itself contains an explicit attribution "
    "like \"Josh: ...\" or \"Jeff said the contract was signed\". Inferring identity from context, role, "
    "or topic is NOT enough.\n\n"
    "You have access to a web_search tool. Use it whenever the user's question requires current, "
    "external, or factual information that is not present in the transcript or backstory. "
    "Do NOT use it for questions that can be answered from the transcript or backstory alone.\n\n"
    "If the user message contains a <highlighted_by_user>...</highlighted_by_user> block, "
    "focus your answer on that specific text. Use the surrounding transcript and backstory for context.\n\n"
    "FORMATTING: You may use full Markdown — lists, tables, bold/italic, code blocks, blockquotes. "
    "For diagrams, you may use Mermaid syntax in a fenced code block tagged `mermaid` "
    "(e.g. flowcharts for decision trees / cap tables, pie charts for ownership splits, timelines "
    "for funding rounds, sequence diagrams for deal flow). Use diagrams only when they genuinely "
    "clarify — not for decoration. Example:\n"
    "```mermaid\n"
    "pie title Cap table\n"
    "  \"AMD\" : 15\n"
    "  \"Magnetar\" : 30\n"
    "  \"Others\" : 55\n"
    "```"
)


class QnAManager:
    def __init__(
        self,
        doc: Document | None = None,
        advisor_prompt_name: str | None = None,
        backstory_name: str | None = None,
        save_cb=None,
    ) -> None:
        self._client = anthropic.Anthropic()
        self._doc = doc
        self._advisor_prompt_name = advisor_prompt_name
        self._backstory_name = backstory_name
        self._save_cb = save_cb
        self._threads: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def advisor_prompt_path(self) -> Path | None:
        return prompt_loader.path(self._advisor_prompt_name) if self._advisor_prompt_name else None

    @property
    def backstory_path(self) -> Path | None:
        return prompt_loader.path(self._backstory_name) if self._backstory_name else None

    def set_save_cb(self, cb) -> None:
        self._save_cb = cb

    # ── context builders ──────────────────────────────────────────────────────
    def _build_system_prompt(self) -> str:
        parts: list[str] = []
        if self._advisor_prompt_name:
            try:
                parts.append(prompt_loader.load(self._advisor_prompt_name))
            except Exception as e:
                print(f"[qa] advisor prompt read error: {e}", file=sys.stderr)
        parts.append(QA_SYSTEM_ADDENDUM)
        return "\n\n".join(parts)

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

    def _transcript_block(self) -> list[dict]:
        if not self._doc:
            return []
        state = self._doc.state()
        paras = state.get("paragraphs", [])
        current = state.get("current", "").strip()
        text = "\n\n".join(paras)
        if current:
            text = text + "\n\n" + current if text else current
        if not text:
            return []
        return [{
            "type": "text",
            "text": f"[Transcript — full live call so far]\n{text}",
            "cache_control": {"type": "ephemeral"},
        }]

    @staticmethod
    def _format_user_prompt(prompt: str, selection: str | None) -> str:
        if selection and selection.strip():
            return (
                f"<highlighted_by_user>\n{selection.strip()}\n</highlighted_by_user>\n\n"
                f"{prompt.strip()}"
            )
        return prompt.strip()

    # ── response extraction with inline citations ─────────────────────────────
    @staticmethod
    def _extract(response) -> tuple[str, list[dict]]:
        """Build the final answer string with inline `[N]` markers + dedup sources list."""
        sources: list[dict] = []
        url_to_idx: dict[str, int] = {}
        out_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) != "text":
                continue
            text = block.text or ""
            out_parts.append(text)
            citations = getattr(block, "citations", None) or []
            block_indices: list[int] = []
            for cit in citations:
                url = getattr(cit, "url", None)
                if not url:
                    continue
                if url not in url_to_idx:
                    title = getattr(cit, "title", None) or url
                    sources.append({"title": title, "url": url})
                    url_to_idx[url] = len(sources)  # 1-based
                idx = url_to_idx[url]
                if idx not in block_indices:
                    block_indices.append(idx)
            for idx in block_indices:
                out_parts.append(f"[{idx}]")
        return "".join(out_parts).strip(), sources

    @staticmethod
    def _serialize_content(content) -> list[dict]:
        out = []
        for block in content:
            try:
                out.append(block.model_dump(mode="json"))
            except Exception:
                out.append({"type": getattr(block, "type", "text"), "text": str(block)})
        return out

    # ── public API ────────────────────────────────────────────────────────────
    def ask(self, prompt: str, selection: str | None = None) -> None:
        threading.Thread(target=self._do_ask, args=(prompt, selection), daemon=True).start()

    def followup(self, thread_id: str, prompt: str, selection: str | None = None) -> None:
        threading.Thread(target=self._do_followup, args=(thread_id, prompt, selection), daemon=True).start()

    # ── workers ───────────────────────────────────────────────────────────────
    def _do_ask(self, prompt: str, selection: str | None) -> None:
        thread_id = uuid.uuid4().hex[:12]
        ts = time.strftime("%I:%M %p").lstrip("0")
        user_content = self._backstory_block() + self._transcript_block()
        user_content.append({"type": "text", "text": self._format_user_prompt(prompt, selection)})
        messages = [{"role": "user", "content": user_content}]
        print(f"[qa] new thread {thread_id}: {prompt[:120]!r} (sel={bool(selection)})", file=sys.stderr, flush=True)
        try:
            resp = self._client.messages.create(
                model=CFG.anthropic_model,
                max_tokens=2048,
                system=[{
                    "type": "text",
                    "text": self._build_system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
                tools=[WEB_SEARCH_TOOL],
            )
            answer, sources = self._extract(resp)
            assistant_content = self._serialize_content(resp.content)
        except Exception as e:
            print(f"[qa] error: {type(e).__name__}: {e}", file=sys.stderr)
            answer = f"[error: {e}]"
            sources = []
            assistant_content = []
        turn = {"q": prompt, "a": answer, "sources": sources, "ts": ts}
        with self._lock:
            self._threads[thread_id] = {
                "id": thread_id,
                "ts": ts,
                "turns": [turn],
                "messages": [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ],
            }
        server.push_qa_thread(thread_id, turn, new_thread=True, start_ts=ts)
        self._snapshot_and_save()

    def _do_followup(self, thread_id: str, prompt: str, selection: str | None) -> None:
        with self._lock:
            thread = self._threads.get(thread_id)
            if not thread:
                print(f"[qa] followup: unknown thread {thread_id}", file=sys.stderr)
                return
            messages = list(thread["messages"])
        user_content = [{"type": "text", "text": self._format_user_prompt(prompt, selection)}]
        messages.append({"role": "user", "content": user_content})
        print(f"[qa] followup {thread_id}: {prompt[:120]!r} (sel={bool(selection)})", file=sys.stderr, flush=True)
        try:
            resp = self._client.messages.create(
                model=CFG.anthropic_model,
                max_tokens=2048,
                system=[{
                    "type": "text",
                    "text": self._build_system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
                tools=[WEB_SEARCH_TOOL],
            )
            answer, sources = self._extract(resp)
            assistant_content = self._serialize_content(resp.content)
        except Exception as e:
            print(f"[qa] followup error: {type(e).__name__}: {e}", file=sys.stderr)
            answer = f"[error: {e}]"
            sources = []
            assistant_content = []
        ts = time.strftime("%I:%M %p").lstrip("0")
        turn = {"q": prompt, "a": answer, "sources": sources, "ts": ts}
        with self._lock:
            t = self._threads.get(thread_id)
            if not t:
                return
            t["turns"].append(turn)
            t["messages"].append({"role": "user", "content": user_content})
            t["messages"].append({"role": "assistant", "content": assistant_content})
        server.push_qa_thread(thread_id, turn, new_thread=False, start_ts=None)
        self._snapshot_and_save()

    def _snapshot_and_save(self) -> None:
        with self._lock:
            snapshot = [dict(t) for t in self._threads.values()]
        server.update_qa_threads(snapshot)
        if self._save_cb:
            try:
                self._save_cb()
            except Exception as e:
                print(f"[qa] save_cb error: {e}", file=sys.stderr)

    def restore_threads(self, threads: list[dict]) -> None:
        with self._lock:
            self._threads = {}
            for t in threads:
                tid = t.get("id")
                if tid:
                    self._threads[tid] = t
        server.update_qa_threads(list(self._threads.values()))

    def clear(self) -> None:
        with self._lock:
            self._threads = {}
        server.update_qa_threads([])
