"""OpenAI GPT Image adapter (gpt-image-1-mini by default).

Uses the Images API:
- `client.images.generate` for baseline + backgrounds (text → image).
- `client.images.edit` for reference-guided character generation (Phase B
  basic chars from the baseline, Phase C expression variants from the
  neutral character). `input_fidelity="high"` is added only for the full
  `gpt-image-1` model; the `-mini` variant does not support that param.

Quality is selectable per build (`low`/`medium`/`high`/`auto`), trading cost
for fidelity.
"""
import base64
import io
import time
from pathlib import Path

from pipeline import api_log, cache, config
from pipeline.assets.adapter import ImageAdapter
from pipeline.assets._image_common import (
    DeferredMatteMixin,
    EXPR_HINTS,
    hash_bytes,
    save_resized,
)
from pipeline.assets.placeholders import BG_SIZE, CHAR_SIZE


class OpenAIImageError(RuntimeError):
    pass


_BG_SIZE = "1536x1024"        # landscape
_CHAR_SIZE_STR = "1024x1536"  # portrait


def _client():
    if not config.OPENAI_API_KEY:
        raise OpenAIImageError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    return OpenAI(api_key=config.OPENAI_API_KEY)


def _usage_tokens(usage) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    input_tokens = getattr(usage, "input_tokens", None) or 0
    output_tokens = getattr(usage, "output_tokens", None) or 0
    return int(input_tokens), int(output_tokens)


def _decode_b64(result) -> bytes:
    data = result.data[0]
    b64 = getattr(data, "b64_json", None)
    if not b64:
        raise OpenAIImageError("OpenAI image response missing b64_json")
    return base64.b64decode(b64)


def _call_generate(prompt: str, *, size: str, quality: str, call_type: str) -> bytes:
    client = _client()
    model = config.OPENAI_IMAGE_MODEL
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            t0 = time.time()
            result = client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
            elapsed = time.time() - t0
            img_bytes = _decode_b64(result)
            in_tok, out_tok = _usage_tokens(getattr(result, "usage", None))
            response_desc = (
                f"image ({len(img_bytes)} bytes, size={size}, quality={quality})"
            )
            api_log.dump_call(
                call_type, prompt, response_desc,
                note=f"attempt={attempt + 1} model={model}",
            )
            api_log.current.record(
                model=model,
                call_type=call_type,
                prompt_summary=prompt[:200],
                response_summary=response_desc,
                input_tokens=in_tok,
                output_tokens=out_tok,
                elapsed_s=elapsed,
            )
            return img_bytes
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    raise OpenAIImageError(f"OpenAI images.generate failed after retries: {last_err}")


