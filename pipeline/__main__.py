import argparse
import json
import sys
from pathlib import Path

from pipeline import api_log, backgrounds as bg_mod, bundle, cache, cast as cast_mod, chapters, config
from pipeline.assets import placeholders
from pipeline.assets.adapter import ImageAdapter
from pipeline.assets.placeholders import PlaceholderAdapter
from pipeline.build_log import BuildLog
from pipeline.llm import visual_descriptions
from pipeline.segmenter import Segment
from pipeline import steps

# Re-exports: some tests (and possibly external tooling) import these from
# pipeline.__main__. Keep them as thin delegates so the refactor doesn't break
# import paths.
AssetIdError = steps.AssetIdError


def _validate_asset_ids(llm_timeline: dict) -> None:
    steps.validate_asset_ids(llm_timeline)


def _validate(timeline: dict, schema_path: Path, label: str) -> None:
    try:
        steps.validate_schema(timeline, schema_path, label)
    except SystemExit:
        raise


def _expand_says(llm_timeline: dict, segments: list[Segment]) -> dict:
    return steps.expand_says(llm_timeline, segments)


def _confirm(message: str, *, skip: bool = False) -> None:
    if skip:
        return
    print(f"\n{message}")
    while True:
        ans = input("Continue? [y/N]: ").strip().lower()
        if ans == "y":
            return
        if ans in ("n", "", "q"):
            raise SystemExit("Aborted by user.")
        print("Please answer 'y' to continue or 'n' to abort.")


def _collect_excerpt(entries: list[bundle.BundleEntry], max_segments: int = 10) -> str:
    lines: list[str] = []
    for _chapter, segs, _timeline in entries:
        for seg in segs:
            lines.append(seg.text)
            if len(lines) >= max_segments:
                return "\n".join(lines)
    return "\n".join(lines)


def _collect_manifests(entries: list[bundle.BundleEntry]) -> tuple[set[str], dict[str, set[str]]]:
    bg_ids: set[str] = set()
    char_exprs: dict[str, set[str]] = {}
    for _chapter, _segs, timeline in entries:
        m = placeholders.scan(timeline)
        bg_ids |= m.bgs
        for cid, expr in m.chars:
            char_exprs.setdefault(cid, set()).add(expr)
    return bg_ids, char_exprs


def _editable_input(label: str, default: str, *, skip: bool = False) -> str:
    print(f"\n{label}")
    print(f"  Proposed: {default}")
    if skip:
        return default
    ans = input("  Accept (enter) or type replacement: ").strip()
    return ans or default


