"""In-memory job registry for editor builds.

A single build runs in a worker thread. Events are pushed onto a queue and
drained by the SSE endpoint. Confirmations from the UI resolve
``threading.Event`` objects the worker waits on.
"""
from __future__ import annotations

import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline import api_log, bundle as bundle_mod, cast as cast_mod, config, steps
from pipeline.assets.placeholders import PlaceholderAdapter
from pipeline.build_log import BuildLog


@dataclass
class Job:
    job_id: str
    out_dir: Path
    text: str
    image_gen: str
    skip_confirm: bool = False
    no_cache: bool = False

    events: queue.Queue = field(default_factory=queue.Queue)
    thread: threading.Thread | None = None
    done: bool = False
    error: str | None = None

    # confirmation gates
    desc_gate: threading.Event = field(default_factory=threading.Event)
    baseline_gate: threading.Event = field(default_factory=threading.Event)
    basic_gate: threading.Event = field(default_factory=threading.Event)

    desc_edits: dict | None = None  # {"characters": {...}, "backgrounds": {...}, "display_names": {...}}


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def create(out_dir: Path, text: str, image_gen: str, *,
           skip_confirm: bool = False, no_cache: bool = False) -> Job:
    job = Job(
        job_id=uuid.uuid4().hex[:12],
        out_dir=out_dir,
        text=text,
        image_gen=image_gen,
        skip_confirm=skip_confirm,
        no_cache=no_cache,
    )
    with _LOCK:
        _JOBS[job.job_id] = job
    t = threading.Thread(target=_run, args=(job,), daemon=True)
    job.thread = t
    t.start()
    return job


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def _emit(job: Job, event_type: str, **payload: Any) -> None:
    job.events.put({"type": event_type, "payload": payload})


def _run(job: Job) -> None:
    try:
        api_log.reset()
        out_dir = job.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        log = BuildLog(out_dir)

        _emit(job, "status", message="splitting chapters")
        raw = steps.split_and_segment(job.text)
        _emit(job, "split_done", chapters=[
            {"id": c.id, "title": c.title, "segments": len(s)} for c, s in raw
        ])

        _emit(job, "status", message="calling Gemini for timelines")
        entries, cast = steps.generate_timelines(raw, log=log, no_cache=job.no_cache)

        # Merge existing on-disk descriptions/names so rerunning against an
        # existing bundle dir preserves prior edits.
        existing_cast = cast_mod.load(out_dir).get("cast", {})
        fresh_roster = cast.get("cast", {})
        for cid, meta in fresh_roster.items():
            prior = existing_cast.get(cid, {})
            if prior.get("visual_description"):
                meta["visual_description"] = prior["visual_description"]
            if prior.get("display_name") and prior["display_name"] != cid:
                meta["display_name"] = prior["display_name"]
        _emit(job, "timelines_done", chapters=[
            {"id": c.id, "commands": len(t.get("commands", []))}
            for c, _s, t in entries
        ])

        book_title = raw[0][0].title if len(raw) == 1 else f"build-{job.job_id}"

        char_descs: dict[str, str] = {}
        bg_descs: dict[str, str] = {}

        if job.image_gen == "nanobanana":
            bg_ids, char_exprs = steps.collect_manifests(entries)
            major, minor = steps.classify_chars(cast, char_exprs)
            _emit(job, "manifests", bg_ids=sorted(bg_ids),
                  major_chars=major, minor_chars=minor,
                  char_exprs={c: sorted(v) for c, v in char_exprs.items()})

            _emit(job, "status", message="proposing descriptions via Gemini")
            proposed = steps.propose_missing_descriptions(
                out_dir, entries, cast, book_title, major, bg_ids,
            )
            _emit(job, "descriptions_proposed", **proposed)

            if job.skip_confirm:
                job.desc_edits = {
                    "characters": proposed["characters"],
                    "display_names": proposed["display_names"],
                    "backgrounds": proposed["backgrounds"],
                }
            else:
                _emit(job, "awaiting_confirm", step="descriptions")
                job.desc_gate.wait()

            edits = job.desc_edits or {}
            char_descs = edits.get("characters", proposed["characters"])
            bg_descs = edits.get("backgrounds", proposed["backgrounds"])
            display_names = edits.get("display_names", proposed["display_names"])

            cast = steps.save_descriptions(
                out_dir, cast, char_descs, bg_descs, display_names,
            )

            def _on_matte_done(path: Path) -> None:
                _emit(job, "asset_updated", path=str(path),
                      mtime=path.stat().st_mtime if path.exists() else 0)

            adapter = steps._nano_adapter(defer_matte=True, on_matte_done=_on_matte_done)

            # Phase A: baseline
            _emit(job, "status", message="generating baseline")
            bpath = steps.gen_baseline(out_dir, adapter)
            _emit(job, "asset_written", kind="baseline", path=str(bpath))
            if major:
                if job.skip_confirm:
                    pass
                else:
                    _emit(job, "awaiting_confirm", step="baseline")
                    job.baseline_gate.wait()

            # Phase B: basic chars
            for cid in major:
                _emit(job, "status", message=f"generating {cid} neutral")
                p = steps.gen_basic_char(out_dir, cid, char_descs.get(cid, ""), adapter=adapter)
                _emit(job, "asset_written", kind="char_neutral", id=cid, path=str(p))

            if major:
                if job.skip_confirm:
                    pass
                else:
                    _emit(job, "awaiting_confirm", step="basic_chars")
                    job.basic_gate.wait()

            # Phase C: expressions
            for cid in major:
                exprs = sorted(char_exprs.get(cid, set()) - {"neutral"})
                for expr in exprs:
                    _emit(job, "status", message=f"generating {cid}/{expr}")
                    p = steps.gen_expression(
                        out_dir, cid, expr, char_descs.get(cid, ""), adapter=adapter,
                    )
                    _emit(job, "asset_written", kind="char_expr",
                          id=cid, expr=expr, path=str(p))

            # Backgrounds
            for bg_id in sorted(bg_ids):
                _emit(job, "status", message=f"generating bg {bg_id}")
                p = steps.gen_bg(out_dir, bg_id, bg_descs.get(bg_id, ""), adapter=adapter)
                _emit(job, "asset_written", kind="bg", id=bg_id, path=str(p))

            _emit(job, "status", message="waiting for background removal to finish")
            adapter.drain_matte()

            image_adapter = PlaceholderAdapter()
        else:
            image_adapter = PlaceholderAdapter()

        _emit(job, "status", message="writing bundle")
        steps.write_bundle(
            out_dir, entries, cast,
            book_title=book_title,
            image_adapter=image_adapter,
            char_descs=char_descs,
            bg_descs=bg_descs,
        )
        log.summary(len(raw), sum(len(s) for _c, s in raw))
        api_log.current.write(out_dir)
        _emit(job, "done", out_dir=str(out_dir.resolve()))
    except Exception as exc:  # noqa: BLE001
        job.error = str(exc)
        tb = traceback.format_exc()
        _emit(job, "step_error", message=str(exc), traceback=tb)
    finally:
        job.done = True
        job.events.put(None)  # sentinel


def resolve_confirm(job: Job, step: str, edits: dict | None = None) -> None:
    if step == "descriptions":
        if edits:
            job.desc_edits = edits
        job.desc_gate.set()
    elif step == "baseline":
        job.baseline_gate.set()
    elif step == "basic_chars":
        job.basic_gate.set()
    else:
        raise ValueError(f"unknown confirm step: {step}")
