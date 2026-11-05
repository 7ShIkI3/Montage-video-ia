"""Orchestration du pipeline : ingestion → analyse → transcription → réalisation → rendu."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from . import config, storage
from .ai import director as dir_mod
from .ai.decorate import decorate
from .ai import publish as publish_mod
from .ai import comments as comments_mod
from .ai import pvp as pvp_mod
from .ai import quality as quality_mod
from .ai import review as review_mod
from .ai import soundsheet
from .ai.segments import build_segments
from .analysis import audio as audio_an
from .analysis import faces as faces_an
from .analysis import hits as hits_an
from .analysis import transcribe as tr_mod
from .analysis import video as video_an
from .jobs import JobContext
from .media import ffmpeg
from .models import EditSettings, Media, Project, Segment, Timeline, Transcript
from .render import captions as cap_mod
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
                          ctx.sub(lo, lo + span * 0.35, f"{m.filename} : création du proxy de lecture"),
                          cancel=ctx.cancel_event)
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
    if m.has_audio and "hits" not in signals:
        ctx.step(f"{m.filename} : détection des coups et des chocs", lo + span * 0.47)
        if pcm is None:
            pcm = ffmpeg.decode_audio(m.path)
        signals["hits"] = hits_an.detect_hits(pcm, config.AUDIO_SR)
        storage.save_signals(project.id, m.id, signals)
    if m.has_audio and storage.load_transcript(project.id, m.id) is None:
        ctx.step(f"{m.filename} : chargement de Whisper {config.WHISPER_MODEL}", lo + span * 0.5, stage="transcription")
        try:
            if pcm is None:
                pcm = ffmpeg.decode_audio(m.path)
            tr_mod.ensure_loaded()  # le choix GPU / processeur se fait au chargement
            st = tr_mod.whisper_status()
            if st["fallback_reason"]:
                warn = f"GPU indisponible ({st['fallback_reason'].splitlines()[0][:100]}) : transcription sur CPU, plus lente."
                if warn not in project.warnings:
                    project.warnings.append(warn)
            ctx.step(f"{m.filename} : transcription (Whisper {config.WHISPER_MODEL} sur {st['device_used'].upper()})",
                     lo + span * 0.5, stage="transcription")
            t = tr_mod.transcribe(pcm, m.id, m.duration, ctx.sub(lo + span * 0.5, hi))
            storage.save_transcript(project.id, t)
        except (RuntimeError, OSError) as e:  # dont TranscriptionUnavailable : le montage se fait sans paroles
            if not isinstance(e, tr_mod.TranscriptionUnavailable):
                log.warning("Transcription de %s échouée : %s", m.filename, e)
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


def _faces(project: Project) -> dict[str, dict]:
    return {m.id: (storage.load_signals(project.id, m.id) or {}).get("faces") or {} for m in project.media}


def _has_effects(tl: Timeline) -> bool:
    return bool(tl.texts or tl.sfx or tl.overlays or (tl.look and tl.look != "aucun")
                or any(c.effects or c.audio_effects or c.transition != "aucune" for c in tl.clips))


def _save_media(project: Project) -> None:
    """Enregistre seulement les médias analysés, sans toucher au reste du projet."""
    done = {m.id: m for m in project.media}

    def apply(p: Project) -> None:
        p.media = [done.get(m.id, m) for m in p.media]
    storage.update_project(project.id, apply)


def auto_edit(project_id: str, settings: EditSettings, ctx: JobContext) -> Timeline:
    project = storage.load_project(project_id)
    if not project.media:
        raise ValueError("Ajoute au moins une vidéo avant de lancer le montage.")
    project.settings = settings
    project.warnings = []
    n = len(project.media)
    for i, m in enumerate(project.media):
        ctx.check()
        analyze_media(project, m, ctx, 0.85 * i / n, 0.85 * (i + 1) / n)
        _save_media(project)

    ctx.step("Repérage et notation des moments forts", 0.86)
    segments = all_segments(project, settings)
    music = pick_music(project, settings.style) if settings.music else None
    if settings.music and not music:
        project.warnings.append("Aucune musique trouvée : ajoute des fichiers audio libres de droits.")

    if project.reference:
        from .ai import reference as ref_mod
        hint = ref_mod.describe(project.reference) + " Reproduis ce rythme de montage (le look reste celui des rushes)."
        settings = settings.model_copy(update={"instructions": (settings.instructions + "\n" + hint).strip()})
    if project.timeline.brand and project.timeline.brand.tone:
        b = project.timeline.brand
        settings = settings.model_copy(update={"instructions": (settings.instructions + f"\nTon de la marque {b.name} : "
                                                                f"{b.tone}.").strip()})
    if project.research:
        from .ai import research as research_mod
        settings = settings.model_copy(update={"instructions": (settings.instructions + "\n" +
                                                                research_mod.notes_as_instructions(project.research)).strip()})
    from .ai import roles
    ctx.step("Stratège et directeur créatif : objectif, public, accroche, moment fort", 0.88)
    brief, brief_by = roles.brief(project, segments, settings)
    if settings.style != "pvp":
        settings = settings.model_copy(update={"instructions": (settings.instructions + "\n" +
                                                                roles.brief_instructions(brief)).strip()})
    director = dir_mod.get_director()
    ctx.step("Monteur : réalisation du montage" + (" (Claude)" if director.name == "claude" else ""), 0.9)
    order = [m.id for m in project.media]
    # Les commentaires survivent au re-montage et le guident (sauf ceux que l'IA n'a pas compris).
    notes = [c for c in project.timeline.comments if c.status != "incompris"]
    old_comments = project.timeline.comments
    try:
        if settings.style == "pvp":
            # Edit PvP : calé sur les coups et le tempo, construit localement (calcul précis au centième).
            tl = pvp_mod.build(project.media, {m.id: (storage.load_signals(project.id, m.id) or {}).get("hits") or []
                                               for m in project.media}, settings, project.name, music)
            if not tl.clips:
                project.warnings.append(tl.summary + " Montage classique utilisé à la place.")
                tl = dir_mod.HeuristicDirector().direct(segments, settings.model_copy(update={"style": "short"}),
                                                        order, music)
        elif director.name == "claude" and notes:
            lines = [comments_mod.context_line(project.timeline, c, segments, k + 1) for k, c in enumerate(notes)]
            tl = director.direct(segments, settings, order, music, comment_lines=lines)
            for c, r in zip(notes, director.last_replies):
                c.status, c.reply = "applique", r
        else:
            tl = director.direct(segments, settings, order, music)
    except Exception as e:
        if director.name != "claude":
            raise
        log.warning("Réalisateur Claude en échec, repli heuristique : %s", e)
        project.warnings.append(f"Réalisateur Claude indisponible ({e}) : montage heuristique utilisé.")
        tl = dir_mod.HeuristicDirector().direct(segments, settings, order, music)
    if tl.director == "heuristique" or (tl.director == "claude" and not _has_effects(tl)):
        ctx.step("Habillage : transitions, effets, bruitages, titres", 0.97)
        tl = decorate(tl, segments, settings, project.name)
    tl.comments = old_comments
    tl.brand = project.timeline.brand  # l'identité de marque survit au re-montage
    if tl.director == "claude":
        comments_mod.carry_overrides(project.timeline, tl)
        zone = [c for c in notes if comments_mod.is_region_action(c)]
        if zone:
            comments_mod.apply_all(tl, segments, settings, {m.id: m.duration for m in project.media}, zone, _faces(project))
    if notes and tl.director != "claude":  # heuristique ou pvp
        ctx.step("Application de tes commentaires", 0.98)
        comments_mod.apply_all(tl, segments, settings, {m.id: m.duration for m in project.media}, notes, _faces(project))
    if tl.clips and settings.style != "pvp":
        ctx.step("Plans calés sur les phrases, projecteur sur ce que montre la voix", 0.981)
        tl.voice_focus = voice_focus(project, tl, transcripts_for(project))
    if project.reference and tl.clips and settings.style != "pvp":
        from .ai import reference as ref_mod
        ctx.step("Rythme calé sur la vidéo de référence", 0.982)
        tl, tl.reference_match = ref_mod.match(tl, project.reference, transcripts_for(project))
    project.timeline = tl
    tl = polish(project, tl, segments, settings, music, ctx, director, 0.985)
    ctx.step("Storyboard, montage et publication : vérification rôle par rôle", 0.995)
    try:
        tl.plan = roles.before_steps(brief, brief_by) + roles.after_steps(project, tl, segments, settings, brief)
    except Exception as e:  # le plan affiché ne doit jamais faire échouer le montage
        log.warning("Plan de réalisation incomplet : %s", e)
    saved = settings.model_copy(update={"instructions": project.settings.instructions})

    def apply(p: Project) -> None:
        p.settings, p.warnings, p.timeline = saved, project.warnings, tl
    storage.update_project(project_id, apply)
    rv = f", note du relecteur {tl.review.score:.1f}/10" if tl.review else ""
    ctx.step(f"Montage prêt : {len(tl.clips)} plans, {tl.duration:.0f} s{rv}", 1.0)
    return tl


def polish(project: Project, tl: Timeline, segments: list[Segment], settings: EditSettings, music: str | None,
           ctx: JobContext, director=None, at: float = 0.9, max_rounds: int = review_mod.MAX_ROUNDS) -> Timeline:
    """Après le montage : pic musical calé, contrôle qualité avec corrections automatiques, puis relecture
    indépendante. Sous la note de passage, le réalisateur reprend le montage avec les remarques (3 tours au plus)."""
    transcripts = transcripts_for(project)
    if tl.music.track and settings.style != "pvp":
        msg = soundsheet.align_music_peak(tl, soundsheet.music_profile(tl.music.track))
        if msg:
            log.info(msg)
    reviewer = review_mod.get_reviewer()
    director = director or dir_mod.get_director()
    history: list[float] = []
    fixed: list[str] = list(tl.quality.fixed) if tl.quality else []
    rev = None
    for rnd in range(1, max_rounds + 1):
        ctx.step(f"Contrôle qualité et relecture (tour {rnd})", min(0.995, at + 0.004 * rnd))
        tl = quality_mod.check_and_fix(tl, transcripts, settings)
        fixed += tl.quality.fixed
        tl.quality.issues += soundsheet.sync_checks(project, tl, transcripts)
        tl.quality.score = quality_mod.score_of(tl.quality.issues)
        instructions = ""
        rev = None
        if reviewer:
            try:
                sheet = soundsheet.sheet(project, tl, transcripts)
                rev, instructions = reviewer.review(project, tl, transcripts, tl.quality, sheet, settings.style)
            except Exception as e:
                log.warning("Relecture Claude impossible, relecteur local : %s", e)
        rev = rev or review_mod.local_review(tl.quality)
        history.append(rev.score)
        if rev.score >= review_mod.PASS_SCORE or rnd == max_rounds or director.name != "claude" or not (
                instructions or rev.remarks):
            break
        consigne = instructions or "; ".join(f"{r.problem} ({r.fix})" for r in rev.remarks[:8])
        try:
            keep = (tl.comments, tl.music)
            new = director.command(f"Retouches demandées par le relecteur : {consigne}", tl, segments, settings, music)
            new.comments, new.music = keep[0], keep[1]
            comments_mod.carry_overrides(tl, new)
            tl = new
        except Exception as e:
            log.warning("Retouche après relecture impossible : %s", e)
            break
    tl.quality.fixed = list(dict.fromkeys(fixed))
    rev.rounds, rev.history = len(history), history
    tl.review = rev
    return tl


def ai_command(project_id: str, instruction: str, ctx: JobContext) -> dict:
    project = storage.load_project(project_id)
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    order = [m.id for m in project.media]
    music = project.timeline.music.track or (pick_music(project, project.settings.style)
                                              if project.settings.music else None)
    ctx.step("Application de la consigne", 0.2)
    director = dir_mod.get_director()
    msg = ""
    if director.name == "claude":
        if project.research:  # les faits trouvés sur le web guident aussi les retouches
            from .ai import research as research_mod
            instruction = research_mod.notes_as_instructions(project.research) + "\n\nConsigne : " + instruction
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
    tl.comments = project.timeline.comments
    if tl is not project.timeline:
        comments_mod.carry_overrides(project.timeline, tl)
    def apply(p: Project) -> None:
        p.settings, p.timeline = project.settings, tl
    storage.update_project(project_id, apply)
    ctx.step(msg or "Consigne appliquée", 1.0)
    return {"message": msg, "director": director.name}


def export(project_id: str, quality: str, ctx: JobContext, fps: int | None = None, smooth: bool = False,
           container: str = "mp4", tl: Timeline | None = None, fmt: str | None = None, label: str | None = None) -> str:
    project = storage.load_project(project_id)
    tl_is_active = tl is None and not fmt
    tl = (tl or project.timeline).model_copy(deep=True)
    if fmt and fmt != tl.format:
        tl.format = fmt  # le recadrage automatique suit le visage dans le nouveau format
    out_dir = storage.exports_dir(project.id)
    idx = len([p for p in out_dir.iterdir() if p.suffix in (".mp4", ".webm", ".mov", ".gif")]) + 1
    base = label or tl.title or project.name
    safe = "".join(ch for ch in base if ch.isalnum() or ch in " -_·").strip()[:70] or "montage"
    rate = fps or render_mod.source_fps(project, tl)
    ext = "gif" if container == "gif" else render_mod.CONTAINERS.get(container, render_mod.CONTAINERS["mp4"])[0]
    out = out_dir / f"{idx:02d} - {safe} ({tl.format.replace(':', 'x')} {quality} {rate}fps).{ext}"
    work = storage.cache_dir(project.id) / f"render_{idx}"
    work.mkdir(parents=True, exist_ok=True)
    music = tl.music.track if tl.music.track and Path(tl.music.track).exists() else None
    ctx.step(f"Rendu {quality} à {rate} i/s" + (" avec interpolation" if smooth else ""), 0.01)
    render_mod.render(project, tl, transcripts_for(project), out.resolve(), work.resolve(), quality, music,
                      ctx.sub(0.01, 0.99, f"Rendu {quality} à {rate} i/s"), fps=rate, smooth=smooth,
                      container=container)
    if container != "gif" and tl_is_active:
        ctx.step("Contrôle de la vidéo finie (volume, flashs, début)", 0.995)
        try:
            issues, measured = quality_mod.render_checks(out, tl, rate)
        except Exception as e:  # le contrôle ne doit jamais faire échouer un export
            log.warning("Contrôle du rendu impossible : %s", e)
            issues, measured = [], {}

        def save(p: Project) -> None:
            p.exports.append(out.name)
            rep = quality_mod.plan_checks(p.timeline, transcripts_for(p), p.settings)
            rep.issues += issues
            rep.measured, rep.rendered = measured, True
            rep.fixed = list(p.timeline.quality.fixed) if p.timeline.quality else []
            rep.score = quality_mod.score_of(rep.issues)
            p.timeline.quality = rep
        storage.update_project(project_id, save)
    else:
        storage.update_project(project_id, lambda p: p.exports.append(out.name))
    ctx.step("Export terminé", 1.0)
    return out.name


def publish_kit(project_id: str, ctx: JobContext) -> dict:
    project = storage.load_project(project_id)
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    ctx.step("Titres, description et chapitres" + (" (Claude)" if config.claude_available() else ""), 0.2)
    work = storage.cache_dir(project.id) / "publish"
    work.mkdir(parents=True, exist_ok=True)
    kit = publish_mod.build_kit(project, segments, storage.exports_dir(project.id), work.resolve())
    storage.update_project(project_id, lambda p: setattr(p, "publish_kit", kit))
    ctx.step("Kit de publication prêt", 1.0)
    return kit


def apply_comments(project_id: str, ctx: JobContext) -> dict:
    """L'IA lit les commentaires ouverts et modifie le montage en conséquence."""
    project = storage.load_project(project_id)
    tl = project.timeline
    open_c = [c for c in tl.comments if c.status == "ouvert"]
    if not open_c:
        raise ValueError("Aucun commentaire en attente.")
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    director = dir_mod.get_director()
    ctx.step(f"L'IA lit tes {len(open_c)} commentaire(s)" + (" (Claude)" if director.name == "claude" else ""), 0.2)
    durations = {m.id: m.duration for m in project.media}
    if director.name == "claude":
        # Les actions sur une zone de l'image (flou, cadre, zoom…) sont appliquées au pixel près en local ;
        # le reste part à Claude, avec l'image de chaque zone sélectionnée.
        zone = [c for c in open_c if comments_mod.is_region_action(c)]
        if zone:
            comments_mod.apply_all(tl, segments, project.settings, durations, zone, _faces(project))
        rest = [c for c in open_c if c not in zone]
        lines = [comments_mod.context_line(tl, c, segments, k + 1) for k, c in enumerate(rest)]
        images = []
        for k, c in enumerate(rest):
            if c.region is not None and c.media_id:
                try:
                    m = project.media_by_id(c.media_id)
                    images.append((k + 1, comments_mod.frame_with_region(m.proxy or m.path, c.src_t, c.region)))
                except Exception as e:
                    log.warning("Image de zone impossible : %s", e)
        try:
            if not rest:
                raise StopIteration
            new_tl, replies = director.apply_comments(tl, lines, segments, project.settings, tl.music.track, images)
            comments_mod.carry_overrides(tl, new_tl)
            new_tl.comments = tl.comments
            new_tl.reframe = tl.reframe
            for k, c in enumerate(rest):
                c.status, c.reply = "applique", (replies[k] if k < len(replies) else "Pris en compte.")
            tl = new_tl
        except StopIteration:
            pass
        except Exception as e:
            log.warning("Commentaires via Claude impossibles, interprète local : %s", e)
            comments_mod.apply_all(tl, segments, project.settings, durations, None, _faces(project))
    else:
        comments_mod.apply_all(tl, segments, project.settings, {m.id: m.duration for m in project.media}, None, _faces(project))
    storage.update_project(project_id, lambda p: setattr(p, "timeline", tl))
    ok = sum(c.status == "applique" for c in open_c)
    msg = f"{ok} commentaire(s) appliqué(s)" + (f", {len(open_c) - ok} non compris" if ok < len(open_c) else "")
    ctx.step(msg, 1.0)
    return {"message": msg}


