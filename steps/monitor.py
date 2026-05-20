from __future__ import annotations
import queue
import sys
import threading

import requests
import prompt_loader
import server
from config import CFG
from steps.base import Step, PipelineContext


class ConversationMonitor(Step):
    def __init__(self, prompt_name: str) -> None:
        self._prompt = prompt_loader.load(prompt_name)
        self._seen: set[int] = set()
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="monitor").start()

    def run(self, ctx: PipelineContext) -> None:
        for para_idx, text in ctx.data.get("finalized_paragraphs", []):
            if para_idx not in self._seen:
                self._seen.add(para_idx)
                self._queue.put((para_idx, text))

    def _worker(self) -> None:
        while True:
            para_idx, text = self._queue.get()
            try:
                payload = {
                    "model": CFG.ollama_model,
                    "messages": [
                        {"role": "system", "content": self._prompt},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                }
                resp = requests.post(CFG.ollama_url, json=payload, timeout=60)
                resp.raise_for_status()
                result = resp.json().get("message", {}).get("content", "").strip()
                if result and result.upper() != "SKIP":
                    server.push_suggestion(result)
            except Exception as e:
                print(f"[monitor] LLM error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                self._queue.task_done()
