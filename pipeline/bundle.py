import json
from pathlib import Path

from pipeline import config
from pipeline.assets import placeholders
from pipeline.chapters import Chapter
from pipeline.segmenter import Segment
from pipeline.tts.silent import SilentTTS, silent_ogg


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_bundle(
    out_dir: Path,
    chapter: Chapter,
    segments: list[Segment],
    timeline: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # book.json
    book = {
        "title": chapter.title,
        "chapters": [
            {
                "id": chapter.id,
                "title": timeline.get("title", chapter.title),
                "file": f"{config.BUNDLE_CHAPTERS_DIR}/{chapter.id}.json",
            }
        ],
    }
    _write_json(out_dir / config.BUNDLE_BOOK_JSON, book)

    # chapter timeline
    _write_json(out_dir / config.BUNDLE_CHAPTERS_DIR / f"{chapter.id}.json", timeline)

    # asset placeholders (post-hoc from timeline)
    manifest = placeholders.scan(timeline)
    for bg_id in sorted(manifest.bgs):
        placeholders.generate_bg(bg_id, out_dir / config.BUNDLE_BG_DIR / f"{bg_id}.png")
    for char_id, expr in sorted(manifest.chars):
        placeholders.generate_char(
            char_id, expr, out_dir / config.BUNDLE_CHAR_DIR / char_id / f"{expr}.png"
        )
    for bgm_id in sorted(manifest.bgms):
        p = out_dir / config.BUNDLE_BGM_DIR / f"{bgm_id}.ogg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(silent_ogg(5000))
    for se_id in sorted(manifest.ses):
        p = out_dir / config.BUNDLE_SE_DIR / f"{se_id}.ogg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(silent_ogg(800))

    # voice clips: one per segment, sized by text length
    tts = SilentTTS()
    voice_dir = out_dir / config.BUNDLE_VOICE_DIR
    voice_dir.mkdir(parents=True, exist_ok=True)
    for seg in segments:
        (voice_dir / f"{seg.seg_id}.ogg").write_bytes(tts.synth(seg.text))
