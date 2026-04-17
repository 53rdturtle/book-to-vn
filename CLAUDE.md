# Claude Code Context for book-to-vn

## Architecture

**Goal:** Turn plain-text novels/stories into playable visual novels in Godot.

**Two decoupled halves:**
1. **Python pipeline** (`pipeline/`) — ingests `.txt`, orchestrates chapter split → segmentation → Gemini LLM call → asset generation → TTS → writes a bundle.
2. **Godot runtime** (`godot_player/`) — generic player that loads any bundle and plays it. No per-book Godot projects.

**Contract:** JSON timeline schema is the only coupling. Both sides validate against it.

### Pipeline Flow
```
book.txt
  ↓ chapters.py (M1: single-chapter wrapper; M2: heuristic + Gemini fallback)
segments (60–180 chars ASCII / 25–70 chars CJK, merged from sentences)
  ↓ gemini_client.py (compact LLM schema: say carries seg_id only)
LLM timeline (schema: llm_timeline.schema.json)
  ↓ _expand_says in __main__.py (resolves seg_id → text + voice_clip)
Runtime timeline (schema: timeline.schema.json)
  ↓ bundle.py (generates placeholder assets post-hoc from timeline commands)
bundle/ directory structure (book.json, chapters/, assets/, voice/)
  ↓ Godot BundleLoader (loads bundle, plays commands via TimelinePlayer)
Visual novel playback
```

### Key Architectural Decisions (Locked M1)
- **Input:** any `.txt`, structure-tolerant.
- **Assets v1:** placeholder art (Pillow-drawn BGs + silhouettes) and silent TTS stubs. Real generation lands in M4–M5 behind adapter interfaces.
- **Gemini output:** compact form (send `seg_id`, not prose text). Pipeline expands post-hoc for zero text drift. Token savings ~600 per chapter.
- **Caching:** stub in M1 (no-op). Real content-hash cache wires in M2.
- **Character state:** persistent `cast.json` deferred to M2. M1 has no cross-chapter memory.

## File Structure

```
pipeline/
  __main__.py              # CLI: `python -m pipeline build <txt> -o <bundle>` or `editor`
  config.py               # env loading, path constants (GODOT_EXE, EDITOR_PORT)
  api_log.py              # centralized API call logging (token usage, costs)
  cache.py                # stub (M1); content-hash keyed cache (M2+)
  cast.py                 # persistent character roster (M2+)
  backgrounds.py          # persistent background descriptions (M4+)
  chapters.py             # single-chapter wrapper (M1); multi-chapter heuristics (M2)
  segmenter.py            # sentence split + merge (CJK-aware: 25–70 / ASCII: 60–180)
  asset_catalog.py        # curated bg/bgm/se ID pools for manifest validation
  bundle.py               # writes bundle/ directory tree
  steps.py                # discrete pipeline steps (split, timelines, descriptions, asset gen) — reusable by CLI & editor
  llm/
    gemini_client.py      # thin google-genai wrapper, JSON mode, 3× retry
    visual_descriptions.py # LLM-driven character/background visual descriptions (M4)
    prompts/chapter_to_timeline.txt  # interpolated with schema + segments
    prompts/visual_descriptions.txt  # propose descriptions from book title + excerpt (M4)
  assets/
    adapter.py            # abstract ImageAdapter interface (M4)
    placeholders.py       # PlaceholderAdapter: scan timeline → generate Pillow BGs/chars
    nanobanana.py         # NanoBananaAdapter: Gemini 3.1 Flash Image real image gen (M4)
    matte.py              # ToonOut background removal for character images (anime-specialized BiRefNet fine-tune)
  editor/
    __init__.py
    server.py             # FastAPI server (SSE events, /api/start, /api/confirm, /api/regenerate, /api/upload, /api/play)
    jobs.py               # in-memory job registry; worker thread drives pipeline, confirmations via threading.Event
    static/
      index.html          # single-page UI (Source / Progress / Assets panels stacked vertically)
      app.js              # vanilla JS event stream consumer, form handling, asset management
      style.css           # dark theme styling
  tts/
    adapter.py            # abstract TTSAdapter interface
    silent.py             # implements silent OGG generation by text length
  schema/
    timeline.schema.json       # runtime shape (what Godot reads)
    llm_timeline.schema.json   # compact LLM shape (what Gemini emits)

godot_player/
  project.godot           # Godot 4.6 config, 1280×720
  Main.tscn               # scene tree: Background, CharLeft/Middle/Right, DialogueBox, audio players
  scripts/
    BundleLoader.gd       # load bundle/ dir, parse JSON, resolve asset paths
    Main.gd               # parse --bundle arg, load chapters, auto-advance to next at end

tests/
  test_segmenter.py       # CJK detection + segment sizing
  test_expand_says.py     # say expansion + segment coverage
  test_asset_validation.py # asset-ID manifest validation
  test_golden.py          # golden-file expansion + asset scan
  fixtures/               # ch01_llm.json, ch01_expected.json

samples/
  short.txt               # ~500-word test chapter (narrator + 2 characters)
```