# ---------------------------------------------------------------------------
# Versions du montage
# ---------------------------------------------------------------------------

def _version(project: Project, vid: str):
    for v in project.versions:
        if v.id == vid:
            return v
    raise KeyError(vid)


def version_duplicate(project: Project, name: str | None = None):
    """Copie la version active sous un nouveau nom et bascule dessus."""
    from .models import Version
    cur = project.ensure_versions()
    cur.timeline = project.timeline.model_copy(deep=True)
    v = Version(name=(name or f"{cur.name} (copie)").strip()[:60])
    project.versions.append(v)
    version_activate(project, v.id, v.timeline or cur.timeline)
    return v


def version_activate(project: Project, vid: str, timeline: Timeline | None = None) -> None:
    cur = project.ensure_versions()
    target = _version(project, vid)
    if target.id == cur.id:
        return
    cur.timeline = project.timeline.model_copy(deep=True)
    project.timeline = (timeline or target.timeline or Timeline()).model_copy(deep=True)
    target.timeline = None
    project.active_version = target.id


def version_delete(project: Project, vid: str) -> None:
    project.ensure_versions()
    if len(project.versions) <= 1:
        raise ValueError("Impossible de supprimer la dernière version.")
    if vid == project.active_version:
        other = next(v for v in project.versions if v.id != vid)
        version_activate(project, other.id)
    project.versions = [v for v in project.versions if v.id != vid]


