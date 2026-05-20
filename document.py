import threading


class Document:
    def __init__(self):
        self._sentences: list[str] = []
        self._lock = threading.Lock()

    def append(self, sentence: str) -> None:
        with self._lock:
            self._sentences.append(sentence)

    def update_last(self, text: str) -> None:
        """Update the last non-empty sentence in place."""
        with self._lock:
            for i in range(len(self._sentences) - 1, -1, -1):
                if self._sentences[i] != "":
                    self._sentences[i] = text
                    return

    def last_sentence(self) -> str:
        with self._lock:
            for s in reversed(self._sentences):
                if s != "":
                    return s
            return ""

    def current_para(self) -> list[str]:
        """Sentences in the current (last) paragraph — everything after the last ''."""
        with self._lock:
            boundary = self._last_boundary_locked()
            return list(self._sentences[boundary + 1:])

    def _last_boundary_locked(self) -> int:
        """Index of the last '' in _sentences, or -1 if none."""
        for i in range(len(self._sentences) - 1, -1, -1):
            if self._sentences[i] == "":
                return i
        return -1

    def split_current(self, indices: set[int]) -> None:
        """Insert '' boundaries at given sentence indices within the current paragraph."""
        with self._lock:
            boundary = self._last_boundary_locked()
            current = list(self._sentences[boundary + 1:])
            prefix = list(self._sentences[:boundary + 1])
            new_tail: list[str] = []
            for i, s in enumerate(current):
                if i in indices:
                    new_tail.append("")
                new_tail.append(s)
            self._sentences = prefix + new_tail

    def _window_parts_locked(self, n: int):
        """Returns (prefix, window_raw, current) for last n finalized paragraphs."""
        last_boundary = -1
        for i in range(len(self._sentences) - 1, -1, -1):
            if self._sentences[i] == "":
                last_boundary = i
                break
        if last_boundary == -1:
            return [], [], list(self._sentences)
        finalized = list(self._sentences[:last_boundary])
        current = list(self._sentences[last_boundary + 1:])
        boundaries = [i for i, s in enumerate(finalized) if s == ""]
        if len(boundaries) >= n:
            cut = boundaries[-n]
            prefix = finalized[:cut + 1]
            window_raw = finalized[cut + 1:]
        else:
            prefix = []
            window_raw = finalized
        return prefix, window_raw, current

    def window_sentences(self, n: int) -> list[str]:
        """Flat sentences from last n finalized paragraphs (no '' entries)."""
        with self._lock:
            _, window_raw, _ = self._window_parts_locked(n)
            return [s for s in window_raw if s != ""]

    def window_sentences_and_splits(self, n: int) -> tuple[list[str], set[int]]:
        """Flat sentences and current split indices for last n finalized paragraphs."""
        with self._lock:
            _, window_raw, _ = self._window_parts_locked(n)
            sentences: list[str] = []
            splits: set[int] = set()
            for s in window_raw:
                if s == "":
                    if sentences:
                        splits.add(len(sentences))
                else:
                    sentences.append(s)
            return sentences, splits

    def rewrite_window(self, n: int, sentences: list[str], split_indices: set[int]) -> bool:
        """Rewrite '' boundaries for last n finalized paragraphs. Stale-safe. Returns True if changed."""
        with self._lock:
            if not sentences:
                return False
            prefix, window_raw, current = self._window_parts_locked(n)
            flat_window = [s for s in window_raw if s != ""]
            if flat_window != sentences:
                return False  # stale
            new_window: list[str] = []
            for i, s in enumerate(sentences):
                if i in split_indices:
                    new_window.append("")
                new_window.append(s)
            if new_window == window_raw:
                return False  # no change
            new_sentences = list(prefix)
            if prefix and prefix[-1] != "":
                new_sentences.append("")
            new_sentences.extend(new_window)
            new_sentences.append("")
            new_sentences.extend(current)
            before = sum(1 for s in self._sentences if s != "")
            after = sum(1 for s in new_sentences if s != "")
            if before != after:
                import sys
                print(f"[document] BUG: sentence count changed {before} → {after} in rewrite_window", file=sys.stderr, flush=True)
                return False
            self._sentences = new_sentences
            return True

    def state(self) -> dict:
        """Return {paragraphs: [str, ...], current: str} for the server."""
        with self._lock:
            groups: list[list[str]] = []
            current_group: list[str] = []
            for s in self._sentences:
                if s == "":
                    if current_group:
                        groups.append(current_group)
                    current_group = []
                else:
                    current_group.append(s)
            para_texts = [" ".join(g) for g in groups]
            current_text = " ".join(current_group)
            return {"paragraphs": para_texts, "current": current_text}

    def merge_by_content(self, last_text: str, first_text: str) -> bool:
        """Find last_text followed by first_text within 2 positions and merge. Stale-safe."""
        with self._lock:
            for i in range(len(self._sentences)):
                if self._sentences[i] != last_text:
                    continue
                for j in range(i + 1, min(i + 3, len(self._sentences))):
                    if self._sentences[j] == first_text:
                        self._sentences[i] = last_text.rstrip() + " " + first_text
                        del self._sentences[i + 1:j + 1]
                        return True
            return False

    def is_empty(self) -> bool:
        with self._lock:
            return all(s == "" for s in self._sentences) or not self._sentences

    def restore_paragraphs(self, paragraphs: list[str]) -> None:
        with self._lock:
            self._sentences = []
            for i, p in enumerate(paragraphs):
                if i > 0:
                    self._sentences.append("")
                self._sentences.append(p)
            self._sentences.append("")

    def clear(self) -> None:
        with self._lock:
            self._sentences = []