## Command Examples

```bash
# Setup
pip install -r pipeline/requirements.txt
export GEMINI_API_KEY="..."

# Build a bundle with placeholder assets (default)
python -m pipeline build samples/short.txt -o out/short

# Build with real Nano Banana 2 (Gemini 3.1 Flash Image) assets (default 512 resolution)
python -m pipeline build samples/short.txt -o out/short --image-gen nanobanana

# Build with NanoBanana at 1K resolution (higher quality, higher cost)
NANO_BANANA_IMAGE_SIZE=1K python -m pipeline build samples/short.txt -o out/short --image-gen nanobanana

# Build with NanoBanana using 4 concurrent image generation threads (default 10)
NANO_BANANA_CONCURRENCY=4 python -m pipeline build samples/short.txt -o out/short --image-gen nanobanana

# Build with NanoBanana, auto-accepting all descriptions and confirmations
python -m pipeline build samples/short.txt -o out/short --image-gen nanobanana --skip-image-gen-confirmation

# Multi-chapter (Chinese primary, English regression)
python -m pipeline build samples/multi_zh.txt -o out/multi_zh
python -m pipeline build samples/multi_en.txt -o out/multi_en

# Bypass the content-hash cache for a clean rebuild
python -m pipeline build samples/multi_zh.txt -o out/multi_zh --no-cache

# Run tests
pytest tests/ -v

# Validate a timeline
python -m pipeline validate out/short/chapters/ch01.json

# Launch the interactive assets editor (web UI)
python -m pipeline editor
# Browser opens at http://127.0.0.1:8765; paste text, set bundle dir, click Start
# Step through confirmations interactively: edit visual style, character/background descriptions inline, 
# regenerate assets, upload overrides, play.

# Play in Godot (4.6+)
# Godot binary (directory containing the .exe):
#   C:\Users\wssrd\OneDrive\Desktop\Godot_v4.6.1-stable_win64.exe\
# Launch from bash (use console exe for log output):
"/c/Users/wssrd/OneDrive/Desktop/Godot_v4.6.1-stable_win64.exe/Godot_v4.6.1-stable_win64_console.exe" --path "/c/Users/wssrd/Code/book-to-vn/godot_player" -- --bundle ../out/short
```

## Gemini Integration

### Timeline Generation

**Model:** `gemini-3-flash-preview` (configurable via `GEMINI_MODEL` env; default in `config.py`).

**Compact LLM Schema:** `say` commands only carry `{"type": "say", "speaker": "id", "seg": "ch01_seg003"}` — Gemini does NOT output prose. The pipeline expands `seg` → `text` + `voice_clip` post-hoc from the segmenter table.

**Prompt:** `pipeline/llm/prompts/chapter_to_timeline.txt`. Embeds `llm_timeline.schema.json` at template-expansion time so schema and prompt stay in sync.

**Validation:** LLM output validated against `llm_timeline.schema.json` immediately after Gemini call. Runtime timeline (post-expansion) validated against `timeline.schema.json` before bundle write.

**Asset-ID Discipline:** `pipeline/asset_catalog.py` defines curated pools of bg/bgm/se IDs. The prompt injects these as an ASSET MANIFEST. Post-LLM validation checks all IDs against the catalog; unknown IDs trigger one retry before failing.

**Error Handling:** Transient failures retry 3× with backoff. Schema validation failures raise loudly. Asset-ID validation retries once with feedback.

### Image Generation (M4)

**Model:** `gemini-3.1-flash-image-preview` (Nano Banana 2, configurable via `NANO_BANANA_MODEL` env).

