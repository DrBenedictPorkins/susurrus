from __future__ import annotations
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field


@dataclass
class PipelineContext:
    utterance: str
    text: str               # mutable working copy — steps read and write this
    history: deque[str]
    tags: set[str] = field(default_factory=set)
    data: dict = field(default_factory=dict)


class Step(ABC):
    @abstractmethod
    def run(self, ctx: PipelineContext) -> None: ...
