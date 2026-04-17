import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline import config

_override: Path | None = None


def set_dir(path: Path | None) -> None:
    """Point the cache at a specific directory for the current build.

    Passing None reverts to the global ``config.CACHE_DIR``.
    """
    global _override
    _override = Path(path) if path else None


def _root() -> Path:
    return _override if _override is not None else config.CACHE_DIR


def content_key(*parts: Any) -> str:
    """Build a content-hash key from arbitrary stringifiable parts.

    Callers pass every input that determines the result (prompt text, model,
    chapter body, etc.) so that any change invalidates the cache automatically.
    """
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, (dict, list)):
            blob = json.dumps(p, ensure_ascii=False, sort_keys=True)
        else:
            blob = str(p)
        h.update(blob.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _path_for(stage: str, key: str) -> Path:
    return _root() / stage / key[:2] / f"{key}.json"


def _bytes_path_for(stage: str, key: str, ext: str) -> Path:
    return _root() / stage / key[:2] / f"{key}.{ext}"


def get(stage: str, key: str) -> Any | None:
    path = _path_for(stage, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def put(stage: str, key: str, value: Any) -> None:
    path = _path_for(stage, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def get_bytes(stage: str, key: str, ext: str = "png") -> bytes | None:
    path = _bytes_path_for(stage, key, ext)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def put_bytes(stage: str, key: str, data: bytes, ext: str = "png") -> None:
    path = _bytes_path_for(stage, key, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