**Output:** 512 resolution (configurable via `NANO_BANANA_IMAGE_SIZE` env; valid values: `"512"`, `"1K"`, `"2K"`, `"4K"`). Default 512 for cost-efficiency during development.

**Workflow:** `--image-gen nanobanana` triggers 3-phase pipeline:
1. LLM proposes `visual_style` (concise art style description matching book tone/genre) + character/background visual descriptions (leans on knowledge of well-known source works)
2. User confirms or edits style and descriptions via stdin (CLI) or web UI (editor)
3. **Phase A:** Generate baseline character (style anchor for entire book, uses proposed visual_style)
4. **Phase B:** Generate basic (neutral) characters using baseline as reference (style reference only; character description applies) → user confirms
5. **Phase C:** Generate all expression variants per character (match clothing/features; pose may vary per expression)
6. Generate backgrounds from enriched descriptions

**Character Generation Prompts:** All prompts include the proposed `visual_style`. Three strategies depending on context:
- **No reference:** Visual style + character description + expression
- **Baseline reference (Phase B):** Visual style + match baseline art style only; character description (pose/clothing unconstrained)
- **Neutral reference (Phase C):** Match clothing/features exactly; expression only changes face (pose may naturalistically vary)

**Major Character Filter:** Only characters with ≥10 appearances (configurable via `MAJOR_CHAR_MIN_APPEARANCES`) receive NanoBanana images. Minor characters fall back to placeholder silhouettes.

**Caching:** Image cache at `~/.book-to-vn/cache/nanobanana_*` keyed by model + prompt + reference hash. Subsequent builds reuse.

**Descriptions:** LLM generates `visual_style` (art style for all images), `visual_description` (character/background appearance), and `display_name` (speaker label in source language, e.g., Chinese characters for Chinese books). Visual style is stored in `backgrounds.json` and applies uniformly to baseline, character, and background generation. Character descriptions and names are stored in `cast.json` and reused across chapters. Background descriptions are stored in `backgrounds.json` and similarly reused. Edit any entry and delete the corresponding PNG to regenerate with a new description.

**Background Removal:** Character images are automatically matted using ToonOut (fine-tuned BiRefNet trained on 1.2K anime images, 99.5% pixel accuracy). Removes gray placeholder backgrounds without halos or color fringing. Runs on CPU after image generation. Configurable via `CHAR_MATTE` env (`"toonout"` default; `"none"` to disable).

**Image Resizing:** Character images are alpha-cropped to their content bounding box after matting, normalizing height across expression variants (which may have different padding). Then resized to fit within 600×1200 while preserving aspect ratio, centered horizontally and anchored at bottom (feet on stage floor). BG images fitted to 1920×1080. Transparent/black padding fills unused space.

**API Logging:** All Gemini calls logged to `logs/api_calls.json` (structured) and `logs/api_usage.txt` (human-readable summary) with token counts and estimated USD costs.

## Data Model

### Segment (Internal)
```python
@dataclass
class Segment:
    seg_id: str  # e.g. "ch01_seg003"
    text: str    # 60–180 chars (ASCII) / 25–70 chars (CJK), merged sentences
```

### Timeline JSON (Runtime, in bundle)
```json
{
  "chapter_id": "ch01",
  "title": "The Arrival",
  "commands": [
    { "type": "bg", "id": "bg_forest_dawn" },
    { "type": "char_show", "id": "alice", "expr": "neutral", "slot": "left" },
    { "type": "say", "speaker": "alice", "text": "...", "voice_clip": "ch01_seg003.ogg" },
    { "type": "char_hide", "id": "alice" },
    { "type": "bgm_play", "id": "bgm_calm", "fade_ms": 800 }
  ]
}
```

Slots: `left`, `middle`, `right` (fixed enum). Expressions: `neutral`, `smile`, `sad`, `angry`, `surprised`, `worried`, `thinking` (fixed enum, M3).

