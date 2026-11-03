"""Orchestration du pipeline : ingestion → analyse → transcription → réalisation → rendu."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from . import config, storage
from .ai import director as dir_mod
from .ai.decorate import decorate
from .ai import publish as publish_mod
from .ai.segments import build_segments
from .analysis import audio as audio_an
from .analysis import faces as faces_an
from .analysis import transcribe as tr_mod
from .analysis import video as video_an
from .jobs import JobContext
from .media import ffmpeg
from .models import EditSettings, Media, Project, Segment, Timeline, Transcript
from .render import render as render_mod

log = logging.getLogger(__name__)
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def ingest(project: Project, src: Path, filename: str) -> Media:
    info = ffmpeg.probe(src)
    if not info["has_video"] and not info["has_audio"]:
        raise ValueError("Ce fichier ne contient ni vidéo ni audio.")
    m = Media(filename=filename, path=str(src.resolve()), **info)
    project.media.append(m)
    return m


def analyze_media(project: Project, m: Media, ctx: JobContext, lo: float, hi: float) -> None:
    """Proxy + signaux + transcription d'un média, avec cache."""
    cache = storage.cache_dir(project.id)
    span = hi - lo
    if not m.proxy and m.has_video:
        proxy = cache / f"{m.id}.proxy.mp4"
        ffmpeg.make_proxy(m.path, proxy, m.duration,
                          ctx.sub(lo, lo + span * 0.35, f"{m.filename} : création du proxy de lecture"))
        m.proxy = str(proxy)
        ffmpeg.thumbnail(proxy, cache / f"{m.id}.jpg", min(1.0, m.duration / 2))
    signals = storage.load_signals(project.id, m.id)
    pcm = None
    if signals is None:
        ctx.step(f"{m.filename} : analyse audio et vidéo", lo + span * 0.4)
        signals = {}
        if m.has_audio:
            pcm = ffmpeg.decode_audio(m.path)
            signals["audio"] = audio_an.analyze(pcm, m.duration)
        if m.has_video:
            signals["video"] = video_an.analyze(ffmpeg.decode_gray_frames(m.proxy or m.path))
        storage.save_signals(project.id, m.id, signals)
    if m.has_video and "faces" not in signals and faces_an.AVAILABLE:
        ctx.step(f"{m.filename} : suivi des visages (recadrage auto)", lo + span * 0.45)
        try:
            frames = ffmpeg.decode_gray_small(m.proxy or m.path, faces_an.FACE_FPS)
            signals["faces"] = faces_an.analyze(frames, (signals.get("video") or {}).get("cuts", []))
            storage.save_signals(project.id, m.id, signals)
        except Exception as e:  # le recadrage centré reste possible
            log.warning("Suivi des visages impossible pour %s : %s", m.filename, e)
    if m.has_audio and storage.load_transcript(project.id, m.id) is None:
        ctx.step(f"{m.filename} : transcription (Whisper {config.WHISPER_MODEL})", lo + span * 0.5)
        try:
            if pcm is None:
                pcm = ffmpeg.decode_audio(m.path)
            t = tr_mod.transcribe(pcm, m.id, m.duration, ctx.sub(lo + span * 0.5, hi))
            storage.save_transcript(project.id, t)
        except tr_mod.TranscriptionUnavailable as e:
            warn = f"Transcription indisponible ({e}). Montage basé sur l'audio et l'image seulement."
            if warn not in project.warnings:
                project.warnings.append(warn)
    m.analyzed = True
    ctx.step(f"{m.filename} : analysé", hi)


def transcripts_for(project: Project) -> dict[str, Transcript]:
    out = {}
    for m in project.media:
        t = storage.load_transcript(project.id, m.id)
        if t:
            out[m.id] = t
    return out


def all_segments(project: Project, settings: EditSettings) -> list[Segment]:
    segs: list[Segment] = []
    for m in project.media:
        signals = storage.load_signals(project.id, m.id) or {}
        segs += build_segments(m, storage.load_transcript(project.id, m.id), signals,
                               split_fillers=settings.remove_fillers,
                               include_action=settings.style != "podcast")
    storage.save_segments(project.id, segs)
    return segs


def pick_music(project: Project, style: str) -> str | None:
    pdir = storage.project_dir(project.id) / "music"
    tracks = sorted(p for d in (pdir, config.MUSIC_DIR) if d.exists() for p in d.iterdir()
                    if p.suffix.lower() in MUSIC_EXT)
    if not tracks:
        return None
    # Choix stable par projet ; un nom de fichier contenant le style est prioritaire.
    styled = [t for t in tracks if style in t.name.lower()]
    rnd = random.Random(project.id)
    return str(rnd.choice(styled or tracks))


