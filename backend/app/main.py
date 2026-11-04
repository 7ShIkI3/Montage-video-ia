"""API HTTP du Monteur IA."""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from pathlib import Path

import anyio
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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

_PROJECT_ROUTE = re.compile(r"^/api/projects/([A-Za-z0-9_]+)(/.*)?$")
_UPLOADS = re.compile(r"^/(media|music|assets|audio|luts|reference)$")  # envois de fichiers : verrou pris seulement à l'écriture


@app.middleware("http")
async def serialize_project_writes(request: Request, call_next):
    """Une modification de projet à la fois : évite qu'une requête écrase ce qu'une autre vient d'écrire."""
    m = _PROJECT_ROUTE.match(request.url.path)
    if request.method in ("GET", "HEAD", "OPTIONS") or not m or (request.method == "POST" and _UPLOADS.match(m.group(2) or "")):
        return await call_next(request)
    lock = storage.project_lock(m.group(1))
    await anyio.to_thread.run_sync(lock.acquire)
    try:
        return await call_next(request)
    finally:
        lock.release()

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
    _project(pid)
    saved: list[Path] = []
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
        saved.append(dst)

    def ingest_all() -> list:
        added = []

        def apply(p: Project) -> None:
            for dst in saved:
                try:
                    added.append(pipeline.ingest(p, dst, dst.name))
                except Exception as e:
                    for d in saved:
                        d.unlink(missing_ok=True)
                    raise HTTPException(400, str(e))
        storage.update_project(pid, apply)
        return added
    return await anyio.to_thread.run_sync(ingest_all)


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


@app.post("/api/projects/{pid}/reference")
async def upload_reference(pid: str, file: UploadFile = File(...)):
    """Vidéo de référence : l'IA en copie le rythme (durée des plans, accélération, fondus) au prochain montage."""
    from .ai import reference as ref_mod
    _project(pid)
    name = _safe_name(file.filename or "reference.mp4")
    if Path(name).suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Vidéo attendue (MP4, MOV, MKV, WebM…).")
    d = storage.project_dir(pid) / "reference"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        old.unlink(missing_ok=True)
    dst = d / name
    await _save_upload(file, dst)
    try:
        prof = await anyio.to_thread.run_sync(ref_mod.analyze, dst)
    except Exception as e:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"Vidéo de référence illisible : {e}")
    prof["filename"] = name
    prof["summary"] = ref_mod.describe(prof)
    await anyio.to_thread.run_sync(lambda: storage.update_project(pid, lambda p: setattr(p, "reference", prof)))
    return prof


@app.delete("/api/projects/{pid}/reference")
def delete_reference(pid: str):
    p = _project(pid)  # requête déjà sous le verrou du projet (middleware)
    shutil.rmtree(storage.project_dir(pid) / "reference", ignore_errors=True)
    p.reference = None
    storage.save_project(p)
    return {"ok": True}


class DirectionsReq(BaseModel):
    platform: str = "auto"  # auto, youtube, tiktok, linkedin, instagram
    tone: str = "auto"  # auto, fun, pro, emotion, pedago
    audience: str = ""


@app.post("/api/projects/{pid}/directions")
def directions(pid: str, body: DirectionsReq):
    """Propose 3 directions créatives avant le montage (questions facultatives : plateforme, ton, public)."""
    _project(pid)
    return manager.submit(pid, "directions", lambda ctx: pipeline.propose_directions(
        pid, body.platform, body.tone, body.audience[:200], ctx))


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


@app.post("/api/projects/{pid}/comments/apply")
def apply_comments(pid: str):
    p = _project(pid)
    if not any(c.status == "ouvert" for c in p.timeline.comments):
        raise HTTPException(400, "Aucun commentaire en attente.")
    return manager.submit(pid, "commentaires", lambda ctx: pipeline.apply_comments(pid, ctx))


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
    if timeline.music.track and not storage.safe_music_path(timeline.music.track):
        raise HTTPException(400, "Musique refusée : choisis-la dans la bibliothèque ou importe-la.")
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
    tr = storage.load_translations(pid, p.timeline.caption_language)
    return [{"start": ln.start, "end": ln.end, "words": [w.model_dump() for w in ln.words]}
            for ln in cap.build_lines(p.timeline, pipeline.transcripts_for(p), tr)]


class TranslateReq(BaseModel):
    language: str | None = None  # None = langue parlée


@app.get("/api/languages")
def languages():
    from .ai.translate import LANGUAGES
    return [{"code": k, "name": v} for k, v in LANGUAGES.items()]


@app.post("/api/projects/{pid}/translate")
def translate_captions(pid: str, body: TranslateReq):
    from .ai.translate import LANGUAGES
    _project(pid)
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(400, "Langue inconnue")
    return manager.submit(pid, "traduction", lambda ctx: pipeline.translate_captions(pid, body.language, ctx))


