"""Discrete pipeline step functions.

Extracted from ``pipeline/__main__.py`` so that both the CLI and the editor
UI can drive the pipeline one step at a time. Every function here is
side-effect explicit (paths taken as arguments, no stdin prompts).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import (
    api_log,
    asset_catalog,
    backgrounds as bg_mod,
    bundle,
    cache,
    cast as cast_mod,
    chapters,
    config,
    segmenter,
)
from pipeline.assets import placeholders
from pipeline.assets.adapter import ImageAdapter
from pipeline.assets.placeholders import PlaceholderAdapter
from pipeline.build_log import BuildLog
from pipeline.llm import gemini_client, visual_descriptions
from pipeline.segmenter import Segment


ALL_EXPRS = ("neutral", "smile", "sad", "angry", "surprised", "worried", "thinking")


class AssetIdError(Exception):
    pass


# ---------- validation helpers ----------

def validate_asset_ids(llm_timeline: dict) -> None:
    unknown: list[str] = []
    for cmd in llm_timeline.get("commands", []):
        t = cmd.get("type")
        if t == "bg" and cmd["id"] not in asset_catalog.BG_SET:
            unknown.append(f"bg '{cmd['id']}'")
        elif t == "bgm_play" and cmd["id"] not in asset_catalog.BGM_SET:
            unknown.append(f"bgm '{cmd['id']}'")
        elif t == "se" and cmd["id"] not in asset_catalog.SE_SET:
            unknown.append(f"se '{cmd['id']}'")
    if unknown:
        raise AssetIdError(f"Unknown asset IDs: {', '.join(unknown)}")


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_schema(timeline: dict, schema_path: Path, label: str) -> None:
    validator = Draft202012Validator(_load_schema(schema_path))
    errors = sorted(validator.iter_errors(timeline), key=lambda e: list(e.path))
    if errors:
        msgs = [f"- {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
        raise SystemExit(f"{label} failed schema validation:\n" + "\n".join(msgs))


def expand_says(llm_timeline: dict, segments: list[Segment]) -> dict:
    seg_table = {s.seg_id: s.text for s in segments}
    expected_order = [s.seg_id for s in segments]
    seen: list[str] = []

    expanded_commands: list[dict] = []
    for cmd in llm_timeline.get("commands", []):
        if cmd.get("type") != "say":
            expanded_commands.append(cmd)
            continue
        seg_id = cmd.get("seg", "")
        if seg_id not in seg_table:
            raise SystemExit(f"LLM referenced unknown seg_id: {seg_id!r}")
        seen.append(seg_id)
        expanded_commands.append({
            "type": "say",
            "speaker": cmd["speaker"],
            "text": seg_table[seg_id],
            "voice_clip": f"{seg_id}.ogg",
        })

    if seen != expected_order:
        missing = [s for s in expected_order if s not in seen]
        extra = [s for s in seen if s not in expected_order]
        dupes = [s for s in seen if seen.count(s) > 1]
        raise SystemExit(
            "Segment coverage mismatch:\n"
            f"  expected order: {expected_order}\n"
            f"  got order:      {seen}\n"
            f"  missing: {missing}\n  extra: {extra}\n  duplicated: {sorted(set(dupes))}"
        )

    return {
        "chapter_id": llm_timeline["chapter_id"],
        "title": llm_timeline["title"],
        "commands": expanded_commands,
    }


def _segments_signature(segs: list[Segment]) -> list[dict]:
    return [asdict(s) for s in segs]


# ---------- text -> entries ----------

def split_and_segment(text: str) -> list[tuple]:
    """Return a list of ``(Chapter, list[Segment])`` tuples (no timelines yet)."""
    chs = chapters.split(text)
    if not chs:
        raise SystemExit("Chapter splitter returned no chapters")
    out = []
    for chapter in chs:
        segs = segmenter.segment(chapter.id, chapter.body)
        if not segs:
            raise SystemExit(f"No segments produced from chapter {chapter.id!r}")
        out.append((chapter, segs))
    return out


def generate_timelines(
    raw_entries: list[tuple],
    *,
    log: BuildLog | None = None,
    no_cache: bool = False,
) -> tuple[list[bundle.BundleEntry], dict]:
    """Run Gemini on each chapter, expand says, update cast. Returns (entries, cast)."""
    prompt_template = config.PROMPT_PATH.read_text(encoding="utf-8")
    llm_schema_text = config.LLM_SCHEMA_PATH.read_text(encoding="utf-8")
    cast = {"cast": {}}
    entries: list[bundle.BundleEntry] = []

    for chapter, segs in raw_entries:
        if log:
            log.segments(chapter.id, segs)

        known_cast_text = cast_mod.render_known_cast(cast)
        cache_key = cache.content_key(
            "gemini_timeline",
            config.GEMINI_MODEL,
            prompt_template,
            llm_schema_text,
            chapter.id,
            chapter.title,
            chapter.body,
            _segments_signature(segs),
            known_cast_text,
            asset_catalog.BG_IDS,
            asset_catalog.BGM_IDS,
            asset_catalog.SE_IDS,
        )

        llm_timeline: dict | None = None
        if not no_cache:
            llm_timeline = cache.get("gemini_timeline", cache_key)
            if llm_timeline is not None:
                print(f"[pipeline] {chapter.id}: cache hit")

        if llm_timeline is None:
            for attempt in range(2):
                print(f"[pipeline] {chapter.id}: calling Gemini model {config.GEMINI_MODEL}"
                      + (" (retry)" if attempt else ""))
                t0 = time.time()
                llm_timeline, rendered_prompt = gemini_client.generate_timeline(
                    chapter.id, chapter.title, chapter.body, segs,
                    known_cast_text=known_cast_text,
                )
                elapsed = time.time() - t0
                if log:
                    log.gemini_prompt(chapter.id, rendered_prompt)
                    log.gemini_response(chapter.id, llm_timeline, elapsed)
                validate_schema(llm_timeline, config.LLM_SCHEMA_PATH,
                                f"LLM output for {chapter.id}")
                try:
                    validate_asset_ids(llm_timeline)
                    break
                except AssetIdError as e:
                    if log:
                        log.validation_error(chapter.id, "asset_ids", str(e))
                    if attempt == 1:
                        raise SystemExit(f"Asset-ID validation failed after retry: {e}")
                    print(f"[pipeline] {chapter.id}: {e} — retrying")
            cache.put("gemini_timeline", cache_key, llm_timeline)
        else:
            validate_schema(llm_timeline, config.LLM_SCHEMA_PATH,
                            f"cached LLM output for {chapter.id}")
            validate_asset_ids(llm_timeline)

        timeline = expand_says(llm_timeline, segs)
        validate_schema(timeline, config.SCHEMA_PATH,
                        f"Runtime timeline for {chapter.id}")
        entries.append((chapter, segs, timeline))
        cast = cast_mod.update_from_timeline(cast, timeline, chapter.id)
        if log:
            log.cast_snapshot(chapter.id, cast)
    return entries, cast


# ---------- manifests / major chars ----------

def collect_manifests(
    entries: list[bundle.BundleEntry],
) -> tuple[set[str], dict[str, set[str]]]:
    bg_ids: set[str] = set()
    char_exprs: dict[str, set[str]] = {}
    for _chapter, _segs, timeline in entries:
        m = placeholders.scan(timeline)
        bg_ids |= m.bgs
        for cid, expr in m.chars:
            char_exprs.setdefault(cid, set()).add(expr)
    return bg_ids, char_exprs


def classify_chars(
    cast: dict, char_exprs: dict[str, set[str]]
) -> tuple[list[str], list[str]]:
    roster = cast.get("cast", {})
    major = sorted(
        cid for cid in char_exprs
        if roster.get(cid, {}).get("appearance_count", 0)
        >= config.MAJOR_CHAR_MIN_APPEARANCES
    )
    minor = sorted(set(char_exprs) - set(major))
    return major, minor


def collect_excerpt(entries: list[bundle.BundleEntry], max_segments: int = 10) -> str:
    lines: list[str] = []
    for _chapter, segs, _timeline in entries:
        for seg in segs:
            lines.append(seg.text)
            if len(lines) >= max_segments:
                return "\n".join(lines)
    return "\n".join(lines)


# ---------- descriptions ----------

def propose_missing_descriptions(
    out_dir: Path,
    entries: list[bundle.BundleEntry],
    cast: dict,
    book_title: str,
    major_chars: list[str],
    bg_ids: set[str],
) -> dict:
    """Propose char/bg descriptions for the ones that aren't yet saved.

    Returns ``{"characters": {...}, "display_names": {...}, "backgrounds": {...}}``
    filled only for entries that were missing. Existing descriptions are
    folded into the result so callers get a complete picture.
    """
    roster = cast.get("cast", {})
    stored_bgs = bg_mod.load(out_dir)

    chars_needing = [
        cid for cid in major_chars
        if not roster.get(cid, {}).get("visual_description")
    ]
    bgs_needing = sorted(
        bid for bid in bg_ids
        if not bg_mod.get_visual_description(stored_bgs, bid)
    )

    existing_chars = {
        cid: roster.get(cid, {}).get("visual_description", "")
        for cid in major_chars
        if roster.get(cid, {}).get("visual_description")
    }
    existing_names = {
        cid: roster.get(cid, {}).get("display_name", cid)
        for cid in major_chars
    }
    existing_bgs = {
        bid: bg_mod.get_visual_description(stored_bgs, bid)
        for bid in bg_ids
        if bg_mod.get_visual_description(stored_bgs, bid)
    }

    proposed = {"characters": {}, "display_names": {}, "backgrounds": {}}
    if chars_needing or bgs_needing:
        excerpt = collect_excerpt(entries)
        proposed = visual_descriptions.propose(
            book_title=book_title,
            char_ids=chars_needing,
            bg_ids=bgs_needing,
            excerpt=excerpt,
        )

    return {
        "characters": {**existing_chars, **proposed.get("characters", {})},
        "display_names": {**existing_names, **proposed.get("display_names", {})},
        "backgrounds": {**existing_bgs, **proposed.get("backgrounds", {})},
        "chars_needing": chars_needing,
        "bgs_needing": bgs_needing,
    }


def save_descriptions(
    out_dir: Path,
    cast: dict,
    char_descs: dict[str, str],
    bg_descs: dict[str, str],
    display_names: dict[str, str] | None = None,
) -> dict:
    """Persist edited descriptions into cast.json and backgrounds.json. Returns updated cast."""
    for cid, desc in char_descs.items():
        cast = cast_mod.set_visual_description(cast, cid, desc)
    if display_names:
        for cid, name in display_names.items():
            if name:
                cast = cast_mod.set_display_name(cast, cid, name)

    stored_bgs = bg_mod.load(out_dir)
    for bid, desc in bg_descs.items():
        stored_bgs = bg_mod.set_visual_description(stored_bgs, bid, desc)
    out_dir.mkdir(parents=True, exist_ok=True)
    bg_mod.save(out_dir, stored_bgs)
    cast_mod.save(out_dir, cast)
    return cast


# ---------- per-asset generation (idempotent unless force=True) ----------

def _nano_adapter(*, defer_matte: bool = False, on_matte_done=None):
    from pipeline.assets.nanobanana import NanoBananaAdapter
    return NanoBananaAdapter(defer_matte=defer_matte, on_matte_done=on_matte_done)


def baseline_path(out_dir: Path) -> Path:
    return out_dir / config.BUNDLE_CHAR_DIR / "_baseline" / "neutral.png"


def char_path(out_dir: Path, cid: str, expr: str) -> Path:
    return out_dir / config.BUNDLE_CHAR_DIR / cid / f"{expr}.png"


def bg_path(out_dir: Path, bg_id: str) -> Path:
    return out_dir / config.BUNDLE_BG_DIR / f"{bg_id}.png"


def gen_baseline(out_dir: Path, adapter=None, *, force: bool = False) -> Path:
    path = baseline_path(out_dir)
    if path.exists() and not force:
        return path
    if force and path.exists():
        path.unlink()
    (adapter or _nano_adapter()).generate_baseline(path)
    return path


def gen_basic_char(
    out_dir: Path, cid: str, desc: str, *,
    adapter=None, force: bool = False,
) -> Path:
    out = char_path(out_dir, cid, "neutral")
    if out.exists() and not force:
        return out
    if force and out.exists():
        out.unlink()
    bpath = baseline_path(out_dir)
    (adapter or _nano_adapter()).generate_char(
        cid, "neutral", desc, out, reference=bpath, style_ref_only=True,
    )
    return out


def gen_expression(
    out_dir: Path, cid: str, expr: str, desc: str, *,
    adapter=None, force: bool = False,
) -> Path:
    out = char_path(out_dir, cid, expr)
    if out.exists() and not force:
        return out
    if force and out.exists():
        out.unlink()
    neutral_ref = char_path(out_dir, cid, "neutral")
    (adapter or _nano_adapter()).generate_char(
        cid, expr, desc, out, reference=neutral_ref,
    )
    return out


def gen_bg(
    out_dir: Path, bg_id: str, desc: str, *,
    adapter=None, force: bool = False,
) -> Path:
    out = bg_path(out_dir, bg_id)
    if out.exists() and not force:
        return out
    if force and out.exists():
        out.unlink()
    (adapter or _nano_adapter()).generate_bg(bg_id, desc, out)
    return out


# ---------- bundle write ----------

def write_bundle(
    out_dir: Path,
    entries: list[bundle.BundleEntry],
    cast: dict,
    *,
    book_title: str,
    image_adapter: ImageAdapter | None = None,
    char_descs: dict[str, str] | None = None,
    bg_descs: dict[str, str] | None = None,
) -> None:
    bundle.write_bundle_multi(
        out_dir, entries, cast,
        book_title=book_title,
        image_adapter=image_adapter or PlaceholderAdapter(),
        visual_descriptions={
            "characters": char_descs or {},
            "backgrounds": bg_descs or {},
        },
    )


# ---------- godot launcher ----------

def launch_godot(bundle_dir: Path) -> subprocess.Popen:
    exe = os.environ.get("GODOT_EXE") or getattr(config, "GODOT_EXE", "")
    if not exe:
        raise RuntimeError(
            "Godot executable not configured. Set GODOT_EXE env var to the path "
            "of the Godot console exe (e.g. Godot_v4.6.1-stable_win64_console.exe)."
        )
    project_dir = (Path(__file__).resolve().parent.parent / "godot_player").resolve()
    return subprocess.Popen(
        [exe, "--path", str(project_dir), "--", "--bundle", str(Path(bundle_dir).resolve())],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