def _run_nanobanana_pipeline(
    out_dir: Path,
    entries: list[bundle.BundleEntry],
    cast: dict,
    book_title: str,
    *,
    skip_confirm: bool = False,
) -> tuple[ImageAdapter, dict, dict]:
    """Execute 3-phase char pipeline + BG proposal. Returns (adapter, visual_descriptions, cast).

    Only major characters go through NanoBanana; minor characters are left for
    the PlaceholderAdapter path inside write_bundle_multi.
    """
    from pipeline.assets.nanobanana import NanoBananaAdapter

    bg_ids, char_exprs = _collect_manifests(entries)
    roster = cast.get("cast", {})

    major_chars = sorted(
        cid for cid in char_exprs
        if roster.get(cid, {}).get("appearance_count", 0) >= config.MAJOR_CHAR_MIN_APPEARANCES
    )
    minor_chars = sorted(set(char_exprs) - set(major_chars))

    print(f"[nanobanana] major characters (>= {config.MAJOR_CHAR_MIN_APPEARANCES} appearances): "
          f"{major_chars or '(none)'}")
    if minor_chars:
        print(f"[nanobanana] minor characters (using placeholder silhouettes): {minor_chars}")
    print(f"[nanobanana] backgrounds to generate: {sorted(bg_ids) or '(none)'}")

    stored_bgs = bg_mod.load(out_dir)

    # --- Visual descriptions (LLM proposes, user confirms/edits) ---
    chars_needing_desc = [
        cid for cid in major_chars
        if not roster.get(cid, {}).get("visual_description")
    ]
    bgs_needing_desc = sorted(
        bid for bid in bg_ids
        if not bg_mod.get_visual_description(stored_bgs, bid)
    )

    char_descs: dict[str, str] = {
        cid: roster.get(cid, {}).get("visual_description", "")
        for cid in major_chars
        if roster.get(cid, {}).get("visual_description")
    }
    bg_descs: dict[str, str] = {
        bid: bg_mod.get_visual_description(stored_bgs, bid)
        for bid in bg_ids
        if bg_mod.get_visual_description(stored_bgs, bid)
    }

    visual_style = bg_mod.get_visual_style(stored_bgs)

    minors_needing_sil = [
        cid for cid in minor_chars
        if not roster.get(cid, {}).get("silhouette_type")
    ]

    def _has_display_name(cid: str) -> bool:
        name = roster.get(cid, {}).get("display_name", "")
        return bool(name) and name != cid

    all_chars = list(dict.fromkeys(major_chars + minor_chars))
    names_needing = [cid for cid in all_chars if not _has_display_name(cid)]
    name_only_major = [cid for cid in names_needing
                       if cid in major_chars and cid not in chars_needing_desc]
    name_only_minor = [cid for cid in names_needing
                       if cid in minor_chars and cid not in minors_needing_sil]

    if chars_needing_desc or bgs_needing_desc or minors_needing_sil or names_needing:
        excerpt = _collect_excerpt(entries)
        print(f"\n[nanobanana] proposing descriptions via {config.GEMINI_MODEL}...")
        proposed = visual_descriptions.propose(
            book_title=book_title,
            char_ids=sorted(set(chars_needing_desc) | set(name_only_major)),
            bg_ids=bgs_needing_desc,
            excerpt=excerpt,
            minor_char_ids=sorted(set(minors_needing_sil) | set(name_only_minor)),
        )
        if not visual_style:
            visual_style = proposed.get("visual_style", "")
            if visual_style:
                visual_style = _editable_input("Visual style", visual_style, skip=skip_confirm)
                stored_bgs = bg_mod.set_visual_style(stored_bgs, visual_style)
        for cid in chars_needing_desc:
            desc = proposed["characters"].get(cid, "")
            char_descs[cid] = _editable_input(f"Character '{cid}'", desc, skip=skip_confirm)
            cast = cast_mod.set_visual_description(cast, cid, char_descs[cid])
        for cid in names_needing:
            name = proposed.get("display_names", {}).get(cid, "").strip()
            if name:
                cast = cast_mod.set_display_name(cast, cid, name)
        for bg_id in bgs_needing_desc:
            desc = proposed["backgrounds"].get(bg_id, "")
            bg_descs[bg_id] = _editable_input(f"Background '{bg_id}'", desc, skip=skip_confirm)
            stored_bgs = bg_mod.set_visual_description(stored_bgs, bg_id, bg_descs[bg_id])
        for cid, stype in proposed.get("minor_silhouettes", {}).items():
            if cid in minors_needing_sil:
                cast = cast_mod.set_silhouette_type(cast, cid, stype)
                print(f"[nanobanana] minor '{cid}' → silhouette: {stype}")

    bg_mod.save(out_dir, stored_bgs)

    adapter = NanoBananaAdapter(style=visual_style or None)

    # --- Phase A: baseline character ---
    baseline_path = out_dir / config.BUNDLE_CHAR_DIR / "_baseline" / "neutral.png"
    if not baseline_path.exists():
        print(f"\n[nanobanana] Phase A: generating baseline character art style...")
        adapter.generate_baseline(baseline_path)
    print(f"[nanobanana] baseline at: {baseline_path}")
    if major_chars:
        _confirm("Review the baseline image above. This anchors the art style for all characters.", skip=skip_confirm)

    # --- Phase B: basic (neutral) character refs ---
    if major_chars:
        print(f"\n[nanobanana] Phase B: generating basic character refs (neutral)...")
        for cid in major_chars:
            char_out = out_dir / config.BUNDLE_CHAR_DIR / cid / "neutral.png"
            if not char_out.exists():
                print(f"  - {cid}")
                adapter.generate_char(cid, "neutral", char_descs.get(cid, ""),
                                      char_out, reference=baseline_path,
                                      style_ref_only=True)
        _confirm("Review the basic character images above. Expression variants will branch from these.", skip=skip_confirm)

    # --- Phase C: expression variants ---
    for cid in major_chars:
        neutral_ref = out_dir / config.BUNDLE_CHAR_DIR / cid / "neutral.png"
        exprs = sorted(char_exprs.get(cid, set()) - {"neutral"})
        if not exprs:
            continue
        print(f"\n[nanobanana] Phase C: expressions for '{cid}': {exprs}")
        for expr in exprs:
            char_out = out_dir / config.BUNDLE_CHAR_DIR / cid / f"{expr}.png"
            if not char_out.exists():
                adapter.generate_char(cid, expr, char_descs.get(cid, ""),
                                      char_out, reference=neutral_ref)

    # --- Backgrounds ---
    if bg_ids:
        print(f"\n[nanobanana] generating {len(bg_ids)} background(s)...")
        for bg_id in sorted(bg_ids):
            bg_out = out_dir / config.BUNDLE_BG_DIR / f"{bg_id}.png"
            if not bg_out.exists():
                print(f"  - {bg_id}")
                adapter.generate_bg(bg_id, bg_descs.get(bg_id, ""), bg_out)

    # For the final bundle write, route minor chars through placeholder.
    # Major chars already have files on disk and bundle.py skips existing files.
    visual = {"characters": char_descs, "backgrounds": bg_descs}
    return PlaceholderAdapter(cast=cast), visual, cast


