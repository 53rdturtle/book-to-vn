"""LLM-driven visual descriptions for characters and backgrounds.

Gemini is asked to propose visual descriptions given the book title, the set
of character/background IDs, and a short excerpt for tone. For well-known
source works, Gemini leans on its own knowledge; otherwise it infers from
the excerpt.
"""
from pathlib import Path

from pipeline.llm import gemini_client

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "visual_descriptions.txt"


def _render(book_title: str, char_ids: list[str], bg_ids: list[str], excerpt: str) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    chars = "\n".join(f"- {c}" for c in char_ids) or "(none)"
    bgs = "\n".join(f"- {b}" for b in bg_ids) or "(none)"
    return (
        template.replace("{BOOK_TITLE}", book_title)
        .replace("{CHARACTERS}", chars)
        .replace("{BACKGROUNDS}", bgs)
        .replace("{EXCERPT}", excerpt)
    )


def propose(
    book_title: str,
    char_ids: list[str],
    bg_ids: list[str],
    excerpt: str,
) -> dict:
    """Return {"characters": {...}, "backgrounds": {...}}."""
    if not char_ids and not bg_ids:
        return {"characters": {}, "backgrounds": {}}
    prompt = _render(book_title, char_ids, bg_ids, excerpt)
    raw = gemini_client.call_gemini_json(prompt, call_type="visual_desc")
    return {
        "characters": dict(raw.get("characters", {})),
        "backgrounds": dict(raw.get("backgrounds", {})),
    }