# ---------------------------------------------------------------------------
# Shorts automatiques : plusieurs clips courts tirés des rushes, avec score viral
# ---------------------------------------------------------------------------

def _windows(segments: list[Segment], target: float, max_gap: float = 4.0):
    """Fenêtres de segments consécutifs d'un même rush, de la durée d'un short."""
    by_media: dict[str, list[Segment]] = {}
    for s in sorted(segments, key=lambda s: (s.media_id, s.start)):
        if not s.duplicate_of:
            by_media.setdefault(s.media_id, []).append(s)
    lo = max(6.0, target * 0.45)
    for mid, segs in by_media.items():
        for i in range(len(segs)):
            win, dur = [], 0.0
            for s in segs[i:]:
                if win and s.start - win[-1].end > max_gap:
                    break
                if dur + s.duration > target * 1.15 and win:
                    break
                win.append(s)
                dur += s.duration
            if dur >= lo:
                yield mid, win


def virality(win: list[Segment]) -> tuple[int, list[str]]:
    """Score 0-100 : accroche du début, intensité, réactions, rythme, fin nette."""
    import numpy as np
    first = win[0]
    hook = max(first.features.get("hook", 0.0), win[1].features.get("hook", 0.0) if len(win) > 1 else 0.0)
    mean = float(np.mean([s.score for s in win]))
    energy = float(np.mean([s.features.get("energy", 0.0) for s in win]))
    dur = sum(s.duration for s in win)
    peaks = sum(s.features.get("peak", 0.0) for s in win) / max(1.0, dur / 15)
    rate = float(np.mean([s.features.get("speech_rate", 0.0) for s in win if s.kind == "speech"] or [0.0]))
    motion = float(np.mean([s.features.get("motion", 0.0) for s in win]))
    ends = 1.0 if (win[-1].text.rstrip()[-1:] in ".!?…" or win[-1].kind == "action") else 0.4
    v = (0.28 * min(1.0, hook + first.score) + 0.24 * mean + 0.14 * energy + 0.12 * min(1.0, peaks)
         + 0.1 * min(1.0, rate / 3.2 + motion) + 0.12 * ends)
    reasons = []
    if hook >= 0.6:
        reasons.append("accroche forte dès les premières secondes")
    if energy > 0.55:
        reasons.append("voix énergique")
    if peaks >= 0.5:
        reasons.append("réactions (rires, cris)")
    if rate > 2.6 or motion > 0.45:
        reasons.append("rythme soutenu")
    if ends >= 1:
        reasons.append("se termine sur une phrase complète")
    return int(round(100 * min(1.0, v))), reasons or ["passage le plus solide disponible"]


