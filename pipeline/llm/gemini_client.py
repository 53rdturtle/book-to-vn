import json
import time
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import asset_catalog, config
from pipeline.chapters import Chapter
from pipeline.segmenter import Segment

_FALLBACK_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "prompts"


def _dump_call(call_type: str, prompt: str, response: str, note: str = "") -> Path:
    from pipeline import api_log
    base = (api_log.OUT_DIR / "logs" / "calls") if api_log.OUT_DIR else _FALLBACK_LOG_DIR
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = base / f"{ts}_{call_type}.txt"
    header = (
        f"# call_type={call_type}\n"
        f"# timestamp={ts}\n"
        f"# note={note}\n"
        f"# prompt_length={len(prompt)}\n"
        f"# response_length={len(response)}\n"
        f"--- PROMPT ---\n"
    )
    path.write_text(header + prompt + "\n--- RESPONSE ---\n" + response + "\n", encoding="utf-8")
    return path


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
        .replace("{BG_MANIFEST}", ", ".join(asset_catalog.BG_IDS))
        .replace("{BGM_MANIFEST}", ", ".join(asset_catalog.BGM_IDS))
        .replace("{SE_MANIFEST}", ", ".join(asset_catalog.SE_IDS))
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
            dump_path = _dump_call(call_type, prompt, text, note=f"attempt={attempt + 1} {note}")
            if not text.strip():
                print(f"[gemini_client] call dumped to: {dump_path}")
            result = json.loads(text)

            usage = resp.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            from pipeline import api_log
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


def generate_timeline(
    chapter_id: str,
    chapter_title: str,
    chapter_body: str,
    segments: list[Segment],
    known_cast_text: str = "None — introduce characters as needed.",
) -> tuple[dict, str]:
    """Returns (llm_timeline, rendered_prompt)."""
    prompt = _render_timeline_prompt(
        chapter_id, chapter_title, chapter_body, segments, known_cast_text
    )
    return _call_gemini_json(prompt, call_type="timeline"), prompt


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
