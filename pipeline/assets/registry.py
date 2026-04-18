"""Backend registry for image adapters.

Each backend registers a factory keyed by name. Factories are lazy — they
import the adapter module only when the backend is actually selected, so a
missing SDK (e.g. `openai`) never breaks imports for users on a different
backend.

Add a new backend by calling `register("my-backend", factory_callable)`.
"""
from typing import Callable

from pipeline import config
from pipeline.assets.adapter import ImageAdapter

Factory = Callable[..., ImageAdapter]

_FACTORIES: dict[str, Factory] = {}
_CONCURRENCY: dict[str, Callable[[], int]] = {}


def register(name: str, factory: Factory, *, concurrency: Callable[[], int] | None = None) -> None:
    _FACTORIES[name] = factory
    if concurrency is not None:
        _CONCURRENCY[name] = concurrency


def available() -> list[str]:
    return sorted(_FACTORIES)


def create(name: str, **kwargs) -> ImageAdapter:
    if name not in _FACTORIES:
        raise ValueError(f"unknown image backend: {name!r}. Available: {available()}")
    return _FACTORIES[name](**kwargs)


def backend_concurrency(name: str) -> int:
    getter = _CONCURRENCY.get(name)
    return getter() if getter else 1


# ---- built-in registrations ----------------------------------------------

def _make_placeholder(**kwargs) -> ImageAdapter:
    from pipeline.assets.placeholders import PlaceholderAdapter
    cast = kwargs.get("cast")
    return PlaceholderAdapter(cast=cast)


def _make_nanobanana(**kwargs) -> ImageAdapter:
    from pipeline.assets.nanobanana import NanoBananaAdapter
    return NanoBananaAdapter(**kwargs)


def _make_openai(**kwargs) -> ImageAdapter:
    from pipeline.assets.openai_image import OpenAIImageAdapter
    return OpenAIImageAdapter(**kwargs)


register("placeholder", _make_placeholder, concurrency=lambda: 1)
register("nanobanana", _make_nanobanana, concurrency=lambda: config.NANO_BANANA_CONCURRENCY)
register("openai", _make_openai, concurrency=lambda: config.OPENAI_IMAGE_CONCURRENCY)
