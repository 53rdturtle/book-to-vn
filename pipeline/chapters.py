from dataclasses import dataclass


@dataclass
class Chapter:
    id: str
    title: str
    body: str


def split(text: str) -> list[Chapter]:
    # TODO(M2): heuristic multi-chapter split + Gemini fallback.
    stripped = text.strip()
    first_line = next((ln.strip() for ln in stripped.splitlines() if ln.strip()), "Chapter 1")
    title = first_line if len(first_line) <= 80 else "Chapter 1"
    return [Chapter(id="ch01", title=title, body=stripped)]
