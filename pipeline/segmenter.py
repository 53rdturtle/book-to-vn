import re
from dataclasses import dataclass

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'\w])")
_CJK_SENT_SPLIT = re.compile(r"(?<=[。！？」）])")

_MIN_LEN = 60
_MAX_LEN = 180
_CJK_MIN_LEN = 25
_CJK_MAX_LEN = 70

_CJK_RANGES = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


@dataclass
class Segment:
    seg_id: str
    text: str


def _is_cjk_majority(text: str) -> bool:
    non_ws = re.sub(r"\s", "", text)
    if not non_ws:
        return False
    cjk_count = len(_CJK_RANGES.findall(non_ws))
    return cjk_count / len(non_ws) > 0.3


def _paragraphs(body: str) -> list[list[str]]:
    """Split body into paragraphs, each paragraph into sentences."""
    return [
        [p.strip()]
        for p in body.splitlines()
        if p.strip()
    ]


def _split_sentences(paragraphs: list[list[str]], cjk: bool) -> list[list[str]]:
    """Further split each paragraph's text into sentences."""
    splitter = _CJK_SENT_SPLIT if cjk else _SENT_SPLIT
    result: list[list[str]] = []
    for para in paragraphs:
        sentences: list[str] = []
        for text in para:
            parts = splitter.split(text)
            sentences.extend(s.strip() for s in parts if s.strip())
        if sentences:
            result.append(sentences)
    return result


def _merge_paragraph(sentences: list[str], min_len: int, max_len: int) -> list[str]:
    """Merge short sentences within a single paragraph."""
    merged: list[str] = []
    buf = ""
    for s in sentences:
        if not buf:
            buf = s
            continue
        if len(buf) < min_len and len(buf) + 1 + len(s) <= max_len:
            buf = f"{buf} {s}"
        else:
            merged.append(buf)
            buf = s
    if buf:
        if merged and len(buf) < min_len and len(merged[-1]) + 1 + len(buf) <= max_len + 40:
            merged[-1] = f"{merged[-1]} {buf}"
        else:
            merged.append(buf)
    return merged


def segment(chapter_id: str, body: str) -> list[Segment]:
    cjk = _is_cjk_majority(body)
    min_len = _CJK_MIN_LEN if cjk else _MIN_LEN
    max_len = _CJK_MAX_LEN if cjk else _MAX_LEN

    para_sentences = _split_sentences(_paragraphs(body), cjk)
    all_merged: list[str] = []
    for sentences in para_sentences:
        all_merged.extend(_merge_paragraph(sentences, min_len, max_len))

    return [
        Segment(seg_id=f"{chapter_id}_seg{idx + 1:03d}", text=text)
        for idx, text in enumerate(all_merged)
    ]