class DubReq(BaseModel):
    language: str | None = None
    original_volume: float = 0.12


@app.get("/api/tts")
def tts_status():
    """Moteur de voix de synthèse disponible par langue (piper, espeak ou rien)."""
    from .ai import tts
    return tts.status()


@app.post("/api/projects/{pid}/dub")
def dub(pid: str, body: DubReq):
    from .ai.translate import LANGUAGES
    _project(pid)
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(400, "Langue inconnue")
    vol = max(0.0, min(1.0, body.original_volume))
    return manager.submit(pid, "doublage", lambda ctx: pipeline.dub_voice(pid, body.language, ctx, vol))


# --- Pistes audio (voix off, sons) ------------------------------------------------------

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm"}


@app.post("/api/projects/{pid}/audio")
async def upload_audio(pid: str, file: UploadFile = File(...)):
    """Envoie un son (voix off enregistrée dans le navigateur, bruitage, musique) pour les pistes audio."""
    _project(pid)
    name = _safe_name(file.filename or "voix.webm")
    if Path(name).suffix.lower() not in AUDIO_EXT:
        raise HTTPException(400, "Fichier audio attendu (MP3, WAV, M4A, OGG, WebM…).")
    d = storage.audio_dir(pid)
    dst = d / name
    i = 1
    while dst.exists():
        dst = d / f"{Path(name).stem} ({i}){Path(name).suffix}"
        i += 1
    await _save_upload(file, dst)
    if dst.suffix.lower() == ".webm":
        # Les enregistrements du navigateur n'ont souvent pas de durée dans l'en-tête : on les convertit.
        wav = dst.with_suffix(".wav")
        try:
            await anyio.to_thread.run_sync(lambda: ffmpeg.run(
                ["-i", str(dst), "-vn", "-ac", "2", "-ar", "48000", str(wav)]))
        except Exception as e:
            dst.unlink(missing_ok=True)
            raise HTTPException(400, f"Enregistrement illisible : {e}")
        dst.unlink(missing_ok=True)
        dst = wav
    try:
        dur = (await anyio.to_thread.run_sync(ffmpeg.probe, dst)).get("duration") or 0.0
    except Exception:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, "Fichier audio illisible.")
    return {"asset": dst.name, "duration": round(float(dur), 3)}


@app.get("/api/projects/{pid}/audio/{name}")
def get_audio(pid: str, name: str):
    path = storage.audio_dir(pid) / Path(name).name
    if not path.exists():
        raise HTTPException(404, "Son introuvable")
    return FileResponse(path)


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

    def apply(p: Project) -> None:
        p.timeline.music.track = str(dst.resolve())
        p.settings.music = True
    p = await anyio.to_thread.run_sync(storage.update_project, pid, apply)
    return p.timeline.music


@app.get("/api/projects/{pid}/music/current")
def current_music(pid: str):
    p = _project(pid)
    t = storage.safe_music_path(p.timeline.music.track)
    if not t or not Path(t).exists():
        raise HTTPException(404, "Pas de musique")
    return FileResponse(t)


# --- Export ----------------------------------------------------------------------------

class ExportReq(BaseModel):
    quality: str = "1080p"
    fps: int | None = None  # None = comme la source
    smooth: bool = False  # interpolation de mouvement quand la source a moins d'images
    container: str = "mp4"  # mp4, webm, mov (ProRes) ou gif


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: ExportReq):
    p = _project(pid)
    if not p.timeline.clips:
        raise HTTPException(400, "La timeline est vide.")
    if body.quality not in render_mod.QUALITIES:
        raise HTTPException(400, "Qualité inconnue")
    if body.fps is not None and body.fps not in render_mod.FPS_CHOICES:
        raise HTTPException(400, f"Cadence non prise en charge (choix : {', '.join(map(str, render_mod.FPS_CHOICES))})")
    if body.container not in (*render_mod.CONTAINERS, "gif"):
        raise HTTPException(400, "Format de fichier inconnu")
    return manager.submit(pid, "export", lambda ctx: pipeline.export(pid, body.quality, ctx, body.fps, body.smooth,
                                                                     body.container))


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
    tr = storage.load_translations(pid, p.timeline.caption_language)
    srt = cap.to_srt(cap.build_lines(p.timeline, pipeline.transcripts_for(p), tr))
    return Response(srt, media_type="application/x-subrip",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name(p, "srt")}"'})


@app.get("/api/projects/{pid}/exports/{name}")
def get_export(pid: str, name: str):
    path = storage.exports_dir(pid) / Path(name).name
    if not path.exists():
        raise HTTPException(404, "Export introuvable")
    return FileResponse(path, filename=path.name)


