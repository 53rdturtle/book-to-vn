"""Orchestrate the three-pass LLM timeline generation.

Pass A (speakers) and Pass B (backgrounds) run in parallel. Pass C (stage)
runs after speakers so it sees which characters exist in this chapter.
Their outputs are deterministically merged into the compact LLM timeline
shape (``llm_timeline.schema.json``) that the rest of the pipeline expects.

Each pass is cached independently so a manifest tweak that only invalidates
backgrounds does not force the other two passes to rerun.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import asset_catalog, cache, config
from pipeline.llm import gemini_client
from pipeline.segmenter import Segment


class AssemblyError(RuntimeError):
    pass


@dataclass
class PassLog:
    name: str  # "speakers" | "backgrounds" | "stage"
    prompt: str
    response: dict
    cache_hit: bool = False


@dataclass
class AssemblyResult:
    llm_timeline: dict
    new_display_names: dict[str, str]
    passes: list[PassLog] = field(default_factory=list)


def _validate(obj: dict, schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        msgs = [f"- {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
        raise AssemblyError(f"{label} failed schema validation:\n" + "\n".join(msgs))


def _segments_signature(segs: list[Segment]) -> list[dict]:
    return [asdict(s) for s in segs]


def _cache_key(pass_name: str, *parts) -> str:
    return cache.content_key(f"gemini_{pass_name}", config.GEMINI_MODEL, *parts)


def _render_chapter_cast(
    known_cast_text: str,
    new_characters: list[dict],
) -> str:
    """Bullet list of every char id usable in Pass C (for `stage.id`)."""
    lines: list[str] = []
    if known_cast_text and known_cast_text.strip() and not known_cast_text.startswith("None"):
        lines.append(known_cast_text.strip())
    for entry in new_characters:
        cid = entry.get("id", "")
        name = entry.get("display_name", cid)
        if cid:
            lines.append(f"- {cid}: {name} (new in this chapter)")
    return "\n".join(lines) if lines else "None."


def _render_speaker_lines(segments: list[Segment], speakers: list[dict]) -> str:
    by_id = {e["seg_id"]: e["speaker"] for e in speakers}
    out: list[str] = []
    for s in segments:
        spk = by_id.get(s.seg_id, "narrator")
        out.append(f"- {s.seg_id} [{spk}]: {s.text}")
    return "\n".join(out)


def _validate_bg_ids(bgs: dict) -> list[str]:
    unknown: list[str] = []
    for entry in bgs.get("backgrounds", []):
        if entry["bg_id"] not in asset_catalog.BG_SET:
            unknown.append(entry["bg_id"])
    return unknown


def _check_speakers_cover_all(
    speakers_resp: dict, segments: list[Segment], known_char_ids: set[str]
) -> None:
    expected = [s.seg_id for s in segments]
    got_entries = speakers_resp.get("speakers", [])
    got_ids = [e["seg_id"] for e in got_entries]
    if got_ids != expected:
        missing = [x for x in expected if x not in got_ids]
        extra = [x for x in got_ids if x not in expected]
        raise AssemblyError(
            "speakers pass did not cover all segments in order:\n"
            f"  missing: {missing}\n  extra: {extra}"
        )
    new_ids = {e["id"] for e in speakers_resp.get("new_characters", [])}
    allowed = known_char_ids | new_ids | {"narrator"}
    unknown = sorted({e["speaker"] for e in got_entries} - allowed)
    if unknown:
        raise AssemblyError(
            f"speakers pass used unknown speaker ids not in known cast or "
            f"new_characters: {unknown}"
        )


def _check_stage_refs(stage_resp: dict, segments: list[Segment], allowed_ids: set[str]) -> None:
    seg_ids = {s.seg_id for s in segments}
    for e in stage_resp.get("stage", []):
        if e["before_seg_id"] not in seg_ids:
            raise AssemblyError(
                f"stage pass referenced unknown seg_id {e['before_seg_id']!r}"
            )
        if e["id"] not in allowed_ids:
            raise AssemblyError(
                f"stage pass referenced unknown character id {e['id']!r}; "
                f"allowed: {sorted(allowed_ids)}"
            )


def _assemble_commands(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
    speakers_resp: dict,
    bgs_resp: dict,
    stage_resp: dict,
) -> dict:
    speaker_by_seg = {e["seg_id"]: e["speaker"] for e in speakers_resp["speakers"]}

    # Group bg/stage entries by their anchor seg_id, preserving list order.
    bgs_by_anchor: dict[str, list[dict]] = {}
    for e in bgs_resp.get("backgrounds", []):
        bgs_by_anchor.setdefault(e["before_seg_id"], []).append(e)
    stage_by_anchor: dict[str, list[dict]] = {}
    for e in stage_resp.get("stage", []):
        stage_by_anchor.setdefault(e["before_seg_id"], []).append(e)

    commands: list[dict] = []
    for seg in segments:
        for e in bgs_by_anchor.get(seg.seg_id, []):
            commands.append({"type": "bg", "id": e["bg_id"]})
        for e in stage_by_anchor.get(seg.seg_id, []):
            if e["type"] == "show":
                commands.append({
                    "type": "char_show",
                    "id": e["id"],
                    "expr": e["expr"],
                    "slot": e["slot"],
                })
            else:  # hide
                commands.append({"type": "char_hide", "id": e["id"]})
        commands.append({
            "type": "say",
            "speaker": speaker_by_seg[seg.seg_id],
            "seg": seg.seg_id,
        })

    return {"chapter_id": chapter_id, "title": chapter_title, "commands": commands}


def assemble_timeline(
    chapter_id: str,
    chapter_title: str,
    segments: list[Segment],
    known_cast_text: str,
    known_char_ids: set[str],
    *,
    no_cache: bool = False,
) -> AssemblyResult:
    """Run all three passes (A+B in parallel, C after A) and merge.

    `known_char_ids` is the set of character IDs already established in prior
    chapters' cast (excluding "narrator"). Used to decide which speaker ids
    must be declared as new in Pass A.
    """
    seg_sig = _segments_signature(segments)

    speakers_prompt_tpl = config.SPEAKERS_PROMPT_PATH.read_text(encoding="utf-8")
    speakers_schema_text = config.LLM_SPEAKERS_SCHEMA_PATH.read_text(encoding="utf-8")
    bgs_prompt_tpl = config.BACKGROUNDS_PROMPT_PATH.read_text(encoding="utf-8")
    bgs_schema_text = config.LLM_BACKGROUNDS_SCHEMA_PATH.read_text(encoding="utf-8")
    stage_prompt_tpl = config.STAGE_PROMPT_PATH.read_text(encoding="utf-8")
    stage_schema_text = config.LLM_STAGE_SCHEMA_PATH.read_text(encoding="utf-8")

    speakers_key = _cache_key(
        "speakers",
        speakers_prompt_tpl, speakers_schema_text,
        chapter_id, chapter_title, seg_sig, known_cast_text,
    )
    bgs_key = _cache_key(
        "backgrounds",
        bgs_prompt_tpl, bgs_schema_text,
        chapter_id, chapter_title, seg_sig,
        asset_catalog.BG_IDS,
    )

    def run_speakers() -> tuple[dict, str, bool]:
        if not no_cache:
            cached = cache.get("gemini_speakers", speakers_key)
            if cached is not None:
                prompt = gemini_client._render_speakers_prompt(
                    chapter_id, chapter_title, segments, known_cast_text
                )
                return cached, prompt, True
        resp, prompt = gemini_client.generate_speakers(
            chapter_id, chapter_title, segments, known_cast_text=known_cast_text
        )
        cache.put("gemini_speakers", speakers_key, resp)
        return resp, prompt, False

    def run_backgrounds() -> tuple[dict, str, bool]:
        if not no_cache:
            cached = cache.get("gemini_backgrounds", bgs_key)
            if cached is not None:
                prompt = gemini_client._render_backgrounds_prompt(
                    chapter_id, chapter_title, segments
                )
                return cached, prompt, True
        for attempt in range(2):
            resp, prompt = gemini_client.generate_backgrounds(
                chapter_id, chapter_title, segments
            )
            _validate(resp, config.LLM_BACKGROUNDS_SCHEMA_PATH,
                      f"backgrounds output for {chapter_id}")
            unknown = _validate_bg_ids(resp)
            if not unknown:
                cache.put("gemini_backgrounds", bgs_key, resp)
                return resp, prompt, False
            if attempt == 1:
                raise AssemblyError(
                    f"backgrounds pass returned unknown bg_ids after retry: {unknown}"
                )
            print(f"[pipeline] {chapter_id}: unknown bg_ids {unknown} — retrying")
        raise AssemblyError("unreachable")

    # A + B in parallel
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_speakers = pool.submit(run_speakers)
        fut_bgs = pool.submit(run_backgrounds)
        speakers_resp, speakers_prompt, speakers_hit = fut_speakers.result()
        bgs_resp, bgs_prompt, bgs_hit = fut_bgs.result()

    _validate(speakers_resp, config.LLM_SPEAKERS_SCHEMA_PATH,
              f"speakers output for {chapter_id}")
    _check_speakers_cover_all(speakers_resp, segments, known_char_ids)

    # Ensure backgrounds were validated above (only when freshly generated).
    # When loaded from cache we still need to validate.
    if bgs_hit:
        _validate(bgs_resp, config.LLM_BACKGROUNDS_SCHEMA_PATH,
                  f"cached backgrounds output for {chapter_id}")
        unknown = _validate_bg_ids(bgs_resp)
        if unknown:
            raise AssemblyError(
                f"cached backgrounds output has unknown bg_ids: {unknown}"
            )

    # Pass C runs after A — needs the chapter's full cast + per-seg speakers.
    new_characters = speakers_resp.get("new_characters", [])
    chapter_cast_text = _render_chapter_cast(known_cast_text, new_characters)
    speaker_lines = _render_speaker_lines(segments, speakers_resp["speakers"])
    allowed_stage_ids = known_char_ids | {e["id"] for e in new_characters}

    stage_key = _cache_key(
        "stage",
        stage_prompt_tpl, stage_schema_text,
        chapter_id, chapter_title, chapter_cast_text, speaker_lines,
    )

    def run_stage() -> tuple[dict, str, bool]:
        if not no_cache:
            cached = cache.get("gemini_stage", stage_key)
            if cached is not None:
                prompt = gemini_client._render_stage_prompt(
                    chapter_id, chapter_title, chapter_cast_text, speaker_lines
                )
                return cached, prompt, True
        resp, prompt = gemini_client.generate_stage(
            chapter_id, chapter_title, chapter_cast_text, speaker_lines
        )
        cache.put("gemini_stage", stage_key, resp)
        return resp, prompt, False

    stage_resp, stage_prompt, stage_hit = run_stage()
    _validate(stage_resp, config.LLM_STAGE_SCHEMA_PATH,
              f"stage output for {chapter_id}")
    _check_stage_refs(stage_resp, segments, allowed_stage_ids)

    llm_timeline = _assemble_commands(
        chapter_id, chapter_title, segments,
        speakers_resp, bgs_resp, stage_resp,
    )
    _validate(llm_timeline, config.LLM_SCHEMA_PATH,
              f"assembled LLM timeline for {chapter_id}")

    new_display_names = {
        e["id"]: e["display_name"]
        for e in new_characters
        if e.get("display_name")
    }

    return AssemblyResult(
        llm_timeline=llm_timeline,
        new_display_names=new_display_names,
        passes=[
            PassLog("speakers", speakers_prompt, speakers_resp, speakers_hit),
            PassLog("backgrounds", bgs_prompt, bgs_resp, bgs_hit),
            PassLog("stage", stage_prompt, stage_resp, stage_hit),
        ],
    )