def make_shorts(project_id: str, ctx: JobContext, count: int = 3, target: float = 30.0,
                fmt: str = "9:16") -> list[dict]:
    from .models import Version
    project = storage.load_project(project_id)
    if not project.media:
        raise ValueError("Ajoute au moins une vidéo.")
    n = len(project.media)
    for i, m in enumerate(project.media):
        if not m.analyzed:
            ctx.check()
            analyze_media(project, m, ctx, 0.6 * i / n, 0.6 * (i + 1) / n)
            _save_media(project)
    ctx.step("Recherche des passages les plus partageables", 0.65)
    settings = project.settings.model_copy(update={"style": "short", "format": fmt, "target_duration": target})
    segments = all_segments(project, settings)
    cands = [(mid, win, *virality(win)) for mid, win in _windows(segments, target)]
    cands.sort(key=lambda x: -x[2])
    chosen: list[tuple] = []
    for c in cands:
        mid, win = c[0], c[1]
        a, b = win[0].start, win[-1].end
        if all(not (x[0] == mid and a < x[1][-1].end and b > x[1][0].start) for x in chosen):
            chosen.append(c)
        if len(chosen) >= count:
            break
    if not chosen:
        raise ValueError("Pas assez de matière pour un short (il faut au moins 6 secondes de passage continu).")
    project.ensure_versions()
    out = []
    names = {m.id: m.filename for m in project.media}
    for k, (mid, win, score, reasons) in enumerate(chosen, 1):
        ctx.step(f"Montage du short {k}/{len(chosen)}", 0.7 + 0.28 * (k - 1) / len(chosen))
        ids = {s.id for s in win}
        sub = [s for s in segments if s.id in ids]
        tl = dir_mod.HeuristicDirector().direct(sub, settings, [mid], None)
        tl = decorate(tl, sub, settings, project.name)
        hook = next((s.text for s in win if s.text), "")
        tl.title = " ".join(hook.split()[:6]) or f"{project.name} — short {k}"
        tl.summary = f"Short tiré de {names.get(mid, 'la vidéo')} ({win[0].start:.0f}–{win[-1].end:.0f} s)."
        v = Version(name=f"Short {k} · {score}/100", timeline=tl, score=score, note=", ".join(reasons).capitalize() + ".")
        project.versions.append(v)
        out.append({"id": v.id, "name": v.name, "score": score, "note": v.note, "duration": round(tl.duration, 1)})
    new = [v for v in project.versions if v.id in {o["id"] for o in out}]

    def apply(p: Project) -> None:
        p.ensure_versions()
        p.versions.extend(new)
    storage.update_project(project_id, apply)
    ctx.step(f"{len(out)} short(s) prêt(s) dans les versions", 1.0)
    return out


