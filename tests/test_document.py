"""Tests for document.py — the core thread-safe document data structure."""

import pytest
from document import Document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sentences(doc: Document) -> list[str]:
    """White-box accessor: returns the raw _sentences list (copy)."""
    return list(doc._sentences)


def _build(sentences: list[str]) -> Document:
    """Build a Document with an exact _sentences list, bypassing the public API."""
    doc = Document()
    doc._sentences = list(sentences)
    return doc


# ---------------------------------------------------------------------------
# append / last_sentence / current_para
# ---------------------------------------------------------------------------

class TestAppend:
    def test_append_single_sentence(self):
        doc = Document()
        doc.append("Hello world.")
        assert doc.current_para() == ["Hello world."]

    def test_append_multiple_sentences(self):
        doc = Document()
        doc.append("First.")
        doc.append("Second.")
        doc.append("Third.")
        assert doc.current_para() == ["First.", "Second.", "Third."]

    def test_append_after_boundary(self):
        """Sentences appended after a '' boundary appear in current_para."""
        doc = Document()
        doc.append("Para one.")
        doc.append("")  # boundary / paragraph separator
        doc.append("Para two.")
        assert doc.current_para() == ["Para two."]

    def test_append_empty_string_is_stored(self):
        doc = Document()
        doc.append("")
        assert _sentences(doc) == [""]


class TestLastSentence:
    def test_empty_document_returns_empty_string(self):
        doc = Document()
        assert doc.last_sentence() == ""

    def test_single_sentence(self):
        doc = Document()
        doc.append("Only sentence.")
        assert doc.last_sentence() == "Only sentence."

    def test_multiple_sentences_returns_last_non_empty(self):
        doc = Document()
        doc.append("First.")
        doc.append("Second.")
        doc.append("Third.")
        assert doc.last_sentence() == "Third."

    def test_trailing_boundary_skipped(self):
        """last_sentence skips trailing '' boundaries."""
        doc = Document()
        doc.append("Real sentence.")
        doc.append("")
        assert doc.last_sentence() == "Real sentence."

    def test_multiple_trailing_boundaries_skipped(self):
        doc = Document()
        doc.append("Real sentence.")
        doc.append("")
        doc.append("")
        assert doc.last_sentence() == "Real sentence."

    def test_only_boundaries_returns_empty(self):
        doc = Document()
        doc.append("")
        doc.append("")
        assert doc.last_sentence() == ""


class TestCurrentPara:
    def test_empty_document(self):
        doc = Document()
        assert doc.current_para() == []

    def test_no_boundary_all_sentences_are_current(self):
        doc = Document()
        doc.append("A.")
        doc.append("B.")
        assert doc.current_para() == ["A.", "B."]

    def test_only_sentences_after_last_boundary(self):
        doc = Document()
        doc.append("Old.")
        doc.append("")
        doc.append("New one.")
        doc.append("New two.")
        assert doc.current_para() == ["New one.", "New two."]

    def test_boundary_at_end_returns_empty(self):
        """A trailing '' means no in-progress sentences."""
        doc = Document()
        doc.append("Finalized.")
        doc.append("")
        assert doc.current_para() == []

    def test_multiple_boundaries_uses_last(self):
        doc = Document()
        doc.append("P1.")
        doc.append("")
        doc.append("P2.")
        doc.append("")
        doc.append("P3.")
        assert doc.current_para() == ["P3."]


# ---------------------------------------------------------------------------
# update_last
# ---------------------------------------------------------------------------

class TestUpdateLast:
    def test_updates_last_non_empty(self):
        doc = Document()
        doc.append("Original.")
        doc.update_last("Updated.")
        assert doc.last_sentence() == "Updated."

    def test_skips_trailing_boundary(self):
        """update_last looks past trailing '' to find the real last sentence."""
        doc = Document()
        doc.append("Real.")
        doc.append("")
        doc.update_last("Changed.")
        assert doc.last_sentence() == "Changed."

    def test_empty_document_does_nothing(self):
        doc = Document()
        doc.update_last("Anything.")
        assert _sentences(doc) == []

    def test_only_boundary_does_nothing(self):
        doc = Document()
        doc.append("")
        doc.update_last("Anything.")
        assert _sentences(doc) == [""]

    def test_multiple_sentences_only_last_updated(self):
        doc = Document()
        doc.append("First.")
        doc.append("Second.")
        doc.update_last("Second updated.")
        assert _sentences(doc) == ["First.", "Second updated."]


# ---------------------------------------------------------------------------
# split_current
# ---------------------------------------------------------------------------

