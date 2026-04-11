import json
import time

from jsonschema import Draft202012Validator

from pipeline import config
from pipeline.chapters import Chapter
from pipeline.segmenter import Segment


class GeminiError(RuntimeError):
    pass


def _render_timeline_prompt(
    chapter_id: str,
    chapter_title: str,
    chapter_body: str,
    segments: list[Segment],
    known_cast_text: str,
) -> str:
    schema_text = config.LLM_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.PROMPT_PATH.read_text(encoding="utf-8")
    seg_lines = "\n".join(f"- {s.seg_id}: {s.text}" for s in segments)
    return (
        template.replace("{SCHEMA}", schema_text)
        .replace("{CHAPTER_ID}", chapter_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{SEGMENTS}", seg_lines)
        .replace("{CHAPTER_BODY}", chapter_body)
        .replace("{KNOWN_CAST}", known_cast_text)
    )


def _call_gemini_json(prompt: str) -> dict:
    if not config.GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            text = resp.text or ""
            return json.loads(text)
        except Exception as e:
            last_err = e
            if attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    raise GeminiError(f"Gemini call failed after retries: {last_err}")


def generate_timeline(
    chapter_id: str,
    chapter_title: str,
    chapter_body: str,
    segments: list[Segment],
    known_cast_text: str = "None — introduce characters as needed.",
) -> dict:
    prompt = _render_timeline_prompt(
        chapter_id, chapter_title, chapter_body, segments, known_cast_text
    )
    return _call_gemini_json(prompt)


# --- Chapter splitter fallback --------------------------------------------


def _render_splitter_prompt(text: str) -> str:
    schema_text = config.SPLIT_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.SPLITTER_PROMPT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines))
    return template.replace("{SCHEMA}", schema_text).replace("{NUMBERED_TEXT}", numbered)


def split_chapters(text: str) -> list[Chapter]:
    """Gemini-driven chapter split fallback. Returns empty list on failure."""
    prompt = _render_splitter_prompt(text)
    raw = _call_gemini_json(prompt)

    schema = json.loads(config.SPLIT_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        msgs = "; ".join(e.message for e in errors[:5])
        raise GeminiError(f"chapter splitter output failed schema validation: {msgs}")

    lines = text.splitlines()
    chapters: list[Chapter] = []
    for idx, entry in enumerate(raw["chapters"]):
        start = max(0, int(entry["start_line"]))
        end = min(len(lines) - 1, int(entry["end_line"]))
        if end < start:
            continue
        body = "\n".join(lines[start : end + 1]).strip()
        if not body:
            continue
        title = entry["title"].strip() or f"ch{idx + 1:02d}"
        chapters.append(Chapter(id=f"ch{idx + 1:02d}", title=title[:80], body=body))
    return chapters
