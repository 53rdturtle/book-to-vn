import json
import time

from jsonschema import Draft202012Validator

from pipeline import api_log, asset_catalog, config
from pipeline.chapters import Chapter
from pipeline.segmenter import Segment


class GeminiError(RuntimeError):
    pass


def _seg_lines(segments: list[Segment]) -> str:
    return "\n".join(f"- {s.seg_id}: {s.text}" for s in segments)


def _render_speakers_prompt(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
    known_cast_text: str,
) -> str:
    schema_text = config.LLM_SPEAKERS_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.SPEAKERS_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{SCHEMA}", schema_text)
        .replace("{CHAPTER_ID}", chapter_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{SEGMENTS}", _seg_lines(segments))
        .replace("{KNOWN_CAST}", known_cast_text)
    )


def _render_backgrounds_prompt(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
) -> str:
    schema_text = config.LLM_BACKGROUNDS_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.BACKGROUNDS_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{SCHEMA}", schema_text)
        .replace("{CHAPTER_ID}", chapter_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{SEGMENTS}", _seg_lines(segments))
        .replace("{BG_MANIFEST}", ", ".join(asset_catalog.BG_IDS))
    )


def _render_stage_prompt(
    chapter_id: str,
    chapter_title: str,
    chapter_cast_text: str,
    speaker_lines: str,
) -> str:
    schema_text = config.LLM_STAGE_SCHEMA_PATH.read_text(encoding="utf-8")
    template = config.STAGE_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{SCHEMA}", schema_text)
        .replace("{CHAPTER_ID}", chapter_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{CHAPTER_CAST}", chapter_cast_text)
        .replace("{SPEAKER_LINES}", speaker_lines)
    )


def call_gemini_json(prompt: str, call_type: str = "unknown") -> dict:
    return _call_gemini_json(prompt, call_type=call_type)


def _call_gemini_json(prompt: str, call_type: str = "unknown") -> dict:
    if not config.GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            t0 = time.time()
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            elapsed = time.time() - t0
            text = resp.text or ""
            note = "ok"
            if not text.strip():
                pf = getattr(resp, "prompt_feedback", None)
                block_reason = getattr(pf, "block_reason", None) if pf else None
                cands = getattr(resp, "candidates", None) or []
                finish_reason = getattr(cands[0], "finish_reason", None) if cands else None
                safety = getattr(cands[0], "safety_ratings", None) if cands else None
                note = f"empty response: block_reason={block_reason} finish_reason={finish_reason}"
                print(
                    f"[gemini_client] empty response (call_type={call_type}, attempt={attempt + 1}): "
                    f"finish_reason={finish_reason} block_reason={block_reason} "
                    f"safety_ratings={safety} prompt_feedback={pf}"
                )
            dump_path = api_log.dump_call(call_type, prompt, text, note=f"attempt={attempt + 1} {note}")
            if not text.strip():
                print(f"[gemini_client] call dumped to: {dump_path}")
            result = json.loads(text)

            usage = resp.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            api_log.current.record(
                model=config.GEMINI_MODEL,
                call_type=call_type,
                prompt_summary=prompt[:200],
                response_summary=text[:200],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_s=elapsed,
            )

            return result
        except Exception as e:
            last_err = e
            if attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    raise GeminiError(f"Gemini call failed after retries: {last_err}")


def generate_speakers(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
    known_cast_text: str = "None — introduce characters as needed.",
) -> tuple[dict, str]:
    """Pass A: per-segment speaker attribution + new character IDs.

    Returns (llm_speakers, rendered_prompt).
    """
    prompt = _render_speakers_prompt(chapter_id, chapter_title, segments, known_cast_text)
    return _call_gemini_json(prompt, call_type="speakers"), prompt


def generate_backgrounds(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
) -> tuple[dict, str]:
    """Pass B: anchored BG changes through the chapter.

    Returns (llm_backgrounds, rendered_prompt).
    """
    prompt = _render_backgrounds_prompt(chapter_id, chapter_title, segments)
    return _call_gemini_json(prompt, call_type="backgrounds"), prompt


def generate_stage(
    chapter_id: str,
    chapter_title: str,
    chapter_cast_text: str,
    speaker_lines: str,
) -> tuple[dict, str]:
    """Pass C: character show/hide/expression directions.

    `chapter_cast_text` is a bulleted list of every character id that may
    appear on stage in this chapter (KNOWN CAST ∪ new characters from Pass A).
    `speaker_lines` is one line per segment: ``seg_id: speaker — text``.

    Returns (llm_stage, rendered_prompt).
    """
    prompt = _render_stage_prompt(chapter_id, chapter_title, chapter_cast_text, speaker_lines)
    return _call_gemini_json(prompt, call_type="stage"), prompt


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
    raw = _call_gemini_json(prompt, call_type="splitter")

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
