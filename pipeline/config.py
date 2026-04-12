import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

NANO_BANANA_MODEL = os.environ.get("NANO_BANANA_MODEL", "gemini-3.1-flash-image-preview")
NANO_BANANA_IMAGE_SIZE = os.environ.get("NANO_BANANA_IMAGE_SIZE", "1K")
IMAGE_GEN_STYLE = os.environ.get("IMAGE_GEN_STYLE", "anime visual novel style")
MAJOR_CHAR_MIN_APPEARANCES = int(os.environ.get("MAJOR_CHAR_MIN_APPEARANCES", "10"))

BUNDLE_BOOK_JSON = "book.json"
BUNDLE_CHAPTERS_DIR = "chapters"
BUNDLE_ASSETS_DIR = "assets"
BUNDLE_BG_DIR = "assets/bg"
BUNDLE_CHAR_DIR = "assets/char"
BUNDLE_BGM_DIR = "assets/bgm"
BUNDLE_SE_DIR = "assets/se"
BUNDLE_VOICE_DIR = "voice"

PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_ROOT / "schema" / "timeline.schema.json"
LLM_SCHEMA_PATH = PACKAGE_ROOT / "schema" / "llm_timeline.schema.json"
SPLIT_SCHEMA_PATH = PACKAGE_ROOT / "schema" / "chapter_split.schema.json"
CAST_SCHEMA_PATH = PACKAGE_ROOT / "schema" / "cast.schema.json"
PROMPT_PATH = PACKAGE_ROOT / "llm" / "prompts" / "chapter_to_timeline.txt"
SPLITTER_PROMPT_PATH = PACKAGE_ROOT / "llm" / "prompts" / "chapter_splitter.txt"

_cache_env = os.environ.get("BOOK_TO_VN_CACHE_DIR", "").strip()
CACHE_DIR = Path(_cache_env) if _cache_env else (Path.home() / ".book-to-vn" / "cache")

BUNDLE_CAST_JSON = "cast.json"