# --- Préréglages par réseau et file d'export -------------------------------------------

@app.get("/api/export-presets")
def export_presets():
    return [{"id": k, **v} for k, v in pipeline.PRESETS.items()]


class PresetExportReq(BaseModel):
    preset: str
    versions: list[str] | None = None  # None = version active ; liste = file d'export


@app.post("/api/projects/{pid}/export-preset")
def export_with_preset(pid: str, body: PresetExportReq):
    p = _project(pid)
    if body.preset not in pipeline.PRESETS:
        raise HTTPException(400, "Préréglage inconnu")
    known = {v.id for v in p.versions}
    if body.versions and not set(body.versions) <= known:
        raise HTTPException(400, "Version inconnue")
    return manager.submit(pid, "export", lambda ctx: pipeline.export_preset(pid, body.preset, ctx, body.versions))


# --- Versions du montage et shorts automatiques ----------------------------------------

class VersionReq(BaseModel):
    name: str | None = None


def _versions_view(p: Project) -> dict:
    p.ensure_versions()
    return {"active": p.active_version, "versions": [
        {"id": v.id, "name": v.name, "score": v.score, "note": v.note, "created_at": v.created_at,
         "duration": round((p.timeline if v.id == p.active_version else (v.timeline or Timeline())).duration, 2)}
        for v in p.versions]}


@app.get("/api/projects/{pid}/versions")
def list_versions(pid: str):
    return _versions_view(_project(pid))


@app.post("/api/projects/{pid}/versions")
def duplicate_version(pid: str, body: VersionReq):
    p = _project(pid)
    pipeline.version_duplicate(p, body.name)
    storage.save_project(p)
    return _versions_view(p)


@app.post("/api/projects/{pid}/versions/{vid}/activate")
def activate_version(pid: str, vid: str):
    p = _project(pid)
    try:
        pipeline.version_activate(p, vid)
    except KeyError:
        raise HTTPException(404, "Version introuvable")
    storage.save_project(p)
    return p


@app.patch("/api/projects/{pid}/versions/{vid}")
def rename_version(pid: str, vid: str, body: VersionReq):
    p = _project(pid)
    v = next((v for v in p.versions if v.id == vid), None)
    if not v:
        raise HTTPException(404, "Version introuvable")
    v.name = (body.name or v.name).strip()[:60] or v.name
    storage.save_project(p)
    return _versions_view(p)