class TestSplitCurrent:
    def test_split_at_index_2_of_5(self):
        """Index 2 means sentences 0-1 go to finalized, 2-4 stay current."""
        doc = Document()
        for s in ["S0.", "S1.", "S2.", "S3.", "S4."]:
            doc.append(s)

        doc.split_current({2})

        raw = _sentences(doc)
        # Expect: S0 S1 "" S2 S3 S4
        assert raw == ["S0.", "S1.", "", "S2.", "S3.", "S4."]
        assert doc.current_para() == ["S2.", "S3.", "S4."]

    def test_split_at_index_3_of_5(self):
        """Index 3: sentences 0-2 finalized, 3-4 current."""
        doc = Document()
        for s in ["S0.", "S1.", "S2.", "S3.", "S4."]:
            doc.append(s)

        doc.split_current({3})

        raw = _sentences(doc)
        assert raw == ["S0.", "S1.", "S2.", "", "S3.", "S4."]
        assert doc.current_para() == ["S3.", "S4."]

    def test_split_at_index_1(self):
        doc = Document()
        for s in ["S0.", "S1.", "S2."]:
            doc.append(s)

        doc.split_current({1})

        assert _sentences(doc) == ["S0.", "", "S1.", "S2."]
        assert doc.current_para() == ["S1.", "S2."]

    def test_multiple_splits(self):
        """Two split points in one call produce two boundaries."""
        doc = Document()
        for s in ["S0.", "S1.", "S2.", "S3.", "S4."]:
            doc.append(s)

        doc.split_current({1, 3})

        raw = _sentences(doc)
        # boundaries before indices 1 and 3 of current
        assert raw == ["S0.", "", "S1.", "S2.", "", "S3.", "S4."]

    def test_existing_boundary_not_touched(self):
        """Sentences before an existing '' boundary are left intact."""
        doc = Document()
        doc.append("Before boundary.")
        doc.append("")
        for s in ["C0.", "C1.", "C2."]:
            doc.append(s)

        doc.split_current({1})

        raw = _sentences(doc)
        assert raw == ["Before boundary.", "", "C0.", "", "C1.", "C2."]
        assert doc.current_para() == ["C1.", "C2."]

    def test_split_at_zero_finalizes_nothing(self):
        """Index 0 inserts '' before the first current sentence; all remain current."""
        doc = Document()
        for s in ["S0.", "S1."]:
            doc.append(s)

        doc.split_current({0})

        raw = _sentences(doc)
        # "" inserted before S0 — no sentences finalized before the boundary
        assert raw == ["", "S0.", "S1."]
        assert doc.current_para() == ["S0.", "S1."]

    def test_empty_indices_set_is_noop(self):
        doc = Document()
        for s in ["S0.", "S1."]:
            doc.append(s)

        doc.split_current(set())

        assert _sentences(doc) == ["S0.", "S1."]


# ---------------------------------------------------------------------------
# window_sentences
# ---------------------------------------------------------------------------

class TestWindowSentences:
    def _build(self, sentences):
        doc = Document()
        doc._sentences = list(sentences)
        return doc

    def test_empty_document(self):
        doc = Document()
        assert doc.window_sentences(3) == []

    def test_no_finalized_only_current(self):
        # No '' in _sentences → no finalized content
        doc = self._build(["A.", "B."])
        assert doc.window_sentences(3) == []

    def test_one_finalized_para_want_three(self):
        # Only 1 finalized para; return what we have
        doc = self._build(["A.", "B.", "", "Cur."])
        assert doc.window_sentences(3) == ["A.", "B."]

    def test_three_finalized_paras_want_three(self):
        doc = self._build(["A.", "", "B.", "C.", "", "D.", "", "Cur."])
        assert doc.window_sentences(3) == ["A.", "B.", "C.", "D."]

    def test_four_finalized_paras_want_three(self):
        # Last 3 paragraphs: ["C."], ["D.","E."], ["F."]
        doc = self._build(["A.", "B.", "", "C.", "", "D.", "E.", "", "F.", "", "Cur."])
        assert doc.window_sentences(3) == ["C.", "D.", "E.", "F."]

    def test_excludes_current_para(self):
        doc = self._build(["A.", "", "B.", "", "Cur."])
        result = doc.window_sentences(3)
        assert "Cur." not in result

    def test_trailing_boundary_no_current(self):
        # Trailing '' means current is empty
        doc = self._build(["A.", "", "B.", ""])
        assert doc.window_sentences(3) == ["A.", "B."]


# ---------------------------------------------------------------------------
# rewrite_window
# ---------------------------------------------------------------------------