def _call_edit(
    prompt: str, ref_bytes_list: list[bytes], *,
    size: str, quality: str, call_type: str,
) -> bytes:
    client = _client()
    model = config.OPENAI_IMAGE_MODEL
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            # Each retry needs fresh file-like objects; SDK consumes them.
            image_arg = [
                ("image.png", io.BytesIO(b), "image/png") for b in ref_bytes_list
            ]
            t0 = time.time()
            edit_kwargs = dict(
                model=model,
                image=image_arg if len(image_arg) > 1 else image_arg[0],
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
            # input_fidelity is only supported on the full gpt-image-1 model,
            # not on gpt-image-1-mini.
            if "mini" not in model:
                edit_kwargs["input_fidelity"] = "high"
            result = client.images.edit(**edit_kwargs)
            elapsed = time.time() - t0
            img_bytes = _decode_b64(result)
            in_tok, out_tok = _usage_tokens(getattr(result, "usage", None))
            response_desc = (
                f"image ({len(img_bytes)} bytes, size={size}, quality={quality}, "
                f"refs={len(ref_bytes_list)})"
            )
            api_log.dump_call(
                call_type, prompt, response_desc,
                note=f"attempt={attempt + 1} model={model} edit",
            )
            api_log.current.record(
                model=model,
                call_type=call_type,
                prompt_summary=prompt[:200],
                response_summary=response_desc,
                input_tokens=in_tok,
                output_tokens=out_tok,
                elapsed_s=elapsed,
            )
            return img_bytes
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    raise OpenAIImageError(f"OpenAI images.edit failed after retries: {last_err}")


class OpenAIImageAdapter(DeferredMatteMixin, ImageAdapter):
    def __init__(
        self, style: str | None = None, *,
        defer_matte: bool = False, on_matte_done=None,
        quality: str | None = None, **_unused,
    ) -> None:
        self.style = style or config.IMAGE_GEN_STYLE
        self.quality = quality or config.OPENAI_IMAGE_QUALITY
        self.defer_matte = defer_matte
        self.on_matte_done = on_matte_done
        self._init_matte()

    # -- Backgrounds -----------------------------------------------------

    def generate_bg(self, bg_id: str, description: str, out_path: Path) -> None:
        prompt = (
            f"Visual novel background art, {self.style}, widescreen 16:9 composition, "
            f"no characters, no text, no watermark. Scene: {description or bg_id}."
        )
        key = cache.content_key(
            "openai_bg", config.OPENAI_IMAGE_MODEL, _BG_SIZE, self.quality, prompt,
        )
        data = cache.get_bytes("openai_bg", key)
        if data is None:
            data = _call_generate(
                prompt, size=_BG_SIZE, quality=self.quality, call_type="image_bg",
            )
            cache.put_bytes("openai_bg", key, data)
        save_resized(data, out_path, BG_SIZE, mode="RGB")

    # -- Characters ------------------------------------------------------

    def generate_baseline(self, out_path: Path) -> None:
        prompt = (
            f"Full-body character portrait, {self.style}, single character centered, "
            f"neutral pose facing camera, plain neutral background, soft studio lighting, "
            f"full figure from head to feet visible, 2:3 vertical composition, "
            f"no text, no watermark, no additional characters. "
            f"Subject: a young person of unspecified features, serving as the baseline "
            f"art style reference for this book."
        )
        key = cache.content_key(
            "openai_baseline", config.OPENAI_IMAGE_MODEL, _CHAR_SIZE_STR,
            self.quality, prompt,
        )
        data = cache.get_bytes("openai_baseline", key)
        if data is None:
            data = _call_generate(
                prompt, size=_CHAR_SIZE_STR, quality=self.quality,
                call_type="image_baseline",
            )
            cache.put_bytes("openai_baseline", key, data)
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
        has_ref = bool(reference and reference.exists())

        if has_ref and not style_ref_only:
            prompt = (
                f"Generate the same character shown in the reference image, matching art "
                f"style, clothing, and features exactly. "
                f"Only change the facial expression. "
                f"Expression: {expr_hint}. "
                f"Full-body portrait, 2:3 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )
        elif has_ref and style_ref_only:
            prompt = (
                f"Generate a character matching the art style of the reference image. "
                f"{self.style}. "
                f"Character: {base_desc}. "
                f"Expression: {expr_hint}. "
                f"Full-body portrait, 2:3 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )
        else:
            prompt = (
                f"Full-body character portrait, {self.style}. "
                f"Character: {base_desc}. "
                f"Expression: {expr_hint}. "
                f"2:3 vertical composition, plain neutral background, "
                f"no text, no watermark."
            )

        ref_bytes = reference.read_bytes() if has_ref else None
        ref_hash = hash_bytes(ref_bytes) if ref_bytes else ""
        key = cache.content_key(
            "openai_char", config.OPENAI_IMAGE_MODEL, _CHAR_SIZE_STR,
            self.quality, prompt, char_id, expr, ref_hash,
        )
        data = cache.get_bytes("openai_char", key)
        if data is None:
            if ref_bytes is not None:
                data = _call_edit(
                    prompt, [ref_bytes], size=_CHAR_SIZE_STR,
                    quality=self.quality, call_type="image_char",
                )
            else:
                data = _call_generate(
                    prompt, size=_CHAR_SIZE_STR, quality=self.quality,
                    call_type="image_char",
                )
            cache.put_bytes("openai_char", key, data)
        if self.defer_matte:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=False)
            self._matte_later(data, out_path)
        else:
            save_resized(data, out_path, CHAR_SIZE, mode="RGBA", matte=True)