# ---------------------------------------------------------------------------
# Préréglages d'export par réseau et file d'export
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "tiktok": {"label": "TikTok", "format": "9:16", "quality": "1080p", "container": "mp4"},
    "reels": {"label": "Instagram Reels", "format": "9:16", "quality": "1080p", "container": "mp4"},
    "shorts": {"label": "YouTube Shorts", "format": "9:16", "quality": "1080p", "container": "mp4"},
    "youtube": {"label": "YouTube 1080p", "format": "16:9", "quality": "1080p", "container": "mp4"},
    "youtube_4k": {"label": "YouTube 4K", "format": "16:9", "quality": "2160p", "container": "mp4"},
    "instagram_carre": {"label": "Instagram (carré)", "format": "1:1", "quality": "1080p", "container": "mp4"},
    "x": {"label": "X (Twitter)", "format": "16:9", "quality": "1080p", "fps": 30, "container": "mp4"},
    "web": {"label": "Web (WebM)", "quality": "1080p", "container": "webm"},
    "prores": {"label": "ProRes (montage pro, .mov)", "quality": "1080p", "container": "mov"},
    "gif": {"label": "GIF animé", "quality": "720p", "fps": 15, "container": "gif"},
}


class _Scoped:
    """Sous-tâche : ramène la progression 0..1 d'une étape sur une tranche de la tâche."""

    def __init__(self, ctx: JobContext, lo: float, hi: float):
        self.ctx, self.lo, self.hi = ctx, lo, hi

    def _m(self, p: float) -> float:
        return self.lo + (self.hi - self.lo) * p

    def step(self, message: str, progress: float) -> None:
        self.ctx.step(message, self._m(progress))

    def sub(self, a: float, b: float, message: str | None = None):
        return self.ctx.sub(self._m(a), self._m(b), message)


def export_preset(project_id: str, preset: str, ctx: JobContext, version_ids: list[str] | None = None) -> list[str]:
    """Exporte une ou plusieurs versions avec un préréglage (file d'export : les unes après les autres)."""
    p = PRESETS[preset]
    project = storage.load_project(project_id)
    project.ensure_versions()
    ids = version_ids or [project.active_version]
    out = []
    for k, vid in enumerate(ids):
        project = storage.load_project(project_id)
        v = _version(project, vid)
        tl = project.timeline if vid == project.active_version else v.timeline
        if not tl or not tl.clips:
            continue
        ctx.step(f"{v.name} : {p['label']}", k / len(ids))
        sub = _Scoped(ctx, k / len(ids), (k + 1) / len(ids))
        out.append(export(project_id, p["quality"], sub, p.get("fps"), False, p["container"], tl=tl,
                          fmt=p.get("format"), label=f"{v.name} - {p['label']}" if len(project.versions) > 1 else p["label"]))
    if not out:
        raise ValueError("Aucune version à exporter (timelines vides).")
    ctx.step(f"{len(out)} fichier(s) exporté(s)", 1.0)
    return out


