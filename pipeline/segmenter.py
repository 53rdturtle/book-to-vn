import re
from dataclasses import dataclass

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'\w])")
# TODO(M3?): CJK character density is ~2–3× ASCII, so 60–180 chars is too
# large for Chinese input — produces overly long TTS segments. Add a
# language-aware path that uses ~25–70 for CJK-majority chapters.
_MIN_LEN = 60
_MAX_LEN = 180


@dataclass
class Segment:
    seg_id: str
    text: str


def _raw_sentences(body: str) -> list[str]:
    out: list[str] = []
    for paragraph in body.splitlines():
        p = paragraph.strip()
        if not p:
            continue
        parts = _SENT_SPLIT.split(p)
        out.extend(s.strip() for s in parts if s.strip())
    return out


def segment(chapter_id: str, body: str) -> list[Segment]:
    sentences = _raw_sentences(body)
    merged: list[str] = []
    buf = ""
    for s in sentences:
        if not buf:
            buf = s
            continue
        if len(buf) < _MIN_LEN and len(buf) + 1 + len(s) <= _MAX_LEN:
            buf = f"{buf} {s}"
        else:
            merged.append(buf)
            buf = s
    if buf:
        if merged and len(buf) < _MIN_LEN and len(merged[-1]) + 1 + len(buf) <= _MAX_LEN + 40:
            merged[-1] = f"{merged[-1]} {buf}"
        else:
            merged.append(buf)
    return [
        Segment(seg_id=f"{chapter_id}_seg{idx + 1:03d}", text=text)
        for idx, text in enumerate(merged)
    ]