class TestRewriteWindow:
    def _build(self, sentences):
        doc = Document()
        doc._sentences = list(sentences)
        return doc

    def test_split_inserts_boundary(self):
        # 1 para: ["A.","B.","C.","D."] — window=["A.","B.","C.","D."]
        # split at {2} → boundary before "C."
        doc = self._build(["A.", "B.", "C.", "D.", "", "Cur."])
        sentences = ["A.", "B.", "C.", "D."]
        changed = doc.rewrite_window(2, sentences, {2})
        assert changed is True
        assert doc._sentences == ["A.", "B.", "", "C.", "D.", "", "Cur."]

    def test_merge_removes_boundary(self):
        # 2 paras: ["A."] and ["B."] — window=["A.","B."]
        # no split → merge into one para
        doc = self._build(["A.", "", "B.", "", "Cur."])
        sentences = ["A.", "B."]
        changed = doc.rewrite_window(2, sentences, set())
        assert changed is True
        # No '' between A. and B. now
        assert doc._sentences == ["A.", "B.", "", "Cur."]

    def test_stale_returns_false(self):
        doc = self._build(["A.", "", "B.", "", "Cur."])
        changed = doc.rewrite_window(2, ["X.", "Y."], set())
        assert changed is False

    def test_no_change_returns_false(self):
        # window already has split at {1}; LLM returns same
        doc = self._build(["A.", "", "B.", "", "Cur."])
        sentences = ["A.", "B."]
        # split at {1} would produce ["A.","","B."] but window_raw is ["A."] and ["B."] with "" between
        # window_raw = ["A."] (from 2nd para) ... wait let me think
        # _sentences = ["A.","","B.","","Cur."]
        # last_boundary = 3 (before "Cur.")
        # finalized = ["A.","","B."]
        # n=2: boundaries=[1], len>=2? No, len=1 < 2 → prefix=[], window_raw=["A.","","B."]
        # flat_window = ["A.","B."]
        # new_window with split={1}: ["A.","","B."]
        # new_window == window_raw → no change → False
        changed = doc.rewrite_window(2, sentences, {1})
        assert changed is False

    def test_current_para_preserved(self):
        doc = self._build(["A.", "", "B.", "", "In progress."])
        sentences = ["A.", "B."]
        doc.rewrite_window(2, sentences, set())
        assert doc.current_para() == ["In progress."]

    def test_sentence_count_preserved(self):
        doc = self._build(["A.", "B.", "", "C.", "", "Cur."])
        sentences = ["A.", "B.", "C."]
        before = sum(1 for s in doc._sentences if s != "")
        doc.rewrite_window(2, sentences, {1})
        after = sum(1 for s in doc._sentences if s != "")
        assert before == after

    def test_empty_sentences_returns_false(self):
        doc = self._build(["A.", "", "B.", "", "Cur."])
        assert doc.rewrite_window(2, [], set()) is False

    def test_prefix_paragraphs_untouched(self):
        # 3 paras, window covers last 2, first para untouched
        doc = self._build(["Keep.", "", "A.", "", "B.", "", "Cur."])
        sentences = ["A.", "B."]
        doc.rewrite_window(2, sentences, set())
        assert "Keep." in doc._sentences


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

class TestState:
    def test_empty_document(self):
        doc = Document()
        assert doc.state() == {"paragraphs": [], "current": ""}

    def test_only_current_sentences(self):
        doc = Document()
        doc.append("First.")
        doc.append("Second.")
        st = doc.state()
        assert st["paragraphs"] == []
        assert st["current"] == "First. Second."

    def test_finalized_para_joined_with_space(self):
        doc = Document()
        doc.append("Word one.")
        doc.append("Word two.")
        doc.append("")
        doc.append("Current.")
        st = doc.state()
        assert st["paragraphs"] == ["Word one. Word two."]
        assert st["current"] == "Current."

    def test_multiple_finalized_paras(self):
        doc = Document()
        doc.append("P1A.")
        doc.append("P1B.")
        doc.append("")
        doc.append("P2A.")
        doc.append("")
        doc.append("P3.")
        st = doc.state()
        assert st["paragraphs"] == ["P1A. P1B.", "P2A."]
        assert st["current"] == "P3."

    def test_current_empty_when_trailing_boundary(self):
        doc = Document()
        doc.append("Finalized.")
        doc.append("")
        st = doc.state()
        assert st["paragraphs"] == ["Finalized."]
        assert st["current"] == ""

    def test_single_word_sentences(self):
        doc = Document()
        doc.append("Hello.")
        st = doc.state()
        assert st["current"] == "Hello."
        assert st["paragraphs"] == []
