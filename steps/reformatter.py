from __future__ import annotations
import random

import reformatter_log as rlog
import server
from config import CFG
from document import Document
from llm_reformatter import segment
from steps.base import Step, PipelineContext


class ReformatterStep(Step):
    def __init__(self) -> None:
        self._doc = Document()
        self._sentence_count = 0
        self._next_break = self._new_threshold()

    @staticmethod
    def _new_threshold() -> int:
        return CFG.para_break_base + random.randint(0, CFG.para_break_jitter)

    @property
    def doc(self) -> Document:
        return self._doc

    def run(self, ctx: PipelineContext) -> None:
        rlog.log_chunk_received(ctx.text)

        chunk = ctx.text
        sentences = segment(chunk)
        rlog.log_segment(self._doc.last_sentence(), chunk, sentences, False)

        if not sentences:
            return

        last = self._doc.last_sentence()
        if last and len(sentences) >= 3:
            rest = sentences[1:2]
            ctx.data.setdefault("continuation_candidates", []).append((last, sentences[0], rest))
            rlog.log_continuation_queued(last, sentences[0], rest)

        for s in sentences:
            self._doc.append(s)
            if len(s.split()) >= CFG.para_break_min_words:
                self._sentence_count += 1
                if self._sentence_count >= self._next_break:
                    state = self._doc.state()
                    para_idx = len(state["paragraphs"])
                    current_text = state["current"]
                    if current_text:
                        ctx.data.setdefault("finalized_paragraphs", []).append(
                            (para_idx, current_text)
                        )
                    self._doc.append("")
                    self._sentence_count = 0
                    self._next_break = self._new_threshold()
                    rlog.log_new_para(self._next_break)

        server.push_document(self._doc.state())