# ---------------------------------------------------------------------------
# Modèles de style réutilisables (look, sous-titres, musique, logo…)
# ---------------------------------------------------------------------------

def _templates_path() -> Path:
    return config.DATA_DIR / "templates.json"


def list_templates() -> list[dict]:
    import json
    p = _templates_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_templates(items: list[dict]) -> None:
    import json
    _templates_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def save_template(project: Project, name: str) -> dict:
    import shutil
    from .models import new_id
    tl = project.timeline
    adir = config.DATA_DIR / "template_assets"
    adir.mkdir(parents=True, exist_ok=True)
    images = []
    for g in tl.images:
        src = storage.assets_dir(project.id) / Path(g.asset).name
        if src.exists():
            shutil.copy2(src, adir / src.name)
            images.append(g.model_dump())
    t = {"id": new_id("tpl_"), "name": name.strip()[:60] or "Mon modèle", "style": project.settings.style,
         "instructions": project.settings.instructions, "format": tl.format, "captions": tl.captions,
         "look": tl.look, "reframe": tl.reframe, "music_volume": tl.music.volume, "ducking": tl.music.ducking,
         "music_track": tl.music.track, "images": images,
         "brand": tl.brand.model_dump() if tl.brand else None}
    items = [x for x in list_templates() if x["name"] != t["name"]] + [t]
    _save_templates(items)
    return t


def delete_template(tid: str) -> None:
    _save_templates([x for x in list_templates() if x["id"] != tid])


def apply_template(project: Project, tid: str) -> Project:
    import shutil
    from .models import ImageItem, new_id
    t = next((x for x in list_templates() if x["id"] == tid), None)
    if not t:
        raise KeyError(tid)
    tl = project.timeline
    tl.format, tl.captions, tl.look, tl.reframe = t["format"], t["captions"], t["look"], t["reframe"]
    tl.music.volume, tl.music.ducking = t["music_volume"], t["ducking"]
    if t.get("music_track") and Path(t["music_track"]).exists():
        tl.music.track = t["music_track"]
    adir = config.DATA_DIR / "template_assets"
    keep = [g for g in tl.images if g.duration is not None]  # les stickers ponctuels restent
    for g in t.get("images", []):
        src = adir / Path(g["asset"]).name
        if src.exists():
            shutil.copy2(src, storage.assets_dir(project.id) / src.name)
            keep.append(ImageItem(**{**g, "id": new_id("g_")}))
    tl.images = keep
    if t.get("brand"):
        from .models import Brand
        tl.brand = Brand(**t["brand"])
    project.settings = project.settings.model_copy(update={
        "style": t["style"], "instructions": t["instructions"], "format": t["format"], "captions": t["captions"]})
    return project


# ---------------------------------------------------------------------------
# Traduction des sous-titres
# ---------------------------------------------------------------------------

def translate_captions(project_id: str, lang: str | None, ctx: JobContext) -> dict:
    """Traduit les lignes de sous-titres du montage (avec cache) et bascule les sous-titres dans cette langue.
    lang=None remet les sous-titres dans la langue parlée."""
    from .ai import translate as trl
    project = storage.load_project(project_id)
    if lang:
        ctx.step(f"Traduction des sous-titres en {trl.LANGUAGES.get(lang, lang)}", 0.1)
        lines = cap_mod.build_lines(project.timeline.model_copy(update={"caption_language": None}),
                                    transcripts_for(project))
        known = storage.load_translations(project_id, lang)
        todo = list(dict.fromkeys(ln.text for ln in lines if ln.text not in known))
        if not lines:
            raise ValueError("Aucun sous-titre à traduire : il faut des paroles transcrites dans le montage.")
        if todo:
            storage.save_translations(project_id, lang, trl.translate_lines(todo, lang))
    storage.update_project(project_id, lambda p: setattr(p.timeline, "caption_language", lang))
    ctx.step("Sous-titres traduits" if lang else "Sous-titres dans la langue d'origine", 1.0)
    return {"language": lang}


def _dub_lines(tl: Timeline, transcripts: dict[str, Transcript]):
    """Phrases plus longues que les sous-titres : la voix lit d'une traite et respire aux vraies pauses."""
    return cap_mod.group_lines(cap_mod.timeline_words(tl, transcripts), 18, 120, max_gap=0.8)


def dub_needs_claude(project: Project, lang: str) -> bool:
    """Le doublage n'appelle Claude que pour traduire des phrases pas déjà en cache ni déjà dans la bonne langue."""
    transcripts = transcripts_for(project)
    if {transcripts[c.media_id].language for c in project.timeline.clips if c.media_id in transcripts} == {lang}:
        return False
    known = storage.load_translations(project.id, lang)
    tl = project.timeline.model_copy(update={"caption_language": None})
    return any(ln.text not in known for ln in _dub_lines(tl, transcripts))


