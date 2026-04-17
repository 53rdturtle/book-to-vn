"""LLM-driven visual descriptions for characters and backgrounds.

Gemini is asked to propose visual descriptions given the book title, the set
of character/background IDs, and a short excerpt for tone. For well-known
source works, Gemini leans on its own knowledge; otherwise it infers from
the excerpt.
"""
from pathlib import Path

from pipeline.llm import gemini_client

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "visual_descriptions.txt"


VALID_SILHOUETTE_TYPES = {
    "adult_male", "adult_female",
    "child_male", "child_female",
    "elder_male", "elder_female",
}

_PROMPT_TO_INTERNAL = {
    "young_male": "child_male",
    "young_female": "child_female",
}


def _render(
    book_title: str,
    char_ids: list[str],
    bg_ids: list[str],
    excerpt: str,
    minor_char_ids: list[str] | None = None,
) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    chars = "\n".join(f"- {c}" for c in char_ids) or "(none)"
    minors = "\n".join(f"- {c}" for c in (minor_char_ids or [])) or "(none)"
    bgs = "\n".join(f"- {b}" for b in bg_ids) or "(none)"
    return (
        template.replace("{BOOK_TITLE}", book_title)
        .replace("{CHARACTERS}", chars)
        .replace("{MINOR_CHARACTERS}", minors)
        .replace("{BACKGROUNDS}", bgs)
        .replace("{EXCERPT}", excerpt)
    )


def propose(
    book_title: str,
    char_ids: list[str],
    bg_ids: list[str],
    excerpt: str,
    minor_char_ids: list[str] | None = None,
) -> dict:
    """Return {"visual_style": str, "characters": {...}, "display_names": {...},
    "minor_silhouettes": {...}, "backgrounds": {...}}."""
    prompt = _render(book_title, char_ids, bg_ids, excerpt, minor_char_ids)
    raw = gemini_client.call_gemini_json(prompt, call_type="visual_desc")
    minor_sil = {}
    for cid, stype in raw.get("minor_silhouettes", {}).items():
        stype = _PROMPT_TO_INTERNAL.get(stype, stype)
        if stype in VALID_SILHOUETTE_TYPES:
            minor_sil[cid] = stype
        else:
            minor_sil[cid] = "adult_male"
    return {
        "visual_style": str(raw.get("visual_style", "")),
        "characters": dict(raw.get("characters", {})),
        "display_names": dict(raw.get("display_names", {})),
        "minor_silhouettes": minor_sil,
        "backgrounds": dict(raw.get("backgrounds", {})),
    }