def _has_effects(tl: Timeline) -> bool:
    return bool(tl.texts or tl.sfx or tl.overlays or (tl.look and tl.look != "aucun")
                or any(c.effects or c.audio_effects or c.transition != "aucune" for c in tl.clips))


def auto_edit(project_id: str, settings: EditSettings, ctx: JobContext) -> Timeline:
    project = storage.load_project(project_id)
    if not project.media:
        raise ValueError("Ajoute au moins une vidéo avant de lancer le montage.")
    project.settings = settings
    project.warnings = []
    n = len(project.media)
    for i, m in enumerate(project.media):
        analyze_media(project, m, ctx, 0.85 * i / n, 0.85 * (i + 1) / n)
        storage.save_project(project)

    ctx.step("Repérage et notation des moments forts", 0.86)
    segments = all_segments(project, settings)
    music = pick_music(project, settings.style) if settings.music else None
    if settings.music and not music:
        project.warnings.append("Aucune musique trouvée : ajoute des fichiers audio libres de droits.")

    director = dir_mod.get_director()
    ctx.step("Réalisation du montage par l'IA" + (" (Claude)" if director.name == "claude" else ""), 0.9)
    order = [m.id for m in project.media]
    try:
        tl = director.direct(segments, settings, order, music)
    except Exception as e:
        if director.name != "claude":
            raise
        log.warning("Réalisateur Claude en échec, repli heuristique : %s", e)
        project.warnings.append(f"Réalisateur Claude indisponible ({e}) : montage heuristique utilisé.")
        tl = dir_mod.HeuristicDirector().direct(segments, settings, order, music)
    if director.name != "claude" or not _has_effects(tl):
        ctx.step("Habillage : transitions, effets, bruitages, titres", 0.97)
        tl = decorate(tl, segments, settings, project.name)
    project.timeline = tl
    storage.save_project(project)
    ctx.step(f"Montage prêt : {len(tl.clips)} plans, {tl.duration:.0f} s", 1.0)
    return tl


def ai_command(project_id: str, instruction: str, ctx: JobContext) -> dict:
    project = storage.load_project(project_id)
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    order = [m.id for m in project.media]
    music = project.timeline.music.track or (pick_music(project, project.settings.style)
                                              if project.settings.music else None)
    ctx.step("L'IA applique ta consigne", 0.2)
    director = dir_mod.get_director()
    msg = ""
    if director.name == "claude":
        tl = director.command(instruction, project.timeline, segments, project.settings, music)
        msg = tl.summary
    else:
        tl, msg, s2 = dir_mod.heuristic_command(instruction, project.timeline, segments,
                                                project.settings, order, music)
        if tl.director == "heuristique" and tl is not project.timeline and not _has_effects(tl):
            tl = decorate(tl, segments, s2, project.name)
        project.settings = s2
        if s2.music and not tl.music.track:
            tl.music.track = pick_music(project, s2.style)
    project.timeline = tl
    storage.save_project(project)
    ctx.step(msg or "Consigne appliquée", 1.0)
    return {"message": msg, "director": director.name}


def export(project_id: str, quality: str, ctx: JobContext, fps: int | None = None, smooth: bool = False) -> str:
    project = storage.load_project(project_id)
    tl = project.timeline
    out_dir = storage.exports_dir(project.id)
    idx = len(list(out_dir.glob("*.mp4"))) + 1
    safe = "".join(ch for ch in (tl.title or project.name) if ch.isalnum() or ch in " -_").strip()[:50] or "montage"
    rate = fps or render_mod.source_fps(project, tl)
    out = out_dir / f"{idx:02d} - {safe} ({tl.format.replace(':', 'x')} {quality} {rate}fps).mp4"
    work = storage.cache_dir(project.id) / f"render_{idx}"
    work.mkdir(parents=True, exist_ok=True)
    music = tl.music.track if tl.music.track and Path(tl.music.track).exists() else None
    ctx.step(f"Rendu {quality} à {rate} i/s" + (" avec interpolation" if smooth else ""), 0.01)
    render_mod.render(project, tl, transcripts_for(project), out.resolve(), work.resolve(), quality, music,
                      ctx.sub(0.01, 0.99, f"Rendu {quality} à {rate} i/s"), fps=rate, smooth=smooth)
    project.exports.append(out.name)
    storage.save_project(project)
    ctx.step("Export terminé", 1.0)
    return out.name


def publish_kit(project_id: str, ctx: JobContext) -> dict:
    project = storage.load_project(project_id)
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    ctx.step("Titres, description et chapitres" + (" (Claude)" if config.claude_available() else ""), 0.2)
    work = storage.cache_dir(project.id) / "publish"
    work.mkdir(parents=True, exist_ok=True)
    kit = publish_mod.build_kit(project, segments, storage.exports_dir(project.id), work.resolve())
    project.publish_kit = kit
    storage.save_project(project)
    ctx.step("Kit de publication prêt", 1.0)
    return kit
