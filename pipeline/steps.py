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
    minor_chars: list[str] | None = None,
) -> dict:
    """Propose char/bg descriptions for the ones that aren't yet saved.

    Returns ``{"characters": {...}, "display_names": {...}, "backgrounds": {...},
    "minor_silhouettes": {...}}`` filled only for entries that were missing.
    Existing descriptions are folded into the result so callers get a complete picture.
    """
    roster = cast.get("cast", {})
    stored_cast = cast_mod.load(out_dir).get("cast", {})
    stored_bgs = bg_mod.load(out_dir)

    print(f"[descriptions] out_dir={out_dir}")
    print(f"[descriptions] cast.json exists={ (out_dir / config.BUNDLE_CAST_JSON).exists() }, "
          f"prior chars with visual_description="
          f"{sorted(cid for cid, m in stored_cast.items() if m.get('visual_description'))}")
    print(f"[descriptions] backgrounds.json exists={ (out_dir / config.BUNDLE_BACKGROUNDS_JSON).exists() }, "
          f"prior bgs with visual_description="
          f"{sorted(bid for bid, m in stored_bgs.get('backgrounds', {}).items() if m.get('visual_description'))}")
    print(f"[descriptions] in-memory cast chars with visual_description="
          f"{sorted(cid for cid, m in roster.items() if m.get('visual_description'))}")
    print(f"[descriptions] major_chars={major_chars}, bg_ids={sorted(bg_ids)}")

    def _has_char_desc(cid: str) -> bool:
        return bool(
            roster.get(cid, {}).get("visual_description")
            or stored_cast.get(cid, {}).get("visual_description")
        )

    def _stored_display_name(cid: str) -> str:
        name = (
            roster.get(cid, {}).get("display_name")
            or stored_cast.get(cid, {}).get("display_name")
            or ""
        )
        return name if name and name != cid else ""

    chars_needing = [cid for cid in major_chars if not _has_char_desc(cid)]
    bgs_needing = sorted(
        bid for bid in bg_ids
        if not bg_mod.get_visual_description(stored_bgs, bid)
    )
    minors_needing = [
        cid for cid in (minor_chars or [])
        if not (roster.get(cid, {}).get("silhouette_type")
                or stored_cast.get(cid, {}).get("silhouette_type"))
    ]
    all_chars = list(dict.fromkeys(major_chars + list(minor_chars or [])))
    names_needing = [cid for cid in all_chars if not _stored_display_name(cid)]
    # Chars missing only a name (desc/silhouette already present) — include them
    # in the LLM call so it can propose a display_name, but discard any extra
    # desc/silhouette it returns for them.
    name_only_major = [cid for cid in names_needing
                       if cid in major_chars and cid not in chars_needing]
    name_only_minor = [cid for cid in names_needing
                       if cid in (minor_chars or []) and cid not in minors_needing]
    char_ids_for_llm = sorted(set(chars_needing) | set(name_only_major))
    minor_ids_for_llm = sorted(set(minors_needing) | set(name_only_minor))

    print(f"[descriptions] chars_needing (will query Gemini)={chars_needing}")
    print(f"[descriptions] bgs_needing (will query Gemini)={bgs_needing}")
    print(f"[descriptions] minors_needing silhouette classification={minors_needing}")
    print(f"[descriptions] names_needing={names_needing} "
          f"(name-only major={name_only_major}, minor={name_only_minor})")

    existing_chars: dict[str, str] = {}
    for cid in major_chars:
        desc = (
            roster.get(cid, {}).get("visual_description")
            or stored_cast.get(cid, {}).get("visual_description")
        )
        if desc:
            existing_chars[cid] = desc
    existing_names = {
        cid: _stored_display_name(cid) or cid
        for cid in all_chars
    }
    existing_bgs = {
        bid: bg_mod.get_visual_description(stored_bgs, bid)
        for bid in bg_ids
        if bg_mod.get_visual_description(stored_bgs, bid)
    }

    existing_style = bg_mod.get_visual_style(stored_bgs)

    proposed = {"visual_style": "", "characters": {}, "display_names": {},
                "minor_silhouettes": {}, "backgrounds": {}}
    if chars_needing or bgs_needing or minors_needing or names_needing:
        print(f"[descriptions] calling Gemini for {len(chars_needing)} chars + "
              f"{len(bgs_needing)} bgs + {len(minors_needing)} minor classifications + "
              f"{len(names_needing)} display names"
              + ("" if existing_style else " (also proposing visual_style)"))
        excerpt = collect_excerpt(entries)
        proposed = visual_descriptions.propose(
            book_title=book_title,
            char_ids=char_ids_for_llm,
            bg_ids=bgs_needing,
            excerpt=excerpt,
            minor_char_ids=minor_ids_for_llm,
        )
    else:
        print(f"[descriptions] nothing needed — skipping Gemini call entirely"
              + (" (visual_style stays empty; use 'Propose' button in UI to fill it)"
                 if not existing_style else ""))
    print(f"[descriptions] reused existing chars={sorted(existing_chars.keys())}, "
          f"reused existing bgs={sorted(existing_bgs.keys())}")

    visual_style = existing_style or proposed.get("visual_style", "")

    existing_silhouettes = {
        cid: (roster.get(cid, {}).get("silhouette_type")
              or stored_cast.get(cid, {}).get("silhouette_type"))
        for cid in (minor_chars or [])
        if (roster.get(cid, {}).get("silhouette_type")
            or stored_cast.get(cid, {}).get("silhouette_type"))
    }

    # Drop any hallucinated IDs: Gemini may return entries for chars/bgs/minors
    # we didn't ask about (e.g. when called only to propose visual_style).
    raw_proposed_chars = proposed.get("characters", {})
    raw_proposed_bgs = proposed.get("backgrounds", {})
    raw_proposed_names = proposed.get("display_names", {})
    raw_proposed_silhouettes = proposed.get("minor_silhouettes", {})

    dropped_chars = sorted(set(raw_proposed_chars) - set(chars_needing))
    dropped_bgs = sorted(set(raw_proposed_bgs) - set(bgs_needing))
    dropped_silhouettes = sorted(set(raw_proposed_silhouettes) - set(minors_needing))
    dropped_names = sorted(set(raw_proposed_names) - set(names_needing))
    if dropped_chars:
        print(f"[descriptions] dropping hallucinated chars from LLM: {dropped_chars}")
    if dropped_bgs:
        print(f"[descriptions] dropping hallucinated bgs from LLM: {dropped_bgs}")
    if dropped_silhouettes:
        print(f"[descriptions] dropping hallucinated minor silhouettes from LLM: {dropped_silhouettes}")
    if dropped_names:
        print(f"[descriptions] dropping hallucinated display_names from LLM: {dropped_names}")

    filtered_chars = {k: v for k, v in raw_proposed_chars.items() if k in chars_needing}
    filtered_bgs = {k: v for k, v in raw_proposed_bgs.items() if k in bgs_needing}
    filtered_names = {k: v for k, v in raw_proposed_names.items() if k in names_needing and v}
    filtered_silhouettes = {k: v for k, v in raw_proposed_silhouettes.items() if k in minors_needing}

    result = {
        "visual_style": visual_style,
        "characters": {**existing_chars, **filtered_chars},
        "display_names": {**existing_names, **filtered_names},
        "minor_silhouettes": {**existing_silhouettes, **filtered_silhouettes},
        "backgrounds": {**existing_bgs, **filtered_bgs},
        "chars_needing": chars_needing,
        "bgs_needing": bgs_needing,
    }
    print(f"[descriptions] FINAL visual_style={result['visual_style']!r} "
          f"(existing_style={existing_style!r})")
    for cid, desc in result["characters"].items():
        src = "existing" if cid in existing_chars and existing_chars[cid] == desc else "proposed"
        print(f"[descriptions] FINAL char {cid} [{src}]: {desc[:80]!r}")
    for bid, desc in result["backgrounds"].items():
        src = "existing" if bid in existing_bgs and existing_bgs[bid] == desc else "proposed"
        print(f"[descriptions] FINAL bg {bid} [{src}]: {desc[:80]!r}")
    return result