@app.delete("/api/projects/{pid}/versions/{vid}")
def delete_version(pid: str, vid: str):
    p = _project(pid)
    try:
        pipeline.version_delete(p, vid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    storage.save_project(p)
    return p


class ShortsReq(BaseModel):
    count: int = 3
    duration: float = 30.0
    format: str = "9:16"


@app.post("/api/projects/{pid}/shorts")
def make_shorts(pid: str, body: ShortsReq):
    p = _project(pid)
    if not p.media:
        raise HTTPException(400, "Ajoute au moins une vidéo.")
    if body.format not in ("9:16", "1:1", "16:9"):
        raise HTTPException(400, "Format inconnu")
    count = max(1, min(10, body.count))
    dur = max(10.0, min(180.0, body.duration))
    return manager.submit(pid, "shorts", lambda ctx: pipeline.make_shorts(pid, ctx, count, dur, body.format))


# --- Images (logos, stickers) ------------------------------------------------------------

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


@app.post("/api/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...)):
    _project(pid)
    name = _safe_name(file.filename or "image.png")
    if Path(name).suffix.lower() not in IMAGE_EXT:
        raise HTTPException(400, "Image attendue (PNG, JPG ou WebP).")
    dst = storage.assets_dir(pid) / name
    i = 1
    while dst.exists():
        dst = storage.assets_dir(pid) / f"{Path(name).stem} ({i}){Path(name).suffix}"
        i += 1
    await _save_upload(file, dst)
    return {"asset": dst.name}


@app.get("/api/projects/{pid}/assets/{name}")
def get_asset(pid: str, name: str):
    path = storage.assets_dir(pid) / Path(name).name
    if not path.exists():
        raise HTTPException(404, "Image introuvable")
    return FileResponse(path)


# --- Contrôle qualité, relecture, feuille son/image ---------------------------------------------

class QualityReq(BaseModel):
    fix: bool = False


@app.post("/api/projects/{pid}/quality")
def run_quality(pid: str, body: QualityReq):
    """Contrôle du plan de montage ; avec fix, applique les corrections automatiques."""
    _project(pid)
    return manager.submit(pid, "controle", lambda ctx: pipeline.quality_job(pid, body.fix, ctx))


@app.post("/api/projects/{pid}/voice-focus")
def run_voice_focus(pid: str):
    """Cale les plans sur les phrases et pose un projecteur sur ce que la voix désigne."""
    _project(pid)
    return manager.submit(pid, "projecteur", lambda ctx: pipeline.voice_focus_job(pid, ctx))


@app.post("/api/projects/{pid}/review")
def run_review(pid: str):
    """Relecteur indépendant : note la version, et la fait retoucher sous la note de passage (3 tours au plus)."""
    _project(pid)
    return manager.submit(pid, "relecture", lambda ctx: pipeline.review_job(pid, ctx))


@app.get("/api/projects/{pid}/soundsheet")
def get_soundsheet(pid: str):
    from .ai import soundsheet
    p = _project(pid)
    return Response(soundsheet.sheet(p, p.timeline, pipeline.transcripts_for(p)), media_type="text/plain; charset=utf-8")


# --- Étalonnage (LUT) et aperçu image ---------------------------------------------------------

@app.get("/api/projects/{pid}/luts")
def list_luts(pid: str):
    from .render import luts
    _project(pid)
    return luts.listing(pid)


@app.post("/api/projects/{pid}/luts")
async def upload_lut(pid: str, file: UploadFile = File(...)):
    """Importe une LUT 3D .cube (exportée de DaVinci Resolve, Premiere, Lightroom…)."""
    from .render import luts
    _project(pid)
    name = _safe_name(file.filename or "lut.cube")
    if Path(name).suffix.lower() != ".cube":
        raise HTTPException(400, "Fichier .cube attendu.")
    data = await file.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(400, "LUT trop lourde (64 Mo maximum).")
    try:
        luts.validate_cube(data.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    (luts.project_dir(pid) / name).write_bytes(data)
    return {"id": f"projet:{name}", "label": Path(name).stem, "builtin": False}


@app.get("/api/projects/{pid}/preview-frame")
def preview_frame(pid: str, media: str, t: float = 0.0, lut: str | None = None, strength: float = 1.0,
                  effect: str | None = None, background: str | None = None):
    """Image fixe d'un rush avec l'étalonnage et/ou le détourage, pour juger le rendu sans exporter."""
    import subprocess

    import cv2
    import numpy as np

    from .analysis import segment
    from .render import luts
    p = _project(pid)
    m = next((x for x in p.media if x.id == media), None)
    if not m or not m.has_video:
        raise HTTPException(404, "Rush introuvable")
    W = 640
    H = max(2, int(round(W * (m.height or 360) / max(1, m.width or 640) / 2)) * 2)
    vf = f"scale={W}:{H}"
    work = storage.project_dir(pid) / "work"
    work.mkdir(parents=True, exist_ok=True)
    lut_path = luts.resolve(pid, lut)
    cube = work / f"preview_{uuid.uuid4().hex[:8]}.cube"
    if lut_path:
        shutil.copyfile(lut_path, cube)
        k = max(0.0, min(1.0, strength))
        vf += (f",lut3d=file={cube.name}:interp=tetrahedral" if k >= 0.999 else
               f",split[a][b];[b]lut3d=file={cube.name}:interp=tetrahedral[c];[a][c]blend=all_expr='A*(1-{k:.3f})+B*{k:.3f}'")
    try:
        proc = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}",
                               "-i", m.path, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                              capture_output=True, cwd=work)
    finally:
        cube.unlink(missing_ok=True)
    if proc.returncode != 0 or len(proc.stdout) < W * H * 3:
        raise HTTPException(400, "Image illisible à cet instant.")
    frame = np.frombuffer(proc.stdout[:W * H * 3], np.uint8).reshape(H, W, 3).copy()
    if effect and effect.startswith("detourage_"):
        try:
            seg = segment.Segmenter()
        except segment.SegmenterUnavailable as e:
            raise HTTPException(503, str(e))
        bg = segment.cover_image(storage.assets_dir(pid) / Path(background).name, W, H) if background else None
        frame = segment.composite(frame, seg.mask(frame), effect, bg)
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(jpg.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --- Modèles de style ---------------------------------------------------------------------

class TemplateReq(BaseModel):
    name: str


@app.get("/api/templates")
def templates():
    return pipeline.list_templates()


@app.post("/api/projects/{pid}/templates")
def save_template(pid: str, body: TemplateReq):
    return pipeline.save_template(_project(pid), body.name)


@app.post("/api/projects/{pid}/templates/{tid}/apply")
def apply_template(pid: str, tid: str):
    p = _project(pid)
    try:
        pipeline.apply_template(p, tid)
    except KeyError:
        raise HTTPException(404, "Modèle introuvable")
    storage.save_project(p)
    return p


@app.delete("/api/templates/{tid}")
def delete_template(tid: str):
    pipeline.delete_template(tid)
    return {"ok": True}


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