def dub_voice(project_id: str, lang: str | None, ctx: JobContext, original_volume: float = 0.12) -> dict:
    """Doublage IA : traduit les phrases du montage, les fait lire par une voix de synthèse calée sur le
    moment où elles sont dites, et baisse la voix d'origine. lang=None retire le doublage."""
    from .ai import translate as trl
    from .ai import tts
    from .ai import voicecheck as vcheck
    from .models import AudioItem
    project = storage.load_project(project_id)

    if not lang:
        def undo(p: Project) -> None:
            p.timeline.audio = [a for a in p.timeline.audio if not a.dub]
            for c in p.timeline.clips:
                c.volume = 1.0
            p.timeline.dub_language = None
        storage.update_project(project_id, undo)
        ctx.step("Voix d'origine rétablie", 1.0)
        return {"language": None, "lines": 0}

    name = trl.LANGUAGES.get(lang, lang)
    if not tts.engine_for(lang):
        raise ValueError("Aucune voix de synthèse pour cette langue : installe espeak-ng, ou Piper avec une voix "
                         f"dans {tts.voices_dir()}.")
    tl = project.timeline.model_copy(update={"caption_language": None})
    transcripts = transcripts_for(project)
    lines = _dub_lines(tl, transcripts)
    if not lines:
        raise ValueError("Rien à doubler : il faut des paroles transcrites dans le montage.")
    ctx.step(f"Traduction des phrases en {name}", 0.05)
    spoken = {transcripts[c.media_id].language for c in tl.clips if c.media_id in transcripts}
    same = spoken == {lang}  # déjà dans cette langue : la voix relit simplement le texte d'origine
    known = {} if same else storage.load_translations(project_id, lang)
    todo = [] if same else list(dict.fromkeys(ln.text for ln in lines if ln.text not in known))
    if todo:
        known.update(trl.translate_lines(todo, lang))
        storage.save_translations(project_id, lang, known)

    d = storage.audio_dir(project_id)
    for old in d.glob(f"dub_{lang}_*.wav"):
        old.unlink(missing_ok=True)
    total = tl.duration
    items: list[AudioItem] = []
    engine = None
    for i, ln in enumerate(lines):
        text = (known.get(ln.text) or ln.text).strip()
        if not text:
            continue
        ctx.step(f"Voix {i + 1}/{len(lines)}", 0.1 + 0.85 * i / len(lines))
        nxt = lines[i + 1].start if i + 1 < len(lines) else total
        slot = max(0.4, min(nxt, total) - ln.start - 0.05)
        raw = d / f"dub_{lang}_{i:04d}_raw.wav"
        out = d / f"dub_{lang}_{i:04d}.wav"
        engine = tts.synthesize(text, lang, raw)
        dur = float(ffmpeg.probe(raw).get("duration") or 0.0)
        tempo = min(1.6, dur / slot) if dur > slot else 1.0
        # La phrase doublée doit tenir dans le temps de la phrase d'origine : on accélère un peu si besoin.
        ffmpeg.run(["-i", str(raw), "-af", f"atempo={tempo:.4f},aresample=48000", "-ac", "2", str(out)])
        raw.unlink(missing_ok=True)
        dur = float(ffmpeg.probe(out).get("duration") or dur / tempo)
        # Réécoute : la phrase doublée doit se comprendre, et chaque mot est horodaté pour les sous-titres.
        words, check = vcheck.verify(out, text, lang)
        items.append(AudioItem(asset=out.name, start=round(ln.start, 3), src_in=0.0, src_out=round(dur, 3),
                               lane=3, kind="voix", volume=1.0, fade_in=0.02, fade_out=0.08,
                               label=f"Doublage {name} {i + 1}", dub=lang, text=text, words=words, check=check))

    def apply(p: Project) -> None:
        p.timeline.audio = [a for a in p.timeline.audio if not a.dub] + items
        for c in p.timeline.clips:
            c.volume = original_volume
        p.timeline.dub_language = lang
    storage.update_project(project_id, apply)
    bad = sum(1 for a in items if a.check and a.check.get("ok") is False)
    ctx.step(f"Doublage en {name} prêt ({engine})" + (f", {bad} phrase(s) mal comprise(s) à la réécoute" if bad else ""), 1.0)
    return {"language": lang, "lines": len(items), "engine": engine, "misheard": bad}


def ai_voiceover(project_id: str, text: str, start: float, lang: str, ctx: JobContext, lane: int = 2) -> dict:
    """Voix off IA : lit un texte avec une voix de synthèse, la réécoute, et la pose sur une piste audio."""
    from .ai import tts
    from .ai import voicecheck as vcheck
    from .models import AudioItem, new_id
    text = text.strip()
    if not text:
        raise ValueError("Écris le texte que la voix off doit lire.")
    if not tts.engine_for(lang):
        raise ValueError("Aucune voix de synthèse pour cette langue : installe espeak-ng, ou Piper avec une voix "
                         f"dans {tts.voices_dir()}.")
    d = storage.audio_dir(project_id)
    out = d / f"voix_ia_{new_id('')}.wav"
    raw = out.with_name(out.stem + "_raw.wav")
    ctx.step("Lecture du texte par la voix de synthèse", 0.2)
    engine = tts.synthesize(text, lang, raw)
    ffmpeg.run(["-i", str(raw), "-af", "aresample=48000", "-ac", "2", str(out)])
    raw.unlink(missing_ok=True)
    dur = float(ffmpeg.probe(out).get("duration") or 0.0)
    ctx.step("Réécoute de la voix (compréhension et instant de chaque mot)", 0.6)
    words, check = vcheck.verify(out, text, lang)
    item = AudioItem(asset=out.name, start=round(max(0.0, start), 3), src_in=0.0, src_out=round(dur, 3), lane=lane,
                     kind="voix", label="Voix off IA", text=text, words=words, check=check)

    def apply(p: Project) -> None:
        p.timeline.audio.append(item)
    storage.update_project(project_id, apply)
    verdict = ("comprise à la réécoute" if check.get("ok") else
               "mal comprise à la réécoute" if check.get("ok") is False else "non réécoutée (Whisper absent)")
    ctx.step(f"Voix off IA posée ({dur:.1f} s, {engine}), {verdict}", 1.0)
    return {"id": item.id, "duration": dur, "engine": engine, "check": check}


