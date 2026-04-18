import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.assets.adapter import ImageAdapter

BG_SIZE = (1920, 1080)
CHAR_SIZE = (600, 1200)

_SHARED_POOL_DIR = Path(__file__).resolve().parent / "shared_pool"
_DEFAULT_SILHOUETTE = "adult_male"


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


def _copy_shared_silhouette(silhouette_type: str, out_path: Path) -> bool:
    src = _SHARED_POOL_DIR / f"{silhouette_type}.png"
    if src.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out_path)
        return True
    return False


class PlaceholderAdapter(ImageAdapter):
    def __init__(self, cast: dict | None = None):
        self._roster = (cast or {}).get("cast", {})

    def generate_bg(self, bg_id: str, description: str, out_path: Path) -> None:
        generate_bg(bg_id, out_path)

    def generate_char(
        self,
        char_id: str,
        expr: str,
        description: str,
        out_path: Path,
        reference: Path | None = None,
        style_ref_only: bool = False,
    ) -> None:
        stype = self._roster.get(char_id, {}).get("silhouette_type")
        if stype and _copy_shared_silhouette(stype, out_path):
            return
        if _copy_shared_silhouette(_DEFAULT_SILHOUETTE, out_path):
            return
        generate_char(char_id, expr, out_path)
