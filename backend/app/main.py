"""API HTTP du Monteur IA."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, pipeline, storage
from .jobs import manager
from .media import ffmpeg
from .models import EditSettings, Project, Timeline
from .render import captions as cap
from .render import effects as fx
from .render import render as render_mod
from .render import xml_export

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Monteur IA", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts", ".mp3", ".wav", ".m4a", ".flac"}


def _project(pid: str) -> Project:
    try:
        return storage.load_project(pid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Projet introuvable")


def _safe_name(name: str) -> str:
    base = Path(name or "fichier").name
    return "".join(ch for ch in base if ch.isalnum() or ch in "._- ()").strip() or "fichier"


async def _save_upload(f: UploadFile, dst: Path) -> None:
    with dst.open("wb") as out:
        while chunk := await f.read(8 * 1024 * 1024):
            out.write(chunk)


# --- Santé ------------------------------------------------------------------

@app.get("/api/health")
def health():
    enc, _ = ffmpeg.best_h264_encoder()
    return {
        "ok": True,
        "claude": config.claude_available(),
        "claude_model": config.CLAUDE_MODEL,
        "whisper_model": config.WHISPER_MODEL,
        "encoder": enc,
        "music_library": len([p for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in pipeline.MUSIC_EXT]),
    }


# --- Projets ------------------------------------------------------------------

class NewProject(BaseModel):
    name: str = "Nouveau projet"


class ProjectPatch(BaseModel):
    name: str | None = None
    settings: EditSettings | None = None


@app.get("/api/projects")
def list_projects():
    return [
        {"id": p.id, "name": p.name, "updated_at": p.updated_at, "media": len(p.media),
         "duration": p.timeline.duration, "thumb": p.media[0].id if p.media else None}
        for p in storage.list_projects()
    ]


@app.post("/api/projects")
def create_project(body: NewProject):
    return storage.save_project(Project(name=body.name.strip() or "Nouveau projet"))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _project(pid)


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    p = _project(pid)
    if body.name is not None:
        p.name = body.name.strip() or p.name
    if body.settings is not None:
        p.settings = body.settings
    return storage.save_project(p)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _project(pid)
    storage.delete_project(pid)
    return {"ok": True}


# --- Médias -------------------------------------------------------------------

@app.post("/api/projects/{pid}/media")
async def upload_media(pid: str, files: list[UploadFile] = File(...)):
    p = _project(pid)
    added = []
    for f in files:
        name = _safe_name(f.filename or "video.mp4")
        if Path(name).suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, f"Format non pris en charge : {name}")
        dst = storage.media_dir(pid) / name
        i = 1
        while dst.exists():
            dst = storage.media_dir(pid) / f"{Path(name).stem} ({i}){Path(name).suffix}"
            i += 1
        await _save_upload(f, dst)
        try:
            added.append(pipeline.ingest(p, dst, dst.name))
        except Exception as e:
            dst.unlink(missing_ok=True)
            raise HTTPException(400, str(e))
    storage.save_project(p)
    return added


@app.delete("/api/projects/{pid}/media/{mid}")
def delete_media(pid: str, mid: str):
    p = _project(pid)
    try:
        m = p.media_by_id(mid)
    except KeyError:
        raise HTTPException(404, "Média introuvable")
    p.media.remove(m)
    p.timeline.clips = [c for c in p.timeline.clips if c.media_id != mid]
    Path(m.path).unlink(missing_ok=True)
    for f in storage.cache_dir(pid).glob(f"{mid}.*"):
        f.unlink(missing_ok=True)
    return storage.save_project(p)


@app.get("/api/projects/{pid}/media/{mid}/proxy")
def media_proxy(pid: str, mid: str):
    p = _project(pid)
    try:
        m = p.media_by_id(mid)
    except KeyError:
        raise HTTPException(404, "Média introuvable")
    path = m.proxy if m.proxy and Path(m.proxy).exists() else m.path
    return FileResponse(path, media_type="video/mp4" if path.endswith(".mp4") else None)


@app.get("/api/projects/{pid}/media/{mid}/thumb")
def media_thumb(pid: str, mid: str):
    path = storage.cache_dir(pid) / f"{mid}.jpg"
    if not path.exists():
        raise HTTPException(404, "Miniature pas encore prête")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/projects/{pid}/media/{mid}/signals")
def media_signals(pid: str, mid: str):
    s = storage.load_signals(pid, mid)
    if s is None:
        raise HTTPException(404, "Analyse pas encore faite")
    a = s.get("audio") or {}
    return {"hop": a.get("hop", 0.05), "envelope": a.get("envelope", []), "silences": a.get("silences", []),
            "cuts": (s.get("video") or {}).get("cuts", []), "faces": s.get("faces") or None}


# --- Montage IA -----------------------------------------------------------------

@app.post("/api/projects/{pid}/auto-edit")
def auto_edit(pid: str, settings: EditSettings):
    _project(pid)
    job = manager.submit(pid, "montage", lambda ctx: pipeline.auto_edit(pid, settings, ctx))
    return job


class Command(BaseModel):
    instruction: str


@app.post("/api/projects/{pid}/command")
def command(pid: str, body: Command):
    p = _project(pid)
    if not p.timeline.clips and not storage.load_segments(pid):
        raise HTTPException(400, "Lance d'abord un montage IA.")
    if not body.instruction.strip():
        raise HTTPException(400, "Consigne vide.")
    return manager.submit(pid, "consigne", lambda ctx: pipeline.ai_command(pid, body.instruction.strip(), ctx))


@app.put("/api/projects/{pid}/timeline")
def put_timeline(pid: str, timeline: Timeline):
    p = _project(pid)
    ids = {m.id for m in p.media}
    for c in timeline.clips:
        if c.media_id not in ids:
            raise HTTPException(400, f"Clip {c.id} : média inconnu")
        m = p.media_by_id(c.media_id)
        c.src_in = max(0.0, min(c.src_in, m.duration))
        c.src_out = max(c.src_in + 0.05, min(c.src_out, m.duration))
    p.timeline = timeline
    storage.save_project(p)
    return p.timeline


@app.get("/api/projects/{pid}/segments")
def get_segments(pid: str):
    _project(pid)
    return storage.load_segments(pid)


@app.get("/api/projects/{pid}/transcripts")
def get_transcripts(pid: str):
    p = _project(pid)
    return {mid: t.model_dump() for mid, t in pipeline.transcripts_for(p).items()}


class WordEdit(BaseModel):
    text: str


@app.patch("/api/projects/{pid}/transcripts/{mid}/words/{idx}")
def edit_word(pid: str, mid: str, idx: int, body: WordEdit):
    _project(pid)
    t = storage.load_transcript(pid, mid)
    if not t or not 0 <= idx < len(t.words):
        raise HTTPException(404, "Mot introuvable")
    t.words[idx].text = body.text.strip() or t.words[idx].text
    storage.save_transcript(pid, t)
    return t.words[idx]


@app.get("/api/projects/{pid}/captions")
def get_captions(pid: str):
    p = _project(pid)
    if p.timeline.captions == "aucun":
        return []
    return [{"start": ln.start, "end": ln.end, "words": [w.model_dump() for w in ln.words]}
            for ln in cap.build_lines(p.timeline, pipeline.transcripts_for(p))]


# --- Effets ---------------------------------------------------------------------------

@app.get("/api/effects")
def effects_catalog():
    return fx.catalog()


@app.get("/api/sfx/{name}")
def sfx_file(name: str):
    files = fx.ensure_sfx()
    if name not in files:
        raise HTTPException(404, "Bruitage inconnu")
    return FileResponse(files[name], media_type="audio/wav")


# --- Musique -----------------------------------------------------------------------

@app.get("/api/music")
def music_library():
    return sorted(p.name for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in pipeline.MUSIC_EXT)


@app.post("/api/projects/{pid}/music")
async def upload_music(pid: str, file: UploadFile = File(...)):
    p = _project(pid)
    name = _safe_name(file.filename or "musique.mp3")
    if Path(name).suffix.lower() not in pipeline.MUSIC_EXT:
        raise HTTPException(400, "Format audio non pris en charge")
    d = storage.project_dir(pid) / "music"
    d.mkdir(exist_ok=True)
    dst = d / name
    await _save_upload(file, dst)
    p.timeline.music.track = str(dst.resolve())
    p.settings.music = True
    storage.save_project(p)
    return p.timeline.music


@app.get("/api/projects/{pid}/music/current")
def current_music(pid: str):
    p = _project(pid)
    t = p.timeline.music.track
    if not t or not Path(t).exists():
        raise HTTPException(404, "Pas de musique")
    return FileResponse(t)


# --- Export ----------------------------------------------------------------------------

class ExportReq(BaseModel):
    quality: str = "1080p"
    fps: int | None = None  # None = comme la source
    smooth: bool = False  # interpolation de mouvement quand la source a moins d'images


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: ExportReq):
    p = _project(pid)
    if not p.timeline.clips:
        raise HTTPException(400, "La timeline est vide.")
    if body.quality not in render_mod.QUALITIES:
        raise HTTPException(400, "Qualité inconnue")
    if body.fps is not None and body.fps not in render_mod.FPS_CHOICES:
        raise HTTPException(400, f"Cadence non prise en charge (choix : {', '.join(map(str, render_mod.FPS_CHOICES))})")
    return manager.submit(pid, "export", lambda ctx: pipeline.export(pid, body.quality, ctx, body.fps, body.smooth))


@app.post("/api/projects/{pid}/publish-kit")
def publish_kit(pid: str):
    p = _project(pid)
    if not p.timeline.clips:
        raise HTTPException(400, "La timeline est vide.")
    return manager.submit(pid, "publication", lambda ctx: pipeline.publish_kit(pid, ctx))


def _download_name(p: Project, ext: str) -> str:
    safe = "".join(ch for ch in (p.timeline.title or p.name) if ch.isalnum() or ch in " -_").strip()[:50] or "montage"
    return f"{safe}.{ext}"


@app.get("/api/projects/{pid}/export.xml")
def export_xml(pid: str):
    """Montage au format XML (Premiere Pro, DaVinci Resolve, Final Cut Pro) pour finir à la main."""
    p = _project(pid)
    if not p.timeline.clips:
        raise HTTPException(400, "La timeline est vide.")
    return Response(xml_export.to_xmeml(p, p.timeline), media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name(p, "xml")}"'})


@app.get("/api/projects/{pid}/captions.srt")
def captions_srt(pid: str):
    p = _project(pid)
    srt = cap.to_srt(cap.build_lines(p.timeline, pipeline.transcripts_for(p)))
    return Response(srt, media_type="application/x-subrip",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name(p, "srt")}"'})


@app.get("/api/projects/{pid}/exports/{name}")
def get_export(pid: str, name: str):
    path = storage.exports_dir(pid) / Path(name).name
    if not path.exists():
        raise HTTPException(404, "Export introuvable")
    return FileResponse(path, filename=path.name)


# --- Tâches -----------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Tâche introuvable")
    return job


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str):
    return [j for j in manager.for_project(pid) if j.status in ("en_attente", "en_cours")]


# --- Interface web ------------------------------------------------------------------

if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.FRONTEND_DIST / path
        if path and f.is_file() and config.FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
