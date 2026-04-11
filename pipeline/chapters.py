"""Multi-chapter splitter.

Heuristic-first: scan for Chinese (`第N章`, `序章`, …) and English (`Chapter N`)
heading patterns. If the heuristic finds ≥2 confident boundaries, use them.
Otherwise, fall back to the Gemini splitter via `pipeline.llm.gemini_client`.

Public API: `split(text) -> list[Chapter]`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chapter:
    id: str
    title: str
    body: str


# --- Heuristic patterns ---------------------------------------------------

# Chinese: 第N章/回/卷/节/篇, plus common named markers.
_ZH_NUMBERED = re.compile(
    r"^\s*第[一二三四五六七八九十百千零两〇0-9]+[章回卷节篇](?![\w]).*$"
)
_ZH_NAMED = re.compile(
    r"^\s*(?:序章|序言|前言|楔子|引子|终章|尾声|后记|番外篇?|第\s*[一二三四五六七八九十百千0-9]+\s*[章回卷节篇])\b.*$"
)

# English.
_EN_NUMBERED = re.compile(r"^\s*Chapter\s+\d+\b.*$", re.IGNORECASE)
_EN_ROMAN = re.compile(r"^\s*CHAPTER\s+[IVXLCDM]+\b.*$")
_EN_WORD = re.compile(
    r"^\s*Chapter\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|"
    r"Eleven|Twelve|Thirteen|Fourteen|Fifteen|Sixteen|Seventeen|Eighteen|"
    r"Nineteen|Twenty)\b.*$",
    re.IGNORECASE,
)
_EN_DOTTED = re.compile(r"^\s*\d+\.\s+\S.*$")

_HEADING_PATTERNS = (_ZH_NUMBERED, _ZH_NAMED, _EN_NUMBERED, _EN_ROMAN, _EN_WORD, _EN_DOTTED)

# Long LLM-fallback threshold — if heuristic finds only one chapter but the
# text is this long, something probably went wrong and we want the LLM to try.
_LONG_SINGLE_CH_THRESHOLD = 15_000


def _is_heading(line: str) -> bool:
    return any(p.match(line) for p in _HEADING_PATTERNS)


def _cjk_ratio(text: str, sample: int = 2000) -> float:
    sample_text = text[:sample]
    if not sample_text:
        return 0.0
    cjk = sum(1 for ch in sample_text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(sample_text)


def _min_chapter_gap(text: str) -> int:
    # CJK is ~2–3× denser per char than ASCII; require a smaller gap there.
    return 200 if _cjk_ratio(text) > 0.3 else 500


def _make_id(idx: int) -> str:
    return f"ch{idx + 1:02d}"


def _clean_title(line: str, fallback: str) -> str:
    t = line.strip()
    if len(t) > 80:
        t = t[:80]
    return t or fallback


def _heuristic_split(text: str) -> list[Chapter] | None:
    """Return chapters if heuristic is confident, else None."""
    lines = text.splitlines(keepends=True)
    # Index of each heading line (in `lines`) and its running char offset.
    offsets: list[int] = []
    running = 0
    line_offsets: list[int] = []
    for ln in lines:
        line_offsets.append(running)
        running += len(ln)
    line_offsets.append(running)  # sentinel: end-of-text

    heading_indices: list[int] = [i for i, ln in enumerate(lines) if _is_heading(ln)]
    if len(heading_indices) < 2:
        return None

    gap = _min_chapter_gap(text)
    # Walk heading positions, dropping any that are too close to the previous
    # kept heading (rejects false positives like "第一章 written on the door").
    kept: list[int] = []
    for hi in heading_indices:
        if not kept:
            kept.append(hi)
            continue
        prev_end = line_offsets[kept[-1] + 1]
        this_start = line_offsets[hi]
        if this_start - prev_end >= gap:
            kept.append(hi)

    if len(kept) < 2:
        return None

    chapters: list[Chapter] = []
    first = kept[0]
    # Any prose above the first heading becomes a prologue chapter only if
    # it's substantive; otherwise discard (typical book is title + blank).
    preamble = "".join(lines[:first]).strip()
    if len(preamble) >= gap:
        chapters.append(
            Chapter(id=_make_id(0), title=_clean_title(preamble.splitlines()[0], "Prologue"), body=preamble)
        )

    for pos, hi in enumerate(kept):
        end = kept[pos + 1] if pos + 1 < len(kept) else len(lines)
        title = _clean_title(lines[hi], _make_id(len(chapters)))
        body_lines = lines[hi:end]  # include the heading so the title survives re-reads
        body = "".join(body_lines).strip()
        if not body:
            continue
        chapters.append(Chapter(id=_make_id(len(chapters)), title=title, body=body))

    return chapters if len(chapters) >= 2 else None


def _single_wrap(text: str) -> list[Chapter]:
    stripped = text.strip()
    first_line = next((ln.strip() for ln in stripped.splitlines() if ln.strip()), "Chapter 1")
    title = first_line if len(first_line) <= 80 else "Chapter 1"
    return [Chapter(id="ch01", title=title, body=stripped)]


def split(text: str) -> list[Chapter]:
    stripped = text.strip()
    if not stripped:
        raise SystemExit("Input text is empty")

    heuristic = _heuristic_split(stripped)
    if heuristic:
        return heuristic

    # No confident heuristic split. For small inputs, treat as one chapter.
    # For long ones, ask Gemini to try.
    if len(stripped) < _LONG_SINGLE_CH_THRESHOLD:
        return _single_wrap(stripped)

    # Deferred import so unit tests / offline dev don't need google-genai.
    from pipeline.llm import gemini_client

    try:
        llm_chapters = gemini_client.split_chapters(stripped)
    except Exception as e:
        print(f"[pipeline] chapter-splitter LLM fallback failed: {e}; wrapping as single chapter")
        return _single_wrap(stripped)

    if not llm_chapters:
        return _single_wrap(stripped)
    return llm_chapters
