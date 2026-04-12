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
  __main__.py              # CLI: `python -m pipeline build <txt> -o <bundle>`
  config.py               # env loading, path constants
  cache.py                # stub (M1); content-hash keyed cache (M2+)
  chapters.py             # single-chapter wrapper (M1); multi-chapter heuristics (M2)
  segmenter.py            # sentence split + merge (CJK-aware: 25–70 / ASCII: 60–180)
  asset_catalog.py        # curated bg/bgm/se ID pools for manifest validation
  bundle.py               # writes bundle/ directory tree
  llm/
    gemini_client.py      # thin google-genai wrapper, JSON mode, 3× retry
    visual_descriptions.py # LLM-driven character/background visual descriptions (M4)
    prompts/chapter_to_timeline.txt  # interpolated with schema + segments
    prompts/visual_descriptions.txt  # propose descriptions from book title + excerpt (M4)
  assets/
    adapter.py            # abstract ImageAdapter interface (M4)
    placeholders.py       # PlaceholderAdapter: scan timeline → generate Pillow BGs/chars
    nanobanana.py         # NanoBananaAdapter: Gemini 3.1 Flash Image real image gen (M4)
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
    Main.gd               # parse --bundle arg, load chapter 0, advance on input

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

# Build with real Nano Banana 2 (Gemini 3.1 Flash Image) assets
python -m pipeline build samples/short.txt -o out/short --image-gen nanobanana

# Multi-chapter (Chinese primary, English regression)
python -m pipeline build samples/multi_zh.txt -o out/multi_zh
python -m pipeline build samples/multi_en.txt -o out/multi_en

# Bypass the content-hash cache for a clean rebuild
python -m pipeline build samples/multi_zh.txt -o out/multi_zh --no-cache

# Run tests
pytest tests/ -v

# Validate a timeline
python -m pipeline validate out/short/chapters/ch01.json

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

**Output:** 1K resolution (configurable via `NANO_BANANA_IMAGE_SIZE` env; bump to 2K/4K post-prototyping).

**Workflow:** `--image-gen nanobanana` triggers 3-phase pipeline:
1. LLM proposes character/background visual descriptions (leans on knowledge of well-known source works)
2. User confirms or edits descriptions via stdin
3. **Phase A:** Generate baseline character (style anchor for entire book)
4. **Phase B:** Generate basic (neutral) characters using baseline as reference → user confirms
5. **Phase C:** Generate all expression variants per character (auto)
6. Generate backgrounds from enriched descriptions

**Character Consistency:** Baseline → basic reference → expression variants via image-to-image.

**Major Character Filter:** Only characters with ≥10 appearances (configurable via `MAJOR_CHAR_MIN_APPEARANCES`) receive NanoBanana images. Minor characters fall back to placeholder silhouettes.

**Caching:** Image cache at `~/.book-to-vn/cache/nanobanana_*` keyed by model + prompt + reference hash. Subsequent builds reuse.

**Descriptions:** Stored in `cast.json` (`visual_description` field) and reused across chapters. First-appearance descriptions are generated once and persisted.

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
  chapters/ch01.json         # runtime timeline
  assets/
    bg/bg_*.png              # 1920×1080 placeholder BGs
    char/<id>/<expr>.png     # 600×1200 silhouette chars
    bgm/<id>.ogg             # silent 5-sec OGGs
    se/<id>.ogg              # silent 0.8-sec OGGs
  voice/ch01_seg*.ogg        # silent OGGs, length ∝ text length
```

## Known Limitations (post-M3)

- **Chapter splitter scale limit:** Heuristics handle well-formatted books cleanly. The Gemini fallback embeds the entire text as numbered lines in the prompt, so inputs beyond ~30k characters may exceed context comfortably. Windowed splitting is a stub — revisit in M4+.
- **Cast display names are raw IDs:** `cast.json` uses the Gemini-generated speaker ID as `display_name` on first sight. For Chinese input this is usually fine (IDs are Chinese characters); for pinyin'd IDs it looks rough. Future milestone can add a dedicated name field.
- **Silent TTS stubs:** Voice clips are silent OGGs scaled by text length. Real TTS (M5) behind adapter interface.
- **Static asset catalog:** `asset_catalog.py` has a fixed set of bg/bgm/se IDs. Future milestones can make this context-dependent (genre, setting).
- **No inline effects:** `say` commands don't support word-level styling (bold, color, shake). Future milestone adds optional `fx` array with word-offset spans.

## Milestones

- **M1 (done):** Skeleton + playable thin slice. Single chapter, placeholder assets, silent TTS, Gemini 3-Flash timeline generation.
- **M2 (done):** Multi-chapter ingestion with Chinese/English heuristic splitter + Gemini fallback. SHA-256 content-hash cache at `~/.book-to-vn/cache` (override with `BOOK_TO_VN_CACHE_DIR`). Persistent `cast.json` threaded into the timeline prompt so character IDs stay stable across chapters.
- **M3 (done):** Prompt quality & coherence (slot stability, scene-change, BGM discipline, narrator rules). Asset-ID discipline via `asset_catalog.py` + post-LLM validation with retry. Expression enum locked (7 values). CJK-aware segmenter (25–70 chars). Golden-file tests (30 tests via pytest).
- **M4 (done):** Real image generation via Nano Banana 2 (Gemini 3.1 Flash Image). ImageAdapter interface (PlaceholderAdapter + NanoBananaAdapter). LLM-driven visual descriptions (character/background). 3-phase character reference pipeline (baseline → basic → expressions). Major character filtering (≥10 appearances). Binary image caching. `--image-gen nanobanana` CLI flag with interactive description editing.
- **M5 (next):** Real TTS. Per-character voice assignment.
- **M6:** BGM/SE library. Mood-based selection.
- **M7:** Polish. Save/load, backlog, text speed, skip read. Standalone export.

## Development Notes

- **Schema is stable:** Timeline schema is the contract between pipeline and Godot. Changes to it block both halves. Validate early.
- **Segments are immutable per build:** Once written to `voice/` and `chapters/`, segment IDs and text don't change. Makes caching safe.
- **Godot loads external assets at runtime:** Uses `Image.load_from_file()` for PNG and `AudioStreamOggVorbis.load_from_file()` for OGG. Both work on 4.6+. Paths must resolve from the bundle root.
- **Command dispatch in Godot is single-threaded:** `TimelinePlayer` steps through commands sequentially, advancing only on user input (space/enter/click). Voice clips play while dialogue is on screen; BGM fades persist across advances.
