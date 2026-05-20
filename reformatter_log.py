import time
from pathlib import Path

import server

_log_path = Path(__file__).parent / "reformatter.log"
if _log_path.exists():
    _log_path.rename(_log_path.with_suffix(".log.bak"))
_f = _log_path.open("w", buffering=1)  # fresh log each run


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def log_chunk_received(chunk: str) -> None:
    _f.write(f"\n{'='*80}\n")
    _f.write(f"[{_ts()}] CHUNK RECEIVED\n")
    _f.write(f"{chunk}\n")
    server.push_log({"step": "chunk", "ts": _ts(), "text": chunk})


def log_llm_input(numbered: str, join_idx: int, n_sentences: int) -> None:
    _f.write(f"[{_ts()}] LLM INPUT ({n_sentences} sentences)\n")
    _f.write(f"{numbered}\n")
    if join_idx > 0:
        _f.write(f"  join point: between sentences {join_idx - 1} and {join_idx}\n")
    server.push_log({"step": "llm_input", "context": numbered,
                     "join_idx": join_idx, "n": n_sentences})


def log_llm_raw(content: str) -> None:
    _f.write(f"[{_ts()}] LLM RAW\n")
    _f.write(f"  {content}\n")
    server.push_log({"step": "llm_raw", "content": content})


def log_decision(continuation: bool, lowercase_join: bool,
                 splits: set, groups: list[str]) -> None:
    lines = []
    if continuation:
        lc = "lowercase" if lowercase_join else "preserve case"
        lines.append(f"continuation=true ({lc})")
    if splits:
        lines.append(f"splits={sorted(splits)}")
    if len(groups) > 1:
        _f.write(f"[{_ts()}] DECISION: {' | '.join(lines) or 'none'} → {len(groups)} paragraphs\n")
        for i, g in enumerate(groups):
            _f.write(f"  [{i}] {g[:120]}{'...' if len(g) > 120 else ''}\n")
    else:
        _f.write(f"[{_ts()}] DECISION: {' | '.join(lines) or 'no change'}\n")
        if groups:
            _f.write(f"  paragraph: {groups[0][:120]}{'...' if len(groups[0]) > 120 else ''}\n")

    action = "split" if len(groups) > 1 else ("merge" if continuation else "append")
    server.push_log({
        "step": "decision",
        "action": action,
        "continuation": continuation,
        "lowercase_join": lowercase_join,
        "splits": sorted(splits),
        "paragraph": groups[0] if groups else "",
        "new_paragraph": groups[1] if len(groups) > 1 else "",
    })


def log_fallback(reason: str) -> None:
    _f.write(f"[{_ts()}] FALLBACK ({reason})\n")
    server.push_log({"step": "fallback", "reason": reason})


def log_segment(last: str, chunk: str, sentences: list[str], continuation: bool) -> None:
    _f.write(f"[{_ts()}] SEGMENT\n")
    _f.write(f"  LAST:  {last[:120]}\n")
    _f.write(f"  CHUNK: {chunk[:120]}\n")
    _f.write(f"  continuation={continuation}\n")
    for i, s in enumerate(sentences):
        _f.write(f"  [{i}] {s[:120]}\n")
    server.push_log({"step": "segment", "last": last, "chunk": chunk,
                     "sentences": sentences, "continuation": continuation})


def log_current_sentences(sentences: list[str]) -> None:
    _f.write(f"[{_ts()}] CURRENT SENTENCES ({len(sentences)})\n")
    for i, s in enumerate(sentences):
        _f.write(f"  [{i}] {s[:120]}\n")


def log_continuation_queued(last: str, first: str, rest: list[str]) -> None:
    b_context = " ".join([first] + rest)
    _f.write(f"[{_ts()}] CONT QUEUED\n")
    _f.write(f"  LAST:    {last[:120]}\n")
    _f.write(f"  B+CTX:   {b_context[:120]}\n")
    server.push_log({"step": "continuation_queued", "last": last, "b_context": b_context})


def log_continuation_result(last: str, first: str, verdict: str, merged: bool,
                            v1: str = "", v2: str = "") -> None:
    action = "MERGED" if merged else "stale/skip"
    _f.write(f"[{_ts()}] CONT RESULT verdict={verdict} → {action}\n")
    if v1:
        _f.write(f"  SENT 1: {v1[:120]}\n")
        _f.write(f"  SENT 2: {v2[:120]}\n")
    server.push_log({"step": "continuation_result", "verdict": verdict,
                     "merged": merged, "last": last, "first": first,
                     "v1": v1, "v2": v2})


def log_splits_scan(sentences: list[str]) -> None:
    _f.write(f"[{_ts()}] SPLITS SCAN ({len(sentences)} sentences)\n")
    for i, s in enumerate(sentences):
        _f.write(f"  [{i}] {s[:120]}\n")
    server.push_log({"step": "splits_scan", "sentences": sentences})


def log_splits(split_indices: set[int]) -> None:
    _f.write(f"[{_ts()}] SPLITS → {sorted(split_indices) or 'none'}\n")
    server.push_log({"step": "splits", "indices": sorted(split_indices)})


def log_restructure(sentences: list[str]) -> None:
    _f.write(f"[{_ts()}] RESTRUCTURE ({len(sentences)} sentences)\n")
    for i, s in enumerate(sentences):
        _f.write(f"  [{i}] {s[:120]}\n")
    server.push_log({"step": "restructure", "sentences": sentences})


def log_restructure_result(split_indices: set[int]) -> None:
    _f.write(f"[{_ts()}] RESTRUCTURE → splits={sorted(split_indices) or 'none'}\n")
    server.push_log({"step": "restructure_result", "indices": sorted(split_indices)})


def log_new_para(next_break: int) -> None:
    _f.write(f"[{_ts()}] NEW PARA (next break in {next_break} sentences)\n")
    server.push_log({"step": "new_para", "next_break": next_break})
