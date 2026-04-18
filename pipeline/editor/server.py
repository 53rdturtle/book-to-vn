"""FastAPI server for the book-to-vn assets editor."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline import backgrounds as bg_mod, cast as cast_mod, config, steps
from pipeline.editor import jobs


_STATIC = Path(__file__).resolve().parent / "static"
_START_TIME = __import__("time").time()

app = FastAPI(title="book-to-vn editor")


@app.get("/api/health")
async def api_health():
    return {"ok": True, "start_time": _START_TIME}


@app.get("/api/backends")
async def api_backends():
    from pipeline.assets import registry
    return {
        "backends": registry.available(),
        "qualities": ["low", "medium", "high"],
        "quality_default": config.OPENAI_IMAGE_QUALITY,
        "quality_backends": ["openai"],
    }


# ---------- helpers ----------

def _resolve_bundle(path_str: str) -> Path:
    p = Path(path_str).expanduser().resolve()
    return p


def _safe_asset(bundle: Path, rel: str) -> Path:
    """Ensure a requested file is inside the bundle dir."""
    target = (bundle / rel).resolve()
    try:
        target.relative_to(bundle.resolve())
    except ValueError:
        raise HTTPException(400, "path traversal rejected")
    return target


def _list_bundle(bundle: Path) -> dict:
    if not bundle.exists():
        return {"exists": False, "path": str(bundle)}

    book_json = bundle / config.BUNDLE_BOOK_JSON
    book = json.loads(book_json.read_text(encoding="utf-8")) if book_json.exists() else None
    cast = cast_mod.load(bundle)
    bgs = bg_mod.load(bundle)

    # enumerate assets
    assets = {"bg": [], "char": {}, "baseline": None}
    bg_dir = bundle / config.BUNDLE_BG_DIR
    if bg_dir.exists():
        for p in sorted(bg_dir.glob("*.png")):
            assets["bg"].append({
                "id": p.stem,
                "path": str(p.relative_to(bundle)).replace("\\", "/"),
                "mtime": p.stat().st_mtime,
            })
    char_dir = bundle / config.BUNDLE_CHAR_DIR
    if char_dir.exists():
        for cdir in sorted(p for p in char_dir.iterdir() if p.is_dir()):
            entries = []
            for img in sorted(cdir.glob("*.png")):
                entries.append({
                    "expr": img.stem,
                    "path": str(img.relative_to(bundle)).replace("\\", "/"),
                    "mtime": img.stat().st_mtime,
                })
            if cdir.name == "_baseline":
                if entries:
                    assets["baseline"] = entries[0]
            else:
                assets["char"][cdir.name] = entries

    return {
        "exists": True,
        "path": str(bundle),
        "book": book,
        "cast": cast,
        "backgrounds": bgs,
        "assets": assets,
    }


# ---------- API ----------

@app.get("/api/bundle")
def api_bundle(path: str):
    bundle = _resolve_bundle(path)
    return _list_bundle(bundle)


@app.get("/api/asset")
def api_asset(bundle: str, rel: str):
    b = _resolve_bundle(bundle)
    target = _safe_asset(b, rel)
    if not target.exists():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.post("/api/start")
async def api_start(req: Request):
    body = await req.json()
    text = body.get("text", "")
    out_dir = _resolve_bundle(body.get("out_dir", ""))
    image_gen = body.get("image_gen", "placeholder")
    image_quality = body.get("image_quality") or None
    skip_confirm = bool(body.get("skip_confirm", False))
    no_cache = bool(body.get("no_cache", False))
    if not text.strip():
        raise HTTPException(400, "text is required")
    if not out_dir:
        raise HTTPException(400, "out_dir is required")
    job = jobs.create(out_dir, text, image_gen,
                      image_quality=image_quality,
                      skip_confirm=skip_confirm, no_cache=no_cache)
    return {"job_id": job.job_id}


@app.get("/api/events/{job_id}")
async def api_events(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")

    async def gen():
        loop = asyncio.get_event_loop()
        while True:
            evt = await loop.run_in_executor(None, job.events.get)
            if evt is None:
                yield "event: end\ndata: {}\n\n"
                return
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/confirm/{job_id}")
async def api_confirm(job_id: str, req: Request):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    body = await req.json()
    step = body.get("step", "")
    edits = body.get("edits")
    try:
        jobs.resolve_confirm(job, step, edits)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/propose_visual_style")
async def api_propose_visual_style(req: Request):
    body = await req.json()
    bundle = _resolve_bundle(body.get("bundle", ""))
    if not bundle.exists():
        raise HTTPException(404, "bundle not found")

    book_json = bundle / config.BUNDLE_BOOK_JSON
    book = json.loads(book_json.read_text(encoding="utf-8")) if book_json.exists() else {}
    title = book.get("title", "Untitled")

    # collect a short excerpt from the first chapter's say commands
    excerpt_lines: list[str] = []
    chapters_dir = bundle / "chapters"
    if chapters_dir.exists():
        for cp in sorted(chapters_dir.glob("*.json")):
            try:
                ch = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for cmd in ch.get("commands", []):
                if cmd.get("type") == "say" and cmd.get("text"):
                    excerpt_lines.append(cmd["text"])
                    if len(excerpt_lines) >= 10:
                        break
            if len(excerpt_lines) >= 10:
                break
    excerpt = "\n".join(excerpt_lines)

    from pipeline.llm import visual_descriptions
    proposed = visual_descriptions.propose(
        book_title=title, char_ids=[], bg_ids=[], excerpt=excerpt, minor_char_ids=[],
    )
    return {"visual_style": proposed.get("visual_style", "")}


@app.post("/api/regenerate")
async def api_regenerate(req: Request):
    body = await req.json()
    bundle = _resolve_bundle(body.get("bundle", ""))
    kind = body.get("kind", "")
    cid = body.get("id")
    expr = body.get("expr")
    description = body.get("description")
    visual_style = body.get("visual_style")
    cascade = bool(body.get("cascade", False))

    if not bundle.exists():
        raise HTTPException(404, "bundle not found")

    # apply description edits first (persist them)
    cast = cast_mod.load(bundle)
    bgs = bg_mod.load(bundle)
    if description is not None:
        if kind in ("char_neutral", "char_expr") and cid:
            cast = cast_mod.set_visual_description(cast, cid, description)
            cast_mod.save(bundle, cast)
        elif kind == "bg" and cid:
            bgs = bg_mod.set_visual_description(bgs, cid, description)
            bg_mod.save(bundle, bgs)
    if visual_style is not None:
        bgs = bg_mod.set_visual_style(bgs, visual_style)
        bg_mod.save(bundle, bgs)

    regenerated: list[str] = []

    def _make_adapter():
        style = bg_mod.get_visual_style(bgs) or None
        backend = bg_mod.get_image_gen(bgs) or "nanobanana"
        quality = bg_mod.get_image_quality(bgs) or None
        return steps.image_adapter(backend, style=style, quality=quality)

    if kind == "baseline":
        adapter = _make_adapter()
        if cascade:
            char_dir = bundle / config.BUNDLE_CHAR_DIR
            if char_dir.exists():
                for cdir in char_dir.iterdir():
                    if cdir.is_dir() and cdir.name != "_baseline":
                        shutil.rmtree(cdir)
        p = steps.gen_baseline(bundle, adapter=adapter, force=True)
        regenerated.append(str(p))
        if cascade:
            roster = cast.get("cast", {})
            major = [c for c, m in roster.items()
                     if m.get("appearance_count", 0) >= config.MAJOR_CHAR_MIN_APPEARANCES]
            for mc in major:
                desc = roster.get(mc, {}).get("visual_description", "")
                regenerated.append(str(steps.gen_basic_char(bundle, mc, desc, adapter=adapter, force=True)))
    elif kind == "char_neutral":
        if not cid:
            raise HTTPException(400, "id required")
        if cascade:
            cdir = bundle / config.BUNDLE_CHAR_DIR / cid
            if cdir.exists():
                for p in cdir.glob("*.png"):
                    if p.stem != "neutral":
                        p.unlink()
        desc = cast.get("cast", {}).get(cid, {}).get("visual_description", "")
        p = steps.gen_basic_char(bundle, cid, desc, adapter=_make_adapter(), force=True)
        regenerated.append(str(p))
    elif kind == "char_expr":
        if not cid or not expr:
            raise HTTPException(400, "id and expr required")
        desc = cast.get("cast", {}).get(cid, {}).get("visual_description", "")
        p = steps.gen_expression(bundle, cid, expr, desc, adapter=_make_adapter(), force=True)
        regenerated.append(str(p))
    elif kind == "bg":
        if not cid:
            raise HTTPException(400, "id required")
        desc = bg_mod.get_visual_description(bgs, cid)
        p = steps.gen_bg(bundle, cid, desc, adapter=_make_adapter(), force=True)
        regenerated.append(str(p))
    else:
        raise HTTPException(400, f"unknown kind: {kind}")

    return {"regenerated": regenerated}


@app.post("/api/upload")
async def api_upload(
    bundle: str = Form(...),
    kind: str = Form(...),
    id: str = Form(""),
    expr: str = Form(""),
    file: UploadFile = File(...),
):
    b = _resolve_bundle(bundle)
    if not b.exists():
        raise HTTPException(404, "bundle not found")
    if kind == "baseline":
        target = steps.baseline_path(b)
    elif kind in ("char_neutral", "char_expr"):
        if not id:
            raise HTTPException(400, "id required")
        e = expr or "neutral"
        target = steps.char_path(b, id, e)
    elif kind == "bg":
        if not id:
            raise HTTPException(400, "id required")
        target = steps.bg_path(b, id)
    else:
        raise HTTPException(400, f"unknown kind: {kind}")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    target.write_bytes(content)
    return {"path": str(target.relative_to(b)).replace("\\", "/"), "mtime": target.stat().st_mtime}


_PLAY_PROCS: dict[str, subprocess.Popen] = {}


@app.post("/api/play")
async def api_play(req: Request):
    body = await req.json()
    b = _resolve_bundle(body.get("bundle", ""))
    if not b.exists():
        raise HTTPException(404, "bundle not found")
    try:
        proc = steps.launch_godot(b)
    except Exception as e:
        raise HTTPException(500, str(e))
    _PLAY_PROCS[str(b)] = proc
    return {"pid": proc.pid}


@app.get("/api/play/status")
def api_play_status(bundle: str):
    b = str(_resolve_bundle(bundle))
    proc = _PLAY_PROCS.get(b)
    if not proc:
        return {"running": False}
    rc = proc.poll()
    return {"running": rc is None, "exit_code": rc}


def _deferred_exit(*, restart: bool = False) -> None:
    """Shut down (and optionally restart) after a short delay so the HTTP response can flush."""
    import sys
    import time
    import threading

    def _worker():
        time.sleep(0.5)
        if restart:
            subprocess.Popen(
                [sys.executable, "-m", "pipeline", "editor"],
                cwd=Path.cwd(),
            )
        os._exit(0)

    threading.Thread(target=_worker, daemon=True).start()


@app.post("/api/shutdown")
async def api_shutdown():
    _deferred_exit(restart=False)
    return {"ok": True}


@app.post("/api/restart")
async def api_restart():
    _deferred_exit(restart=True)
    return {"ok": True}


# ---------- static ----------

@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")


def run(port: int = None, open_browser: bool = True) -> None:
    import uvicorn
    import webbrowser
    import threading

    port = port or config.EDITOR_PORT
    if open_browser:
        url = f"http://127.0.0.1:{port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
