"""API HTTP du Monteur IA."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Literal
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, cuda, doctor, downloads, pipeline, settings, storage, timeline_ops
from .version import __version__
from .analysis import transcribe as tr_mod
from . import updates
from .jobs import manager
from .media import ffmpeg
from .models import EditSettings, Project, ResearchNote, Timeline
from .render import captions as cap
from .render import effects as fx
from .render import render as render_mod
from .render import xml_export

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        updates.cleanup_old()  # installateurs périmés et téléchargements interrompus
    except Exception:
        logging.getLogger(__name__).warning("Nettoyage des mises à jour impossible", exc_info=True)
    yield
    # À l'arrêt, les tâches encore actives sont annulées : ffmpeg et Whisper ne doivent pas survivre à l'app.
    n = manager.cancel_all()
    if n:
        logging.getLogger(__name__).info("Arrêt : %d tâche(s) annulée(s)", n)
    manager.flush()


app = FastAPI(title="Monteur IA", version=__version__, lifespan=_lifespan)
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

_tts_cache: tuple[float, dict] | None = None


def _tts_status() -> dict:
    """Moteur de voix par langue, mis en cache 30 s (la recherche des voix touche au disque)."""
    global _tts_cache
    if _tts_cache is None or time.monotonic() - _tts_cache[0] > 30:
        from .ai import tts
        _tts_cache = (time.monotonic(), tts.status())
    return _tts_cache[1]


@app.get("/api/health")
def health():
    enc, _ = ffmpeg.best_h264_encoder()
    dll = cuda.add_dll_dirs()  # avant tout import de ctranslate2
    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False
    whisper = tr_mod.whisper_status()
    pub = settings.public()
    return {
        "ok": True,
        "version": __version__,
        "claude": config.claude_available(),
        "claude_model": config.CLAUDE_MODEL,
        "whisper_model": config.WHISPER_MODEL,
        "encoder": enc,
        "music_library": len([p for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in pipeline.MUSIC_EXT]),
        "whisper_device": whisper["device_used"],
        "whisper_fallback": whisper["fallback_reason"],
        "cuda": has_cuda,
        "cublas": dll["cublas"],
        "claude_key_set": pub["anthropic_api_key_set"],
        "key_source": pub["key_source"],
        "tts": _tts_status(),
    }


@app.get("/api/system")
def system(refresh: bool = False):
    """Diagnostic du poste (FFmpeg, GPU, Whisper, voix, Claude, dossiers), gardé 30 s ; `?refresh=1` le recalcule."""
    return doctor.report(refresh)


# --- Réglages -----------------------------------------------------------------

def _require_claude(feature: str) -> None:
    """409 explicite si la fonction a besoin de Claude et qu'aucune clé n'est configurée."""
    if not config.claude_available():
        raise HTTPException(409, {"code": "claude_required", "message": (
            f"{feature} utilise Claude : ajoute ta clé dans Réglages (engrenage en haut à droite).")})


@app.get("/api/settings")
def get_settings():
    return settings.public()


@app.put("/api/settings")
def put_settings(body: dict):
    """Met à jour les réglages envoyés (les autres restent). Une clé vide efface la clé Claude."""
    try:
        settings.apply(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return settings.public()


# --- Mises à jour ---------------------------------------------------------------------

@app.get("/api/update")
def get_update(force: int = 0):
    """État des mises à jour (gardé 6 h ; `force=1` revérifie). Une panne réseau remplit `error`, jamais une erreur HTTP."""
    return updates.status(bool(force))


def _own_page_only(request: Request) -> None:
    """Les routes qui téléchargent ou installent ne répondent qu'à la page de l'application elle-même. Le CORS est
    ouvert à tous : sans ce contrôle, n'importe quelle page web ouverte dans le navigateur pourrait les déclencher
    (requête « simple » sans préflight). On refuse (403) une origine étrangère, et un nom d'hôte qui n'est pas local
    (rebinding DNS)."""
    host = (request.headers.get("host") or "").lower()
    if (urlparse("//" + host).hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(403, "Cette action n'est permise que depuis l'application elle-même.")
    origin = request.headers.get("origin")
    if origin is not None and urlparse(origin).netloc.lower() != host:
        raise HTTPException(403, "Cette action n'est permise que depuis l'application elle-même.")


@app.post("/api/update/download", dependencies=[Depends(_own_page_only)])
def update_download():
    """Télécharge et vérifie l'installateur de la version publiée (tâche « mise_a_jour »), sans l'installer."""
    try:
        return updates.start_download()
    except updates.UpdateError as e:
        raise HTTPException(e.status, e.message)


@app.post("/api/update/install", dependencies=[Depends(_own_page_only)])
def update_install():
    """Lance l'installateur téléchargé, puis l'application se ferme dans la seconde. 409 si ce n'est pas possible."""
    try:
        return updates.install()
    except updates.UpdateError as e:
        raise HTTPException(e.status, e.message)