def _cmd_build(args: argparse.Namespace) -> None:
    api_log.reset()
    text = Path(args.input).read_text(encoding="utf-8")
    raw_entries = steps.split_and_segment(text)
    chs = [chapter for chapter, _segs in raw_entries]
    print(f"[pipeline] split into {len(chs)} chapter(s)")

    out_dir = Path(args.out)
    api_log.set_out_dir(out_dir)
    cache.set_dir(out_dir / "cache")
    # Reuse prior visual_description / display_name entries if a bundle already
    # exists at out_dir; update_from_timeline preserves these across the rebuild.
    prior_cast = cast_mod.load(out_dir)
    prior_roster = prior_cast.get("cast", {})
    initial_cast: dict = {"cast": {}}
    for cid, meta in prior_roster.items():
        carried = {}
        if meta.get("visual_description"):
            carried["visual_description"] = meta["visual_description"]
        if meta.get("display_name") and meta["display_name"] != cid:
            carried["display_name"] = meta["display_name"]
        if carried:
            initial_cast["cast"][cid] = carried
    log = BuildLog(out_dir)

    total_segs = sum(len(segs) for _ch, segs in raw_entries)
    for chapter, segs in raw_entries:
        print(f"[pipeline] {chapter.id}: {len(segs)} segments")

    entries, cast = steps.generate_timelines(
        raw_entries,
        log=log,
        no_cache=args.no_cache,
        initial_cast=initial_cast,
    )

    # Derive book title from the first chapter's first non-heading line for
    # multi-chapter inputs; single-chapter inputs keep the chapter title.
    book_title = chs[0].title if len(chs) == 1 else Path(args.input).stem

    image_adapter: ImageAdapter | None = None
    visual: dict | None = None
    if args.image_gen == "nanobanana":
        out_dir.mkdir(parents=True, exist_ok=True)
        image_adapter, visual, cast = _run_nanobanana_pipeline(
            out_dir, entries, cast, book_title,
            skip_confirm=args.skip_image_gen_confirmation,
        )

    bundle.write_bundle_multi(
        out_dir, entries, cast,
        book_title=book_title,
        image_adapter=image_adapter,
        visual_descriptions=visual,
    )
    log.summary(len(chs), total_segs)
    api_log.current.write(out_dir)
    print(f"[pipeline] bundle written to {out_dir.resolve()}")
    print(f"[pipeline] build logs at {(out_dir / 'logs').resolve()}")


def _cmd_editor(args: argparse.Namespace) -> None:
    from pipeline.editor.server import run as run_editor
    run_editor(port=args.port, open_browser=not args.no_open)


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
    b.add_argument(
        "--image-gen", choices=["placeholder", "nanobanana"], default="placeholder",
        help="Image generation backend (default: placeholder)",
    )
    b.add_argument(
        "--skip-image-gen-confirmation", action="store_true",
        help="Auto-accept all image generation confirmations and descriptions",
    )
    b.set_defaults(func=_cmd_build)

    v = sub.add_parser("validate", help="Validate a chapter timeline JSON against the runtime schema")
    v.add_argument("path")
    v.set_defaults(func=_cmd_validate)

    e = sub.add_parser("editor", help="Launch the assets editor web UI")
    e.add_argument("--port", type=int, default=None, help="Port (default 8765 or $EDITOR_PORT)")
    e.add_argument("--no-open", action="store_true", help="Don't auto-open a browser")
    e.set_defaults(func=_cmd_editor)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
