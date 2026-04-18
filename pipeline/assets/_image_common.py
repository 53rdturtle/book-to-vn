"""Shared helpers for real-image adapters (nanobanana, openai_image).

Keeps image resizing, matting, hashing, and deferred-matte plumbing in one
place so each backend only owns its API-call logic.
"""
import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image

from pipeline import config
from pipeline.assets.placeholders import CHAR_SIZE


EXPR_HINTS = {
    "neutral": "calm, relaxed expression",
    "smile": "soft, natural smile",
    "sad": "subtly downcast expression",
    "angry": "mildly annoyed, slight frown",
    "surprised": "subtly surprised, slightly raised eyebrows",
    "worried": "faintly concerned expression",
    "thinking": "quietly thoughtful expression",
}


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_resized(
    img_bytes: bytes, out_path: Path, size: tuple[int, int], mode: str,
    matte: bool = False,
) -> None:
    img = Image.open(io.BytesIO(img_bytes))
    if matte and config.CHAR_MATTE == "toonout":
        from pipeline.assets.matte import remove_background
        img = remove_background(img)
    if mode == "RGBA" and img.mode != "RGBA":
        img = img.convert("RGBA")
    elif mode == "RGB" and img.mode != "RGB":
        img = img.convert("RGB")
    if mode == "RGBA":
        bbox = img.getchannel("A").getbbox()
        if bbox:
            img = img.crop(bbox)
    tw, th = size
    iw, ih = img.size
    scale = min(tw / iw, th / ih)
    new_w, new_h = round(iw * scale), round(ih * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0))
    canvas.paste(img, ((tw - new_w) // 2, th - new_h), img if mode == "RGBA" else None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")


class DeferredMatteMixin:
    """Adapter mixin: schedule character matting on a background thread.

    Users call `self._matte_later(img_bytes, out_path)` after writing the
    unmatted image, then `self.drain_matte()` at the end to join threads.
    """

    defer_matte: bool
    on_matte_done: object  # Callable[[Path], None] | None

    def _init_matte(self) -> None:
        self._matte_threads: list[threading.Thread] = []

    def _matte_later(self, img_bytes: bytes, out_path: Path) -> None:
        def _worker():
            try:
                tmp = out_path.with_name(out_path.name + ".matting.tmp")
                save_resized(img_bytes, tmp, CHAR_SIZE, mode="RGBA", matte=True)
                os.replace(tmp, out_path)
                if self.on_matte_done:
                    self.on_matte_done(out_path)
            except Exception as e:  # noqa: BLE001
                print(f"[image-adapter] deferred matte failed for {out_path}: {e}")
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        self._matte_threads.append(t)

    def drain_matte(self) -> None:
        for t in self._matte_threads:
            t.join()
        self._matte_threads.clear()