def save_descriptions(
    out_dir: Path,
    cast: dict,
    char_descs: dict[str, str],
    bg_descs: dict[str, str],
    display_names: dict[str, str] | None = None,
    visual_style: str = "",
    minor_silhouettes: dict[str, str] | None = None,
) -> dict:
    """Persist edited descriptions into cast.json and backgrounds.json. Returns updated cast."""
    for cid, desc in char_descs.items():
        cast = cast_mod.set_visual_description(cast, cid, desc)
    if display_names:
        for cid, name in display_names.items():
            if name:
                cast = cast_mod.set_display_name(cast, cid, name)
    if minor_silhouettes:
        for cid, stype in minor_silhouettes.items():
            cast = cast_mod.set_silhouette_type(cast, cid, stype)

    stored_bgs = bg_mod.load(out_dir)
    stored_bgs = bg_mod.set_visual_style(stored_bgs, visual_style)
    for bid, desc in bg_descs.items():
        stored_bgs = bg_mod.set_visual_description(stored_bgs, bid, desc)
    out_dir.mkdir(parents=True, exist_ok=True)
    bg_mod.save(out_dir, stored_bgs)
    cast_mod.save(out_dir, cast)
    return cast


# ---------- per-asset generation (idempotent unless force=True) ----------

def _nano_adapter(*, defer_matte: bool = False, on_matte_done=None, style: str | None = None):
    from pipeline.assets.nanobanana import NanoBananaAdapter
    return NanoBananaAdapter(style=style, defer_matte=defer_matte, on_matte_done=on_matte_done)


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
        image_adapter=image_adapter or PlaceholderAdapter(cast=cast),
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