def _ping_claude() -> None:
    import anthropic
    anthropic.Anthropic(timeout=10, max_retries=0).models.list(limit=1)


@app.post("/api/settings/test-claude")
async def test_claude():
    """Vérifie la clé Claude par un appel léger (liste des modèles)."""
    _require_claude("Le test de connexion")
    try:
        await anyio.to_thread.run_sync(_ping_claude)
    except Exception as e:
        return {"ok": False, "error": (getattr(e, "message", None) or str(e))[:200], "model": config.CLAUDE_MODEL}
    return {"ok": True, "error": None, "model": config.CLAUDE_MODEL}


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


@app.get("/api/projects/{pid}/backups")
def list_backups(pid: str):
    _project(pid)
    return storage.list_backups(pid)


@app.post("/api/projects/{pid}/backups/{name}/restore")
def restore_backup(pid: str, name: str):
    """Remet une sauvegarde automatique en place (l'état courant est gardé comme sauvegarde avant)."""
    _project(pid)
    try:
        return storage.restore_backup(pid, name)
    except ValueError:
        raise HTTPException(400, "Nom de sauvegarde invalide")
    except FileNotFoundError:
        raise HTTPException(404, "Sauvegarde introuvable")


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


_STRIP_LOCK = threading.Lock()  # une pellicule à la fois : chacune lance déjà 8 FFmpeg


@app.get("/api/projects/{pid}/media/{mid}/filmstrip")
def media_filmstrip(pid: str, mid: str):
    """Pellicule du rush pour la timeline (grille de vignettes), fabriquée à la première demande puis gardée.

    Les en-têtes X-Strip-* décrivent la grille : l'interface n'a pas à connaître les constantes du serveur.
    """
    p = _project(pid)
    try:
        m = p.media_by_id(mid)
    except KeyError:
        raise HTTPException(404, "Média introuvable")
    if not m.has_video:
        raise HTTPException(404, "Ce média n'a pas d'image")
    path = storage.cache_dir(pid) / f"{mid}.strip.jpg"
    n = ffmpeg.filmstrip_count(m.duration)
    if not path.exists():
        with _STRIP_LOCK:
            if not path.exists():  # une autre requête vient peut-être de la fabriquer
                src = m.proxy if m.proxy and Path(m.proxy).exists() else m.path
                # 503 et non 404 : l'interface réessaiera (disque rebranché), alors qu'un 404 est définitif.
                if not Path(src).exists():
                    raise HTTPException(503, "Fichier du rush introuvable pour l'instant")
                try:
                    ffmpeg.filmstrip(src, path, m.duration, n)
                except ffmpeg.FFmpegError as e:
                    raise HTTPException(503, f"Vignettes impossibles : {e}")
    return FileResponse(path, media_type="image/jpeg", headers={
        "X-Strip-Count": str(n), "X-Strip-Cols": str(ffmpeg.STRIP_COLS),
        "X-Strip-Tile": f"{ffmpeg.STRIP_W}x{ffmpeg.STRIP_H}", "Cache-Control": "private, max-age=86400",
    })


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


@app.post("/api/demo")
def demo_project():
    """« Essayer sans vidéo » : un projet avec le clip de démo (combat PvP synthétique), monté automatiquement.
    Le projet est créé tout de suite pour que l'éditeur s'ouvre et suive la tâche en direct."""
    from .media import demo
    p = storage.save_project(Project(name=demo.DEMO_PROJECT_NAME,
                                     settings=EditSettings(style="pvp", captions="aucun", music=True)))
    job = manager.submit(p.id, "demo", lambda ctx: demo.demo_job(p.id, ctx))
    return {"project": p, "job": job}


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


