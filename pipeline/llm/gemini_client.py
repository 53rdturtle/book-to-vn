import json
import time

from pipeline import config
from pipeline.segmenter import Segment


class GeminiError(RuntimeError):
    pass


def _render_prompt(chapter_id: str, chapter_title: str, chapter_body: str, segments: list[Segment]) -> str:
    schema_text = config.LLM_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.PROMPT_PATH.read_text(encoding="utf-8")
    seg_lines = "\n".join(f"- {s.seg_id}: {s.text}" for s in segments)
    return (
        template.replace("{SCHEMA}", schema_text)
        .replace("{CHAPTER_ID}", chapter_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{SEGMENTS}", seg_lines)
        .replace("{CHAPTER_BODY}", chapter_body)
    )


def generate_timeline(
    chapter_id: str,
    chapter_title: str,
    chapter_body: str,
    segments: list[Segment],
) -> dict:
    if not config.GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = _render_prompt(chapter_id, chapter_title, chapter_body, segments)

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
