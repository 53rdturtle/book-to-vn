# Text-to-VN Generator — Roadmap

> Long-lived roadmap for the project. Per-milestone implementation plans (M1, M2, ...) are created as separate plan-mode sessions and should reference this file.

## Context
Goal: feed in a plain text file (novel, story, etc.) and get out a playable visual novel in Godot. The pipeline should split the text into chapters, prepare a pool of assets (backgrounds, character sprites, SE, BGM), break each chapter into speakable segments, and ask Gemini to author a per-chapter VN timeline (show/hide characters with expressions and positions, swap backgrounds, play/stop BGM and SE, speak segments via TTS). Godot is the runtime, not the generator.

## Architecture

Two decoupled halves connected by a stable on-disk bundle format.

```
book.txt ──► Python pipeline ──► bundle/
                                   ├── book.json          (metadata, chapter index)
                                   ├── cast.json          (persistent character sheet)
                                   ├── assets/
                                   │   ├── bg/   *.png
                                   │   ├── char/ <name>/<expr>.png
                                   │   ├── se/   *.ogg
                                   │   └── bgm/  *.ogg
                                   ├── voice/   ch01_seg003.ogg ...
                                   └── chapters/
                                       ├── ch01.json      (timeline of typed commands)
                                       └── ...
                                          ▲
                                          │ loaded at runtime
                                   Godot player project (generic)
```

- **Pipeline**: Python. Orchestrates chapter split → asset prep → segmentation → Gemini call → TTS → cache.
- **Runtime**: one generic Godot 4 project that opens any bundle directory and plays it. No per-book Godot projects.
- **Contract**: the JSON timeline schema is the only coupling between halves — both sides validate against it.

## Decisions locked from interview
- Input: any `.txt`, structure-tolerant.
- Pipeline: offline Python; Godot only plays.
- Assets v1: **placeholder art and silent TTS stubs**. Real image gen and real TTS are later milestones behind adapter interfaces.
- Chapter split: regex heuristics (`^Chapter \d+`, roman numerals, `第.章`, blank-line gaps) with Gemini fallback only when heuristics fail.
- Script format: JSON array of typed commands.
- LLM granularity: one Gemini call per chapter, passing the whole chapter + pre-extracted segment list + current cast sheet.
- Character state: persistent `cast.json`, Gemini can propose additions each chapter.
- Asset scope: book-level pool, chapters reference by ID.
- Caching: content-hash cache at every stage (chapter split, segmentation, per-chapter LLM call, per-segment TTS).

## JSON timeline schema (v1)

`chapters/chNN.json`:
```json
{
  "chapter_id": "ch01",
  "title": "The Arrival",
  "commands": [
    { "type": "bg",        "id": "bg_forest_dawn" },
    { "type": "bgm_play",  "id": "bgm_calm_01", "fade_ms": 800 },
    { "type": "char_show", "id": "alice", "expr": "neutral", "slot": "left" },
    { "type": "char_show", "id": "bob",   "expr": "smile",   "slot": "right" },
    { "type": "say",       "speaker": "alice", "text": "...", "voice_clip": "ch01_seg003.ogg" },
    { "type": "se",        "id": "se_door_creak" },
    { "type": "char_hide", "id": "bob" },
    { "type": "bgm_stop",  "fade_ms": 500 }
  ]
}
```

Slots are a fixed enum: `left | middle | right`. Schema lives in `pipeline/schema/timeline.schema.json` and is imported by both Python (jsonschema) and Godot (hand-written validator).

## Pipeline module layout

```
pipeline/
  __main__.py              # `python -m pipeline build path/to/book.txt`
  config.py                # paths, model name, cache dir
  cache.py                 # content-hash keyed cache (stage, input_hash) -> artifact
  chapters.py              # heuristic split + Gemini fallback
  segmenter.py             # sentence split, merge short sentences (target 60-180 chars)
  cast.py                  # load/update cast.json
  llm/
    gemini_client.py       # thin wrapper, retries, JSON-mode
    prompts/chapter_to_timeline.txt
  assets/
    placeholders.py        # generates solid-color BGs, simple char silhouettes, silent audio
    library.py             # future: tagged pool + selection
    image_adapter.py       # stub interface for real image gen
  tts/
    adapter.py             # abstract: synth(text, voice_id) -> ogg bytes
    silent.py              # v1 impl: writes correctly-lengthed silent ogg
  bundle.py                # writes the bundle/ directory structure
  schema/timeline.schema.json
```

## Godot runtime (`godot_player/`)