class UrlReq(BaseModel):
    url: str


def _ingest_downloaded(pid: str, path: Path):
    added = []

    def apply(p: Project) -> None:
        try:
            added.append(pipeline.ingest(p, path, path.name))
        except Exception as e:
            path.unlink(missing_ok=True)
            raise RuntimeError(str(e))
    storage.update_project(pid, apply)
    return added[0]


@app.post("/api/projects/{pid}/media/url")
def import_media_url(pid: str, body: UrlReq):
    """Rush depuis un lien (YouTube, Vimeo, fichier direct…) : téléchargé par yt-dlp puis analysé comme un import."""
    _project(pid)

    def run(ctx):
        if Path(body.url).suffix.lower() in VIDEO_EXT and not body.url.startswith("http"):
            raise ValueError("Donne un lien http(s)")
        path = downloads.fetch_url(body.url, storage.media_dir(pid), ctx)
        if path.suffix.lower() not in VIDEO_EXT:
            path.unlink(missing_ok=True)
            raise ValueError(f"Format non pris en charge : {path.suffix}")
        ctx.step("Analyse du fichier", 0.97)
        return _ingest_downloaded(pid, path)
    return manager.submit(pid, "telechargement", run)


@app.post("/api/projects/{pid}/reference/url")
def reference_from_url(pid: str, body: UrlReq):
    """Vidéo de référence depuis un lien : « monte comme cette vidéo »."""
    from .ai import reference as ref_mod
    _project(pid)

    def run(ctx):
        d = storage.project_dir(pid) / "reference"
        tmp = storage.cache_dir(pid) / "reference_dl"
        shutil.rmtree(tmp, ignore_errors=True)
        path = downloads.fetch_url(body.url, tmp, ctx, max_height=720)
        ctx.step("Analyse du rythme", 0.96)
        prof = ref_mod.analyze(path)
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            old.unlink(missing_ok=True)
        dst = d / _safe_name(path.name)
        shutil.move(str(path), dst)
        shutil.rmtree(tmp, ignore_errors=True)
        prof["filename"] = dst.name
        prof["summary"] = ref_mod.describe(prof)
        storage.update_project(pid, lambda p: setattr(p, "reference", prof))
        return prof
    return manager.submit(pid, "telechargement", run)


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


class ResearchReq(BaseModel):
    question: str


@app.post("/api/projects/{pid}/research")
def research_web(pid: str, body: ResearchReq):
    """Cherche une information sur le web pour la vidéo ; la note est gardée dans le projet et guide le réalisateur."""
    from .ai import research as research_mod
    p = _project(pid)
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Question vide.")
    from .ai.dossier import project_dossier

    def run(ctx):
        # Le dossier des rushes (mesures de l'app) part avec la question : l'assistant sait ce qu'il y a dedans.
        r = research_mod.research(q, project_dossier(storage.load_project(pid), max_chars=6000), ctx)
        note = ResearchNote(question=q, answer=r["answer"], sources=r["sources"], by=r["by"])
        storage.update_project(pid, lambda pr: pr.research.append(note))
        ctx.step("Note enregistrée", 1.0)
        return note.model_dump()
    return manager.submit(pid, "recherche", run)


class ChatReq(BaseModel):
    text: str


@app.post("/api/projects/{pid}/chat")
def chat_send(pid: str, body: ChatReq):
    """Chat avec le réalisateur IA : il connaît le dossier des rushes et peut appliquer une consigne au montage."""
    from .ai import chat as chat_mod
    _project(pid)
    if not body.text.strip():
        raise HTTPException(400, "Message vide.")
    return manager.submit(pid, "chat", lambda ctx: chat_mod.talk(pid, body.text, ctx))


