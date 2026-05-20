from __future__ import annotations
import queue
import sys
import threading

import requests
import prompt_loader
import server
import reformatter_log as rlog
from config import CFG
from document import Document
from steps.base import Step, PipelineContext


class ContinuationStep(Step):
    def __init__(self, doc: Document) -> None:
        self._doc = doc
        self._system = prompt_loader.load("continuation_system")
        self._prompt = prompt_loader.load("continuation")
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="continuation").start()

    def run(self, ctx: PipelineContext) -> None:
        for last, first, rest in ctx.data.get("continuation_candidates", []):
            self._queue.put((last, first, rest))

    def _worker(self) -> None:
        while True:
            last, first, rest = self._queue.get()
            try:
                b_context = " ".join([first] + rest)
                a_stripped = last.rstrip(".?!")
                first_cap = first[0].upper() + first[1:] if first else first
                v1 = f"{a_stripped} ... {b_context}"
                b_context_v2 = " ".join([first_cap] + rest)
                v2 = f"{last}  {b_context_v2}"
                prompt = (self._prompt
                          .replace("{a_stripped}", a_stripped)
                          .replace("{a}", last)
                          .replace("{b_context}", b_context)
                          .replace("{b_context_v2}", b_context_v2))
                payload = {
                    "model": CFG.ollama_model,
                    "messages": [
                        {"role": "system", "content": self._system},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                }
                resp = requests.post(CFG.ollama_url, json=payload, timeout=30)
                resp.raise_for_status()
                verdict = resp.json().get("message", {}).get("content", "").strip()
                v = verdict[0] if verdict and verdict[0] in "12" else "2"

                merged = False
                if v == "1":
                    merged = self._doc.merge_by_content(last, first)
                    if merged:
                        server.push_document(self._doc.state())

                rlog.log_continuation_result(last, first, v, merged, v1, v2)
            except Exception as e:
                print(f"[continuation] error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                self._queue.task_done()
