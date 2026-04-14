"""Persistent cross-chapter character roster.

The cast is loaded at the start of a build (may be empty), passed into each
chapter's Gemini prompt as {KNOWN_CAST}, and updated after each chapter's
timeline validates. It keeps character IDs stable across chapters so the same
"alice" in ch01 is referenced by the same ID in ch05.
"""
import json
from pathlib import Path

from pipeline import config

_NARRATOR = "narrator"


def _cast_path(bundle_dir: Path) -> Path:
    return bundle_dir / config.BUNDLE_CAST_JSON


def load(bundle_dir: Path) -> dict:
    path = _cast_path(bundle_dir)
    if not path.exists():
        return {"cast": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("cast"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"cast": {}}


def save(bundle_dir: Path, cast: dict) -> None:
    path = _cast_path(bundle_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cast, ensure_ascii=False, indent=2), encoding="utf-8")


def update_from_timeline(cast: dict, timeline: dict, chapter_id: str) -> dict:
    """Return a new cast dict with any newly-seen character IDs registered
    and per-character appearance counts incremented."""
    roster: dict = {cid: dict(meta) for cid, meta in cast.get("cast", {}).items()}

    def _bump(cid: str) -> None:
        entry = roster.setdefault(
            cid, {"display_name": cid, "first_chapter": chapter_id, "appearance_count": 0}
        )
        entry.setdefault("appearance_count", 0)
        entry["appearance_count"] += 1

    for cmd in timeline.get("commands", []):
        t = cmd.get("type")
        if t == "char_show":
            cid = cmd.get("id")
            if cid:
                _bump(cid)
        elif t == "say":
            speaker = cmd.get("speaker")
            if speaker and speaker != _NARRATOR:
                _bump(speaker)
    return {"cast": roster}


def set_display_name(cast: dict, char_id: str, display_name: str) -> dict:
    """Return a new cast dict with display_name set for char_id."""
    roster = {cid: dict(meta) for cid, meta in cast.get("cast", {}).items()}
    if char_id in roster and display_name:
        roster[char_id]["display_name"] = display_name
    return {"cast": roster}


def set_visual_description(cast: dict, char_id: str, description: str) -> dict:
    """Return a new cast dict with visual_description set for char_id."""
    roster = {cid: dict(meta) for cid, meta in cast.get("cast", {}).items()}
    if char_id in roster:
        roster[char_id]["visual_description"] = description
    return {"cast": roster}


def set_silhouette_type(cast: dict, char_id: str, silhouette_type: str) -> dict:
    """Return a new cast dict with silhouette_type set for char_id."""
    roster = {cid: dict(meta) for cid, meta in cast.get("cast", {}).items()}
    if char_id in roster:
        roster[char_id]["silhouette_type"] = silhouette_type
    return {"cast": roster}


def render_known_cast(cast: dict) -> str:
    """Format cast as a bullet list for injection into the timeline prompt."""
    roster = cast.get("cast", {})
    if not roster:
        return "None — introduce characters as needed."
    lines = []
    for cid, meta in sorted(roster.items(), key=lambda kv: kv[1].get("first_chapter", "")):
        name = meta.get("display_name", cid)
        first = meta.get("first_chapter", "?")
        lines.append(f"- {cid}: {name} (introduced in {first})")
    return "\n".join(lines)