@app.delete("/api/projects/{pid}/chat")
def chat_clear(pid: str):
    # Le middleware tient déjà le verrou du projet pendant cette requête : lecture puis enregistrement direct
    # (update_project reprendrait le même verrou, non réentrant → blocage).
    p = _project(pid)
    p.chat = []
    storage.save_project(p)
    return {"ok": True}


class VisionReq(BaseModel):
    media_ids: list[str] | None = None
    force: bool = False  # regarder à nouveau un rush déjà vu


@app.get("/api/projects/{pid}/vision")
def vision_estimate(pid: str):
    """Combien d'images une passe de vision enverrait à Claude, et quels rushes ont déjà été regardés."""
    from .ai import vision
    return vision.estimate(_project(pid))


@app.post("/api/projects/{pid}/vision")
def vision_run(pid: str, body: VisionReq | None = None):
    """Fait regarder les rushes par Claude (images aux instants clés). Appel payant : seulement sur action du monteur."""
    from .ai import vision
    _project(pid)
    if not config.claude_available():
        raise HTTPException(400, "Regarder les images demande une clé Claude (Réglages).")
    body = body or VisionReq()
    return manager.submit(pid, "vision", lambda ctx: vision.observe_project(pid, ctx, body.media_ids, body.force))


@app.get("/api/projects/{pid}/dossier", response_class=Response)
def project_dossier_text(pid: str):
    """Le dossier des rushes tel que le réalisateur IA le lit (texte brut)."""
    from .ai.dossier import project_dossier
    return Response(project_dossier(_project(pid)), media_type="text/plain; charset=utf-8")


@app.post("/api/projects/{pid}/duplicate")
def duplicate_project(pid: str):
    """Copie complète du projet (médias, analyses, montage, versions) sous un nouvel identifiant."""
    from .models import new_id
    p = _project(pid)
    new_pid = new_id("p_")
    src, dst = storage.project_dir(pid), storage.project_dir(new_pid)
    # Le verrou du projet source est déjà tenu par le middleware le temps de la requête : copie sûre.
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("exports"))
    cp = Project.model_validate_json((dst / "project.json").read_text(encoding="utf-8"))
    cp.id, cp.name, cp.created_at, cp.exports = new_pid, f"Copie de {p.name}", time.time(), []

    def moved(path: str | None) -> str | None:
        """Chemin absolu dans l'ancien dossier → le même dans la copie."""
        if not path:
            return path
        try:
            return str(dst / Path(path).resolve().relative_to(src.resolve()))
        except ValueError:
            return path
    for m in cp.media:
        m.path, m.proxy = moved(m.path), moved(m.proxy)
    for tl in [cp.timeline, *(v.timeline for v in cp.versions if v.timeline)]:
        tl.music.track = moved(tl.music.track)
    return storage.save_project(cp)


class OpenFolderReq(BaseModel):
    sub: Literal["exports", "media"] | None = None  # sous-dossier à ouvrir (sinon le dossier du projet)
    file: str | None = None  # nom d'un fichier du sous-dossier à mettre en évidence


