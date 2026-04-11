import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import asset_catalog, bundle, cache, cast as cast_mod, chapters, config, segmenter
from pipeline.build_log import BuildLog
from pipeline.llm import gemini_client
from pipeline.segmenter import Segment


class AssetIdError(Exception):
    pass


def _validate_asset_ids(llm_timeline: dict) -> None:
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


def _validate(timeline: dict, schema_path: Path, label: str) -> None:
    validator = Draft202012Validator(_load_schema(schema_path))
    errors = sorted(validator.iter_errors(timeline), key=lambda e: list(e.path))
    if errors:
        msgs = [f"- {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
        raise SystemExit(f"{label} failed schema validation:\n" + "\n".join(msgs))


def _expand_says(llm_timeline: dict, segments: list[Segment]) -> dict:
    """Resolve compact `say.seg` references into runtime `say.text` + `say.voice_clip`.
    Also enforces that every segment is referenced exactly once, in order."""
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


def _cmd_build(args: argparse.Namespace) -> None:
    text = Path(args.input).read_text(encoding="utf-8")
    chs = chapters.split(text)
    if not chs:
        raise SystemExit("Chapter splitter returned no chapters")
    print(f"[pipeline] split into {len(chs)} chapter(s)")

    out_dir = Path(args.out)
    cast = {"cast": {}}
    log = BuildLog(out_dir)

    prompt_template = config.PROMPT_PATH.read_text(encoding="utf-8")
    llm_schema_text = config.LLM_SCHEMA_PATH.read_text(encoding="utf-8")

    entries: list[bundle.BundleEntry] = []
    total_segs = 0

    for chapter in chs:
        segs = segmenter.segment(chapter.id, chapter.body)
        if not segs:
            raise SystemExit(f"No segments produced from chapter {chapter.id!r}")
        print(f"[pipeline] {chapter.id}: {len(segs)} segments")
        total_segs += len(segs)
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
        if not args.no_cache:
            llm_timeline = cache.get("gemini_timeline", cache_key)
            if llm_timeline is not None:
                print(f"[pipeline] {chapter.id}: cache hit")

        if llm_timeline is None:
            for attempt in range(2):
                print(f"[pipeline] {chapter.id}: calling Gemini model {config.GEMINI_MODEL}"
                      + (" (retry)" if attempt else ""))
                t0 = time.time()
                llm_timeline, rendered_prompt = gemini_client.generate_timeline(
                    chapter.id, chapter.title, chapter.body, segs, known_cast_text=known_cast_text
                )
                elapsed = time.time() - t0
                log.gemini_prompt(chapter.id, rendered_prompt)
                log.gemini_response(chapter.id, llm_timeline, elapsed)
                _validate(llm_timeline, config.LLM_SCHEMA_PATH, f"LLM output for {chapter.id}")
                try:
                    _validate_asset_ids(llm_timeline)
                    break
                except AssetIdError as e:
                    log.validation_error(chapter.id, "asset_ids", str(e))
                    if attempt == 1:
                        raise SystemExit(f"Asset-ID validation failed after retry: {e}")
                    print(f"[pipeline] {chapter.id}: {e} — retrying")
            cache.put("gemini_timeline", cache_key, llm_timeline)
        else:
            _validate(llm_timeline, config.LLM_SCHEMA_PATH, f"cached LLM output for {chapter.id}")
            _validate_asset_ids(llm_timeline)

        timeline = _expand_says(llm_timeline, segs)
        _validate(timeline, config.SCHEMA_PATH, f"Runtime timeline for {chapter.id}")

        entries.append((chapter, segs, timeline))
        cast = cast_mod.update_from_timeline(cast, timeline, chapter.id)
        log.cast_snapshot(chapter.id, cast)

    # Derive book title from the first chapter's first non-heading line for
    # multi-chapter inputs; single-chapter inputs keep the chapter title.
    book_title = chs[0].title if len(chs) == 1 else Path(args.input).stem

    bundle.write_bundle_multi(out_dir, entries, cast, book_title=book_title)
    log.summary(len(chs), total_segs)
    print(f"[pipeline] bundle written to {out_dir.resolve()}")
    print(f"[pipeline] build logs at {(out_dir / 'logs').resolve()}")


def _cmd_validate(args: argparse.Namespace) -> None:
    timeline = json.loads(Path(args.path).read_text(encoding="utf-8"))
    _validate(timeline, config.SCHEMA_PATH, "Timeline")
    print("OK")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build a VN bundle from a .txt input")
    b.add_argument("input", help="Path to source .txt")
    b.add_argument("-o", "--out", required=True, help="Output bundle directory")
    b.add_argument("--no-cache", action="store_true", help="Bypass the content-hash cache")
    b.set_defaults(func=_cmd_build)

    v = sub.add_parser("validate", help="Validate a chapter timeline JSON against the runtime schema")
    v.add_argument("path")
    v.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
