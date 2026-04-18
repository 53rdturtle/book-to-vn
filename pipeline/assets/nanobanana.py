"""Nano Banana 2 (Gemini 3.1 Flash Image) adapter.

Generates real BG + character images via the `google-genai` SDK. Uses image
editing with reference images to keep characters visually consistent across
expressions and chapters.
"""
import io
import time
from pathlib import Path

from PIL import Image

from pipeline import api_log, cache, config
from pipeline.assets.adapter import ImageAdapter
from pipeline.assets._image_common import (
    DeferredMatteMixin,
    EXPR_HINTS,
    hash_bytes,
    save_resized,
)
from pipeline.assets.placeholders import BG_SIZE, CHAR_SIZE


class NanoBananaError(RuntimeError):
    pass


def _client():
    if not config.GEMINI_API_KEY:
        raise NanoBananaError("GEMINI_API_KEY is not set")
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)


def _image_config(aspect_ratio: str):
    from google.genai import types

    return types.ImageConfig(
        aspect_ratio=aspect_ratio, image_size=config.NANO_BANANA_IMAGE_SIZE
    )


def _gen_config(aspect_ratio: str):
    from google.genai import types

    return types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=_image_config(aspect_ratio),
    )


def _extract_prompt_text(contents) -> str:
    """Pull the text prompt out of a `contents` payload (string or list of parts)."""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        for item in reversed(contents):
            if isinstance(item, str):
                return item
    return str(contents)


def _contents_summary(contents) -> str:
    """One-line description of the payload — text length plus any reference images."""
    if isinstance(contents, str):
        return f"text({len(contents)} chars)"
    parts = []
    for item in contents:
        if isinstance(item, str):
            parts.append(f"text({len(item)} chars)")
        else:
            parts.append(type(item).__name__)
    return ", ".join(parts)


def _call_image(contents, aspect_ratio: str, call_type: str = "image") -> bytes:
    """Call Gemini 3.1 Flash Image with retries, return raw image bytes."""
    client = _client()
    prompt_text = _extract_prompt_text(contents)
    contents_desc = _contents_summary(contents)
    prompt_summary = prompt_text[:200]
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            t0 = time.time()
            resp = client.models.generate_content(
                model=config.NANO_BANANA_MODEL,
                contents=contents,
                config=_gen_config(aspect_ratio),
            )
            elapsed = time.time() - t0
            for part in resp.parts:
                if part.inline_data is not None and part.inline_data.data:
                    usage = resp.usage_metadata
                    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                    response_desc = (
                        f"image ({len(part.inline_data.data)} bytes, "
                        f"aspect={aspect_ratio}, size={config.NANO_BANANA_IMAGE_SIZE})"
                    )
                    api_log.dump_call(
                        call_type, prompt_text, response_desc,
                        note=f"attempt={attempt + 1} contents=[{contents_desc}]",
                    )
                    api_log.current.record(
                        model=config.NANO_BANANA_MODEL,
                        call_type=call_type,
                        prompt_summary=prompt_summary,
                        response_summary=response_desc,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        elapsed_s=elapsed,
                    )
                    return part.inline_data.data
            raise NanoBananaError("Gemini image response contained no image parts")
        except Exception as e:
            last_err = e
            if attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    raise NanoBananaError(f"Nano Banana call failed after retries: {last_err}")


class NanoBananaAdapter(DeferredMatteMixin, ImageAdapter):
    def __init__(self, style: str | None = None, *, defer_matte: bool = False,
                 on_matte_done=None, **_unused) -> None:
        self.style = style or config.IMAGE_GEN_STYLE
        self.defer_matte = defer_matte
        self.on_matte_done = on_matte_done
        self._init_matte()

    # -- Backgrounds -----------------------------------------------------

    def generate_bg(self, bg_id: str, description: str, out_path: Path) -> None:
        prompt = (
            f"Visual novel background art, {self.style}, widescreen 16:9 composition, "
            f"no characters, no text, no watermark. Scene: {description or bg_id}."
        )
        key = cache.content_key("nanobanana_bg", config.NANO_BANANA_MODEL,
                                config.NANO_BANANA_IMAGE_SIZE, prompt)
        data = cache.get_bytes("nanobanana_bg", key)
        if data is None:
            data = _call_image(prompt, aspect_ratio="16:9", call_type="image_bg")
            cache.put_bytes("nanobanana_bg", key, data)
        save_resized(data, out_path, BG_SIZE, mode="RGB")

    # -- Characters ------------------------------------------------------

    def generate_baseline(self, out_path: Path) -> None:
        """Generate the book's baseline character — establishes overall art style."""
        prompt = (
            f"Full-body character portrait, {self.style}, single character centered, "
            f"neutral pose facing camera, plain neutral background, soft studio lighting, "
            f"full figure from head to feet visible, 1:2 vertical composition, "
            f"no text, no watermark, no additional characters. "
            f"Subject: a young person of unspecified features, serving as the baseline "
            f"art style reference for this book."
        )
        key = cache.content_key("nanobanana_baseline", config.NANO_BANANA_MODEL,
                                config.NANO_BANANA_IMAGE_SIZE, prompt)
        data = cache.get_bytes("nanobanana_baseline", key)
        if data is None:
            data = _call_image(prompt, aspect_ratio="9:16", call_type="image_baseline")
            cache.put_bytes("nanobanana_baseline", key, data)
        if self.defer_matte:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=False)
            self._matte_later(data, out_path)
        else:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=True)

    def generate_char(
        self,
        char_id: str,
        expr: str,
        description: str,
        out_path: Path,
        reference: Path | None = None,
        style_ref_only: bool = False,
    ) -> None:
        expr_hint = EXPR_HINTS.get(expr, expr)
        base_desc = description or f"character id '{char_id}'"
        if reference and reference.exists() and not style_ref_only:
            prompt = (
                f"Generate the same character shown in the reference image, matching art "
                f"style, clothing, and features exactly. "
                f"Only change the facial expression. "
                f"Expression: {expr_hint}. "
                f"Full-body portrait, 1:2 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )
        elif reference and reference.exists() and style_ref_only:
            prompt = (
                f"Generate a character matching the art style of the reference image. "
                f"{self.style}. "
                f"Character: {base_desc}. "
                f"Expression: {expr_hint}. "
                f"Full-body portrait, 1:2 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )
        else:
            prompt = (
                f"Full-body character portrait, {self.style}. "
                f"Character: {base_desc}. "
                f"Expression: {expr_hint}. "
                f"1:2 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )

        ref_bytes = reference.read_bytes() if reference and reference.exists() else None
        ref_hash = hash_bytes(ref_bytes) if ref_bytes else ""
        key = cache.content_key(
            "nanobanana_char", config.NANO_BANANA_MODEL,
            config.NANO_BANANA_IMAGE_SIZE, prompt, char_id, expr, ref_hash,
        )
        data = cache.get_bytes("nanobanana_char", key)
        if data is None:
            if ref_bytes is not None:
                ref_img = Image.open(io.BytesIO(ref_bytes))
                contents = [ref_img, prompt]
            else:
                contents = prompt
            data = _call_image(contents, aspect_ratio="9:16", call_type="image_char")
            cache.put_bytes("nanobanana_char", key, data)
        if self.defer_matte:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=False)
            self._matte_later(data, out_path)
        else:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=True)
