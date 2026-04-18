"""Persistent background visual descriptions.

Mirrors cast.py for backgrounds. Loaded at nanobanana-pipeline start, fed into
the adapter when generating BGs, and saved back to the bundle so descriptions
can be hand-edited and reused across rebuilds without re-prompting the LLM.
"""
import json
from pathlib import Path

from pipeline import config


def _path(bundle_dir: Path) -> Path:
    return bundle_dir / config.BUNDLE_BACKGROUNDS_JSON


def load(bundle_dir: Path) -> dict:
    path = _path(bundle_dir)
    if not path.exists():
        return {"backgrounds": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("backgrounds"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"backgrounds": {}}


def save(bundle_dir: Path, backgrounds: dict) -> None:
    path = _path(bundle_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(backgrounds, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_visual_description(backgrounds: dict, bg_id: str, description: str) -> dict:
    roster = {bid: dict(meta) for bid, meta in backgrounds.get("backgrounds", {}).items()}
    roster.setdefault(bg_id, {})["visual_description"] = description
    return {**backgrounds, "backgrounds": roster}


def get_visual_description(backgrounds: dict, bg_id: str) -> str:
    return backgrounds.get("backgrounds", {}).get(bg_id, {}).get("visual_description", "")


def get_visual_style(backgrounds: dict) -> str:
    return backgrounds.get("visual_style", "")


def set_visual_style(backgrounds: dict, style: str) -> dict:
    return {**backgrounds, "visual_style": style}


def render_known_backgrounds(backgrounds: dict) -> str:
    """Bullet list of `- bg_id: description` for Pass B cross-chapter reuse."""
    roster = backgrounds.get("backgrounds", {}) or {}
    lines = []
    for bid in sorted(roster):
        desc = (roster.get(bid, {}) or {}).get("visual_description", "").strip()
        if desc:
            lines.append(f"- {bid}: {desc}")
        else:
            lines.append(f"- {bid}")
    return "\n".join(lines) if lines else "None."