def review_job(project_id: str, ctx: JobContext) -> dict:
    """Relance contrôle qualité et relecture sur le montage actuel (avec retouches si Claude est là)."""
    project = storage.load_project(project_id)
    if not project.timeline.clips:
        raise ValueError("Pas encore de montage à relire.")
    segments = storage.load_segments(project.id) or all_segments(project, project.settings)
    music = project.timeline.music.track
    tl = polish(project, project.timeline.model_copy(deep=True), segments, project.settings, music, ctx, at=0.1)

    def apply(p: Project) -> None:
        tl.comments = p.timeline.comments
        p.timeline = tl
    storage.update_project(project_id, apply)
    ctx.step(f"Relecture : {tl.review.score:.1f}/10 en {tl.review.rounds} tour(s)", 1.0)
    return {"score": tl.review.score, "rounds": tl.review.rounds, "quality": tl.quality.score}


def _narrow(project: Project, tl: Timeline) -> set[str]:
    """Rushes recadrés sur le visage (format plus étroit) : on n'y ajoute pas de zoom animé."""
    a, b = (int(x) for x in tl.format.split(":"))
    return {m.id for m in project.media if tl.reframe and m.height and m.width / m.height > a / b + 0.01}


def voice_focus(project: Project, tl: Timeline, transcripts: dict) -> list[str]:
    """Plans calés sur la durée des phrases, puis projecteur sur ce que la voix désigne (sur place)."""
    from .ai import voicefocus as vf
    notes = vf.fit_to_sentences(tl, transcripts)
    try:
        notes += vf.add_spotlights(project, tl, transcripts, _faces(project), vf.get_locator(), _narrow(project, tl))
    except Exception as e:  # le projecteur est un plus : il ne doit jamais faire échouer le montage
        log.warning("Projecteur impossible : %s", e)
    return notes


def voice_focus_job(project_id: str, ctx: JobContext) -> dict:
    """Relance sur le montage actuel : retire les projecteurs posés par l'IA, puis recale et repose."""
    project = storage.load_project(project_id)
    transcripts = transcripts_for(project)
    ctx.step("Recherche de ce que montre la voix", 0.3)
    tl = project.timeline.model_copy(deep=True)
    for c in tl.clips:
        if any(m.kind == "projecteur" and m.push_in for m in c.masks):
            c.keyframes = []  # le zoom vers la zone venait du projecteur
        c.masks = [m for m in c.masks if m.kind != "projecteur"]
    notes = voice_focus(project, tl, transcripts)
    tl.voice_focus = notes

    def apply(p: Project) -> None:
        tl.comments = p.timeline.comments
        p.timeline = tl
    storage.update_project(project_id, apply)
    n = sum(1 for x in notes if x.startswith("projecteur"))
    ctx.step(f"{n} projecteur(s), {len(notes) - n} calage(s) sur les phrases", 1.0)
    return {"notes": notes}


def quality_job(project_id: str, fix: bool, ctx: JobContext) -> dict:
    """Contrôle du plan (et corrections automatiques si fix) sur le montage actuel."""
    project = storage.load_project(project_id)
    transcripts = transcripts_for(project)
    ctx.step("Contrôle qualité du montage", 0.3)

    def apply(p: Project) -> None:
        if fix:
            p.timeline = quality_mod.check_and_fix(p.timeline, transcripts, p.settings)
        else:
            old = p.timeline.quality
            rep = quality_mod.plan_checks(p.timeline, transcripts, p.settings)
            rep.fixed = old.fixed if old else []
            p.timeline.quality = rep
        p.timeline.quality.issues += soundsheet.sync_checks(p, p.timeline, transcripts)
        p.timeline.quality.score = quality_mod.score_of(p.timeline.quality.issues)
        p.timeline.review = None  # la note du relecteur portait sur l'état d'avant : à refaire
    storage.update_project(project_id, apply)
    q = storage.load_project(project_id).timeline.quality
    ctx.step(f"Contrôle qualité : {q.score}/100", 1.0)
    return {"score": q.score, "issues": len(q.issues), "fixed": q.fixed}


def propose_directions(project_id: str, platform: str, tone: str, audience: str, ctx: JobContext) -> dict:
    """Analyse les rushes puis propose 3 directions créatives (rangées dans le projet)."""
    from .ai import directions as dir_ai
    project = storage.load_project(project_id)
    if not project.media:
        raise ValueError("Ajoute au moins une vidéo avant de demander des directions.")
    n = len(project.media)
    for i, m in enumerate(project.media):
        analyze_media(project, m, ctx, 0.8 * i / n, 0.8 * (i + 1) / n)
        _save_media(project)
    ctx.step("Le directeur créatif prépare 3 directions" + (" (Claude)" if config.claude_available() else ""), 0.85)
    segments = all_segments(project, project.settings)
    found, who = dir_ai.propose(project, segments, platform, tone, audience)
    storage.update_project(project_id, lambda p: setattr(p, "directions", found))
    ctx.step("3 directions prêtes : choisis-en une", 1.0)
    return {"directions": [d.model_dump() for d in found], "by": who}