- Godot 4, GDScript.
- Single scene `Main.tscn` with layered nodes: `Background`, `CharLeft/Middle/Right`, `DialogueBox`, `BGMPlayer`, `SEPlayer`, `VoicePlayer`.
- `BundleLoader.gd`: opens a directory, parses `book.json`, lists chapters.
- `TimelinePlayer.gd`: consumes a chapter's command array, dispatches to handler nodes. Advance on click/space.
- Dev entry: command-line arg `--bundle <path>` for fast iteration from the pipeline.

## Milestones

### M1 — Skeleton + playable end-to-end thin slice *(defines success for v1)*
Goal: one short chapter of text becomes something you can click through in Godot.
- `pipeline/` scaffolding, config, cache stub.
- Chapter splitter: regex only, no LLM yet (input: a single-chapter .txt).
- Segmenter.
- `placeholders.py`: one solid-color BG, two silhouette characters, silent voice clips matching segment length.
- Gemini client with JSON-mode; chapter→timeline prompt; schema validation on output.
- Bundle writer.
- Godot player project: load bundle, play commands, advance on input.
- **Exit test**: `python -m pipeline build samples/short.txt && godot --path godot_player -- --bundle out/short/` → you can read through the generated VN.

### M2 — Full-book ingestion
- Heuristic multi-chapter splitter + Gemini fallback for weird formats.
- Chapter index in `book.json`, chapter-select screen in Godot.
- Content-hash cache wired in at every stage; re-running on the same book is near-free.
- Persistent `cast.json`: extracted per chapter, merged with history, passed back into next chapter's prompt.
- **Exit test**: build a full public-domain novel; re-run is cached; chapter select works.

### M3 — Prompt quality & coherence
- Prompt iteration: better stage directions, consistent slot usage, fewer hallucinated assets.
- Asset-ID discipline: Gemini must only reference IDs from a provided manifest; validator rejects unknown IDs and retries once with the error.
- Expression vocabulary fixed enum (`neutral|smile|sad|angry|surprised|...`).
- Golden-file tests on a few fixed chapters so prompt regressions are visible.

### M4 — Real assets (images)
- `image_adapter.py` implementations: start with one (Imagen or SD via local API).
- Book-level style prompt stored in `book.json` for visual consistency.
- Per-character reference-image pinning so the same character looks the same across chapters.
- Fallback to placeholders on failure; swap is transparent to Godot.

### M5 — Real TTS
- Pick provider (deferred decision). Implement adapter.
- Per-character voice assignment stored in `cast.json`.
- Narrator voice for non-dialogue segments.
- Per-segment TTS cache keyed on `(text, voice_id, provider)`.

### M6 — BGM / SE library
- Tagged royalty-free pool under `assets_library/`.
- Selection step in the pipeline: Gemini picks IDs from the manifest by mood tags per scene.
- Crossfade semantics in Godot (`bgm_play` with `fade_ms`, queue replacement).

### M7 — Polish & UX
- Save/load, backlog, text speed, skip read.
- Packaging: export Godot player as a standalone, bundle+player distributable per book.

## Critical files to create (M1)
- `pipeline/__main__.py`
- `pipeline/chapters.py`
- `pipeline/segmenter.py`
- `pipeline/llm/gemini_client.py`
- `pipeline/llm/prompts/chapter_to_timeline.txt`
- `pipeline/assets/placeholders.py`
- `pipeline/tts/silent.py`
- `pipeline/bundle.py`
- `pipeline/schema/timeline.schema.json`
- `godot_player/project.godot`
- `godot_player/Main.tscn`
- `godot_player/scripts/BundleLoader.gd`
- `godot_player/scripts/TimelinePlayer.gd`
- `samples/short.txt` (a ~1–2k word test chapter)

## Verification (M1 exit)
1. `pip install -r pipeline/requirements.txt`
2. Set `GEMINI_API_KEY`.
3. `python -m pipeline build samples/short.txt -o out/short`
4. Inspect `out/short/chapters/ch01.json` — schema-valid, references only IDs that exist in `out/short/assets/`.
5. `godot --path godot_player -- --bundle out/short` — click through; background shows, characters appear in correct slots, dialogue advances, silent "voice" clips play for the right duration.
6. Re-run step 3 — cache hits on every stage, no Gemini call made.

## Open questions to revisit later (not blocking M1)
- Which real image-gen provider for M4.
- Which TTS provider for M5.
- Whether to support branching/choices (currently linear only).
- Localization of generated output vs. the source language.