@app.post("/api/projects/{pid}/open-folder")
def open_project_folder(pid: str, request: Request, body: OpenFolderReq | None = None):
    """Ouvre le dossier du projet (ou ses exports, ses médias) dans l'explorateur de fichiers, le fichier demandé
    sélectionné. Seulement depuis ce poste : pas depuis une autre machine du réseau."""
    _project(pid)
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "Le dossier ne s'ouvre que depuis ce poste.")
    body = body or OpenFolderReq()
    d = storage.project_dir(pid)
    if body.sub == "exports":
        d = storage.exports_dir(pid)
    elif body.sub == "media":
        d = storage.media_dir(pid)
    target = d / Path(body.file).name if body.file else None
    if target and not target.exists():
        raise HTTPException(404, "Fichier introuvable")
    if sys.platform == "win32":
        if target:
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(str(d))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target else ["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)])
    return {"path": str(target or d)}


@app.delete("/api/projects/{pid}/research/{nid}")
def research_delete(pid: str, nid: str):
    p = _project(pid)
    before = len(p.research)
    p.research = [n for n in p.research if n.id != nid]
    if len(p.research) == before:
        raise HTTPException(404, "Note introuvable")
    storage.save_project(p)
    return {"ok": True}


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
    if body.language:
        _require_claude("La traduction des sous-titres")
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
    project = _project(pid)
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(400, "Langue inconnue")
    if body.language and pipeline.dub_needs_claude(project, body.language):
        _require_claude("Le doublage")
    vol = max(0.0, min(1.0, body.original_volume))
    return manager.submit(pid, "doublage", lambda ctx: pipeline.dub_voice(pid, body.language, ctx, vol))


class VoiceoverReq(BaseModel):
    text: str
    start: float = 0.0
    language: str = "fr"
    lane: int = 2


@app.post("/api/projects/{pid}/voiceover")
def ai_voiceover(pid: str, body: VoiceoverReq):
    """Voix off IA : un texte lu par une voix de synthèse, réécouté et calé au mot près."""
    from .ai.translate import LANGUAGES
    _project(pid)
    if body.language not in LANGUAGES:
        raise HTTPException(400, "Langue inconnue")
    if not body.text.strip() or len(body.text) > 2000:
        raise HTTPException(400, "Texte vide ou trop long (2000 caractères au plus)")
    lane = max(0, min(3, body.lane))
    return manager.submit(pid, "voix_ia", lambda ctx: pipeline.ai_voiceover(pid, body.text, body.start, body.language,
                                                                           ctx, lane))


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


LIBRARY_PID = "_bibliotheque"  # tâches de la bibliothèque et des modèles, sans projet


@app.get("/api/music/library")
def music_library_detailed():
    """Morceaux de la bibliothèque avec leur origine et leur licence quand on les connaît."""
    out = []
    for p in sorted(config.MUSIC_DIR.iterdir()):
        if p.suffix.lower() not in pipeline.MUSIC_EXT:
            continue
        info = downloads.library_info(p.name) or {}
        out.append({"name": p.name, "path": str(p.resolve()), "size": p.stat().st_size, "title": info.get("title"),
                    "artist": info.get("artist"), "license": info.get("license"), "license_url": info.get("license_url"),
                    "source": info.get("source"), "page": info.get("page")})
    return out


@app.get("/api/music/search")
def music_search(q: str = "", limit: int = 20):
    try:
        return downloads.search_music(q, limit)
    except Exception as e:
        raise HTTPException(502, f"ccMixter injoignable : {e}")


class MusicItem(BaseModel):
    id: str = ""
    title: str = ""
    artist: str = ""
    license: str = ""
    license_url: str = ""
    page: str = ""
    download_url: str
    bpm: int | None = None
    tags: list[str] = []


@app.post("/api/music/download")
def music_download(item: MusicItem):
    if not item.download_url.startswith("https://ccmixter.org/") and not item.download_url.startswith("http://ccmixter.org/"):
        raise HTTPException(400, "Seuls les morceaux ccMixter passent par ici")
    return manager.submit(LIBRARY_PID, "telechargement", lambda ctx: downloads.download_music(item.model_dump(), ctx).name)


@app.post("/api/music/url")
def music_from_url(body: UrlReq):
    return manager.submit(LIBRARY_PID, "telechargement", lambda ctx: downloads.music_from_url(body.url, ctx).name)


@app.delete("/api/music/{name}")
def music_delete(name: str):
    p = config.MUSIC_DIR / _safe_name(name)
    if not p.is_file() or p.suffix.lower() not in pipeline.MUSIC_EXT:
        raise HTTPException(404, "Morceau introuvable")
    p.unlink()
    p.with_suffix(p.suffix + ".json").unlink(missing_ok=True)
    return {"ok": True}


# --- Modèles (Whisper, voix Piper) -------------------------------------------------------------

@app.get("/api/models")
def models_status():
    from .ai import tts as tts_mod
    return {"whisper": downloads.whisper_models(), "piper": {"runtime": downloads.piper_runtime(),
            "voices_dir": str(tts_mod.voices_dir()), "installed": [p.name for p in downloads.installed_piper_voices()]}}


@app.post("/api/models/whisper/{size}")
def models_whisper(size: str):
    if size not in {s for s, _ in downloads.WHISPER_SIZES}:
        raise HTTPException(400, "Modèle inconnu")
    return manager.submit(LIBRARY_PID, "telechargement", lambda ctx: downloads.download_whisper(size, ctx))


@app.get("/api/models/piper")
def models_piper(lang: str | None = None):
    try:
        return downloads.piper_catalog(lang)
    except Exception as e:
        raise HTTPException(502, f"Catalogue des voix injoignable : {e}")


@app.post("/api/models/piper/{key}")
def models_piper_download(key: str):
    if not re.fullmatch(r"[a-zA-Z_]+-[a-zA-Z0-9_]+-[a-z_]+", key):
        raise HTTPException(400, "Nom de voix invalide")
    return manager.submit(LIBRARY_PID, "telechargement", lambda ctx: downloads.download_piper(key, ctx).name)


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
    range_only: bool = False  # seulement la zone d'entrée/sortie (I/O) de la timeline


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: ExportReq):
    p = _project(pid)
    if not p.timeline.clips:
        raise HTTPException(400, "La timeline est vide.")
    tl = label = None
    if body.range_only:
        if not p.timeline.range:
            raise HTTPException(400, "Aucune zone d'entrée/sortie : pose-la avec I et O (sans plan sélectionné).")
        r = p.timeline.range
        tl = timeline_ops.slice_timeline(p.timeline, r.start, r.end)
        if not tl.clips:
            raise HTTPException(400, "La zone ne contient aucun plan.")
        label = f"{p.timeline.title or p.name} (zone {_mmss(r.start)}-{_mmss(r.end)})"
    if body.quality not in render_mod.QUALITIES:
        raise HTTPException(400, "Qualité inconnue")
    if body.fps is not None and body.fps not in render_mod.FPS_CHOICES:
        raise HTTPException(400, f"Cadence non prise en charge (choix : {', '.join(map(str, render_mod.FPS_CHOICES))})")
    if body.container not in (*render_mod.CONTAINERS, "gif"):
        raise HTTPException(400, "Format de fichier inconnu")
    return manager.submit(pid, "export", lambda ctx: pipeline.export(pid, body.quality, ctx, body.fps, body.smooth,
                                                                     body.container, tl=tl, label=label))


