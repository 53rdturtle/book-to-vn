import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG_SIZE = (1920, 1080)
CHAR_SIZE = (600, 1200)


@dataclass
class AssetManifest:
    bgs: set[str] = field(default_factory=set)
    chars: set[tuple[str, str]] = field(default_factory=set)
    bgms: set[str] = field(default_factory=set)
    ses: set[str] = field(default_factory=set)


def scan(timeline: dict) -> AssetManifest:
    m = AssetManifest()
    for cmd in timeline.get("commands", []):
        t = cmd.get("type")
        if t == "bg":
            m.bgs.add(cmd["id"])
        elif t == "bgm_play":
            m.bgms.add(cmd["id"])
        elif t == "se":
            m.ses.add(cmd["id"])
        elif t == "char_show":
            m.chars.add((cmd["id"], cmd["expr"]))
    return m


def _color_from(key: str, sat: int = 140, base: int = 60) -> tuple[int, int, int]:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return (base + h[0] % sat, base + h[1] % sat, base + h[2] % sat)


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", 48)
    except Exception:
        return ImageFont.load_default()


def generate_bg(bg_id: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", BG_SIZE, _color_from(bg_id))
    draw = ImageDraw.Draw(img)
    draw.text((40, BG_SIZE[1] - 100), bg_id, fill=(255, 255, 255), font=_font())
    img.save(out_path, format="PNG")


def generate_char(char_id: str, expr: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", CHAR_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = _color_from(char_id, sat=160, base=40) + (255,)
    # Body (trapezoid approximated as polygon)
    body = [
        (120, CHAR_SIZE[1]),
        (CHAR_SIZE[0] - 120, CHAR_SIZE[1]),
        (CHAR_SIZE[0] - 180, 420),
        (180, 420),
    ]
    draw.polygon(body, fill=color)
    # Head
    draw.ellipse((170, 80, CHAR_SIZE[0] - 170, 460), fill=color)
    # Label
    label = f"{char_id}\n({expr})"
    draw.text((40, 40), label, fill=(255, 255, 255, 255), font=_font())
    img.save(out_path, format="PNG")
