"""Des « oreilles » pour l'IA : la feuille son/image du montage, et le pic musical calé sur le moment fort.

Claude ne peut pas écouter la vidéo. On lui décrit donc le son en mots, instant par instant, à côté de ce qui
est à l'écran : paroles, coups et impacts détectés dans les rushes, bruitages posés, temps forts de la musique.
Le réalisateur et le relecteur lisent cette feuille pour vérifier la synchro.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import config, storage
from ..analysis import hits as hits_an
from ..media import ffmpeg
from ..models import Project, Timeline, Transcript
from ..render import captions as cap
from . import quality


def _fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def music_profile(path: str | Path, max_s: float = 360.0) -> dict | None:
    """Tempo, premier temps et pic d'énergie (le moment le plus fort) d'une musique."""
    try:
        pcm = ffmpeg.decode_audio(path, config.AUDIO_SR)[: int(max_s * config.AUDIO_SR)]
    except Exception:
        return None
    if pcm.size < config.AUDIO_SR * 4:
        return None
    sr = config.AUDIO_SR
    bpm, first = hits_an.beat_grid(pcm, sr)
    hop = int(0.5 * sr)
    n = pcm.size // hop
    rms = np.sqrt(np.mean(pcm[: n * hop].astype(np.float32).reshape(n, hop) ** 2, axis=1) + 1e-12)
    k = 8  # énergie lissée sur 4 s : un passage fort, pas un coup isolé
    smooth = np.convolve(rms, np.ones(k) / k, mode="same")
    # La montée la plus nette : le « drop » ou le refrain. On ignore les 4 premières secondes.
    rise = np.zeros_like(smooth)
    rise[k:] = smooth[k:] - smooth[:-k]
    lo = min(8, n - 1)
    peak_i = lo + int(np.argmax(rise[lo:] + 0.5 * smooth[lo:]))
    peak = max(0.0, (peak_i - k / 2) * 0.5)  # milieu de la montée : là où le passage fort commence
    beat = 60.0 / bpm
    peak = first + round((peak - first) / beat) * beat  # posé sur un temps
    return {"bpm": bpm, "first_beat": first, "peak": round(max(0.0, peak), 3), "duration": pcm.size / sr}


def align_music_peak(tl: Timeline, profile: dict | None) -> str | None:
    """Décale le départ de la musique pour que son pic tombe sur le moment fort du montage."""
    best = quality.strongest(tl)
    if not profile or not best or not tl.music.track:
        return None
    _, t_strong = best
    if t_strong < 2.0:
        return None  # moment fort en ouverture : on garde le début du morceau
    start = profile["peak"] - t_strong
    if start < 0:
        return None  # le pic arrive trop tôt dans le morceau pour être retardé
    beat = 60.0 / profile["bpm"]
    # On garde la phase des temps : la musique démarre sur un temps.
    start = max(0.0, profile["first_beat"] + round((start - profile["first_beat"]) / beat) * beat)
    if profile["duration"] - start < tl.duration * 0.6:
        return None  # trop près de la fin du morceau
    tl.music.start = round(start, 3)
    return f"pic de la musique calé sur le moment fort ({_fmt(t_strong)})"


def events(project: Project, tl: Timeline, transcripts: dict[str, Transcript]) -> list[tuple[float, str]]:
    """Événements sonores du montage en temps timeline, chacun décrit en mots simples."""
    out: list[tuple[float, str]] = []
    t0 = 0.0
    for c in tl.clips:
        sig = storage.load_signals(project.id, c.media_id) or {}
        for h in sig.get("hits") or []:
            t, force = h[0], h[1] if len(h) > 1 else 0.5
            if c.src_in <= t < c.src_out:
                tt = t0 + c.src_to_local(t - c.src_in)
                out.append((tt, "impact fort" if force >= 0.7 else "coup sec"))
        t0 += c.duration
    for line in cap.group_lines(cap.timeline_words(tl, transcripts), 10, 70, max_gap=0.6):
        out.append((line.start, f"voix : « {line.text} »"))
    for s in tl.sfx:
        out.append((s.start, f"bruitage {s.kind}"))
    for a in tl.audio:
        out.append((a.start, f"{a.kind} ajoutée{' (doublage)' if a.dub else ''} : {a.label or a.asset}"))
    prof = None
    if tl.music.track:
        try:
            p = storage.safe_music_path(tl.music.track)
        except Exception:
            p = None
        prof = music_profile(p) if p else None
        if prof:
            beat = 60.0 / prof["bpm"]
            off = tl.music.start or 0.0
            # Temps forts de la musique : un par mesure (4 temps), et son pic.
            t = (prof["first_beat"] - off) % (4 * beat)
            while t < tl.duration:
                out.append((t, f"musique : temps fort ({prof['bpm']:.0f} BPM)"))
                t += 4 * beat
            pk = prof["peak"] - off
            if 0 <= pk < tl.duration:
                out.append((pk, "musique : PIC (passage le plus fort)"))
    return sorted(out, key=lambda e: e[0])


def screen_at(tl: Timeline, t: float) -> str:
    starts = quality._starts(tl)
    i = max(0, max((k for k, s in enumerate(starts) if s <= t + 1e-6), default=0))
    c = tl.clips[i] if tl.clips else None
    parts = []
    if c:
        cut = " (COUPE)" if i and abs(starts[i] - t) < 0.08 else ""
        parts.append(f"plan {i + 1}{cut}{' zoom' if c.zoom > 1.03 else ''}")
    parts += [f"texte « {x.text[:30]} »" for x in tl.texts if x.start <= t < x.start + x.duration]
    parts += [f"impact {x.kind}" for x in tl.impacts if abs(x.start - t) < 0.1]
    parts += ["plan de coupe" for o in tl.overlays if o.start <= t < o.start + o.duration]
    return ", ".join(parts)


def sheet(project: Project, tl: Timeline, transcripts: dict[str, Transcript], limit: int = 160) -> str:
    """Feuille unique : à chaque événement sonore, ce qui est à l'écran au même instant."""
    ev = events(project, tl, transcripts)
    best = quality.strongest(tl)
    rows = ["instant | son | à l'écran"]
    if best:
        rows.append(f"{_fmt(best[1])} | (moment fort du montage) | plan {best[0] + 1}")
    for t, what in ev[:limit]:
        rows.append(f"{_fmt(t)} | {what} | {screen_at(tl, t)}")
    if len(ev) > limit:
        rows.append(f"… {len(ev) - limit} événements de plus")
    return "\n".join(rows)


def sync_checks(project: Project, tl: Timeline, transcripts: dict[str, Transcript]) -> list:
    """Synchro son/image : les gros impacts sonores devraient tomber sur une coupe, un zoom ou un impact visuel."""
    from ..models import QualityIssue
    out = []
    starts = quality._starts(tl)[1:]
    visual = starts + [x.start for x in tl.impacts] + [s.start for s in tl.sfx]
    strong = [t for t, w in events(project, tl, transcripts) if w == "impact fort"]
    missed = [t for t in strong if min((abs(v - t) for v in visual), default=9) > 0.15]
    if len(strong) >= 3 and len(missed) > 0.7 * len(strong):
        out.append(QualityIssue(check="synchro", level="alerte", t=round(missed[0], 2),
                                message=f"{len(missed)} impacts sonores forts sans rien à l'image (coupe, zoom ou "
                                        "impact visuel) au même moment."))
    return out