def _mmss(t: float) -> str:
    return f"{int(t) // 60}m{int(t) % 60:02d}"


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


# --- Identité de marque ---------------------------------------------------------------------

class BrandReq(BaseModel):
    asset: str | None = None  # logo déjà importé (dossier images du projet)
    url: str | None = None  # ou site de la marque
    template: bool = False  # enregistre aussi un modèle de style à son nom


@app.post("/api/projects/{pid}/brand")
def set_brand(pid: str, body: BrandReq):
    """Tire couleurs, police et ton d'un logo ou d'un site, et les applique aux textes et sous-titres."""
    from .ai import brand as brand_mod
    p = _project(pid)
    try:
        if body.asset:
            path = storage.assets_dir(pid) / Path(body.asset).name
            if not path.exists():
                raise HTTPException(404, "Logo introuvable")
            b = brand_mod.from_logo(path, Path(body.asset).stem)
        elif body.url:
            b = brand_mod.from_site(body.url.strip()[:500])
        else:
            raise HTTPException(400, "Donne un logo ou l'adresse d'un site.")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Lecture impossible : {e}")
    p.timeline.brand = b
    storage.save_project(p)
    tpl = pipeline.save_template(p, f"Marque {b.name}".strip()) if body.template else None
    return {"brand": b, "template": tpl}


@app.delete("/api/projects/{pid}/brand")
def clear_brand(pid: str):
    p = _project(pid)
    p.timeline.brand = None
    storage.save_project(p)
    return {"ok": True}


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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = manager.cancel(job_id)
    if not job:
        raise HTTPException(404, "Tâche introuvable")
    return job


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str, all: bool = False):
    """Tâches actives du projet ; avec `all=1`, les 20 dernières tous statuts confondus."""
    return manager.for_project(pid, all)


# --- Interface web ------------------------------------------------------------------

if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.FRONTEND_DIST / path
        if path and path != "index.html" and f.is_file() and config.FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        # index.html n'est jamais mis en cache : il référence des fichiers au nom haché qui changent à chaque build.
        return FileResponse(config.FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
