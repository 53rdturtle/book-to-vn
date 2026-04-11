import os
from pathlib import Path

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

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
PROMPT_PATH = PACKAGE_ROOT / "llm" / "prompts" / "chapter_to_timeline.txt"