### Bundle on Disk
```
bundle/
  book.json                  # { title, chapters: [...] }
  cast.json                  # character roster with display names, first chapter, appearance counts, visual descriptions (M2+), silhouette types for minor chars (M4+)
  backgrounds.json           # visual_style (art style for all images) + background descriptions, persisted for reuse (M4+)
  chapters/ch01.json         # runtime timeline
  assets/
    bg/bg_*.png              # 1920×1080 placeholder or NanoBanana BGs
    char/<id>/<expr>.png     # 600×1200 silhouette chars or NanoBanana images
    bgm/<id>.ogg             # silent 5-sec OGGs
    se/<id>.ogg              # silent 0.8-sec OGGs
  voice/ch01_seg*.ogg        # silent OGGs, length ∝ text length
  logs/                      # build logs and API call history
```

## Known Limitations (post-M4)

- **Chapter splitter scale limit:** Heuristics handle well-formatted books cleanly. The Gemini fallback embeds the entire text as numbered lines in the prompt, so inputs beyond ~30k characters may exceed context comfortably. Windowed splitting is a stub — revisit in M5+.
- **Silent TTS stubs:** Voice clips are silent OGGs scaled by text length. Real TTS (M5) behind adapter interface.
- **Static asset catalog:** `asset_catalog.py` has a fixed set of bg/bgm/se IDs. Future milestones can make this context-dependent (genre, setting).
- **No inline effects:** `say` commands don't support word-level styling (bold, color, shake). Future milestone adds optional `fx` array with word-offset spans.

## Milestones

- **M1 (done):** Skeleton + playable thin slice. Single chapter, placeholder assets, silent TTS, Gemini 3-Flash timeline generation.
- **M2 (done):** Multi-chapter ingestion with Chinese/English heuristic splitter + Gemini fallback. SHA-256 content-hash cache at `~/.book-to-vn/cache` (override with `BOOK_TO_VN_CACHE_DIR`). Persistent `cast.json` threaded into the timeline prompt so character IDs stay stable across chapters.
- **M3 (done):** Prompt quality & coherence (slot stability, scene-change, BGM discipline, narrator rules). Asset-ID discipline via `asset_catalog.py` + post-LLM validation with retry. Expression enum locked (7 values). CJK-aware segmenter (25–70 chars). Golden-file tests (30 tests via pytest).
- **M4 (done):** Real image generation via Nano Banana 2 (Gemini 3.1 Flash Image). ImageAdapter interface (PlaceholderAdapter + NanoBananaAdapter). LLM-driven visual descriptions (character/background). 3-phase character reference pipeline (baseline → basic → expressions). Major character filtering (≥10 appearances). Binary image caching. `--image-gen nanobanana` CLI flag with interactive description editing. **Minor character silhouette pool:** Characters < 10 appearances are classified by LLM (gender/age: adult/child/elder × male/female) and assigned pre-matted silhouettes from `pipeline/assets/shared_pool/`. **Assets editor (M4.5):** Web UI (`python -m pipeline editor`) for step-by-step builds with in-browser confirmation, inline description editing, per-asset regeneration with cascade, manual image upload overrides, and integrated Godot playback.
- **M5 (next):** Real TTS. Per-character voice assignment.
- **M6:** BGM/SE library. Mood-based selection.
- **M7:** Polish. Save/load, backlog, text speed, skip read. Standalone export.

## Development Notes

- **Schema is stable:** Timeline schema is the contract between pipeline and Godot. Changes to it block both halves. Validate early.
- **Segments are immutable per build:** Once written to `voice/` and `chapters/`, segment IDs and text don't change. Makes caching safe.
- **Godot loads external assets at runtime:** Uses `Image.load_from_file()` for PNG and `AudioStreamOggVorbis.load_from_file()` for OGG. Both work on 4.6+. Paths must resolve from the bundle root.
- **Command dispatch in Godot is single-threaded:** `TimelinePlayer` steps through commands sequentially, advancing only on user input (space/enter/click). Voice clips play while dialogue is on screen; BGM fades persist across advances.
- **Editor debug output:** The user reads debug/log output from the editor Web UI (SSE `debug` events), not from the terminal. `print()` inside the worker thread in `jobs.py` and any code it calls (e.g. `steps.py`) is captured via `redirect_stdout` and displayed in the browser.
- **Bug-fix workflow:** If a bug's cause is not obvious from reading the code, DO NOT blind-fix (i.e. speculative changes hoping they resolve it). Instead, add targeted debug logs (`print()` lines that reach the editor UI via the stdout tee), ask the user to reproduce, and fix based on the actual log output. Blind fixes waste cycles and can introduce regressions.
