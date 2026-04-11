import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from pipeline import bundle, chapters, config, segmenter
from pipeline.llm import gemini_client
from pipeline.segmenter import Segment


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


def _cmd_build(args: argparse.Namespace) -> None:
    text = Path(args.input).read_text(encoding="utf-8")
    chs = chapters.split(text)
    if len(chs) != 1:
        raise SystemExit("M1 only supports single-chapter input")
    chapter = chs[0]

    segs = segmenter.segment(chapter.id, chapter.body)
    if not segs:
        raise SystemExit("No segments produced from input text")
    print(f"[pipeline] {len(segs)} segments from chapter {chapter.id!r}")

    print(f"[pipeline] calling Gemini model {config.GEMINI_MODEL}")
    llm_timeline = gemini_client.generate_timeline(chapter.id, chapter.title, chapter.body, segs)
    _validate(llm_timeline, config.LLM_SCHEMA_PATH, "LLM output")
    print(f"[pipeline] LLM output valid, {len(llm_timeline['commands'])} commands")

    timeline = _expand_says(llm_timeline, segs)
    _validate(timeline, config.SCHEMA_PATH, "Runtime timeline")
    print(f"[pipeline] runtime timeline valid")

    out_dir = Path(args.out)
    bundle.write_bundle(out_dir, chapter, segs, timeline)
    print(f"[pipeline] bundle written to {out_dir.resolve()}")


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
    b.set_defaults(func=_cmd_build)

    v = sub.add_parser("validate", help="Validate a chapter timeline JSON against the runtime schema")
    v.add_argument("path")
    v.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
