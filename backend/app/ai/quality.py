"""Règles de réalisation et contrôle qualité.

Deux contrôles, comme un chef monteur :
- `plan_checks` sur le plan de montage, avant le rendu : accroche dans les 3 premières secondes, moment fort
  préparé, plans trop longs sans mouvement, textes lisibles sur un téléphone, flashs, durée, bruitages calés ;
- `render_checks` sur la vidéo finie, mesuré avec FFmpeg : loudness (cible -14 LUFS), crête, flashs
  (règle anti-épilepsie : pas plus de 3 par seconde), début noir ou muet.

`apply_fixes` corrige seule ce qui peut l'être. Le rapport est rangé dans `Timeline.quality`.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ..media import ffmpeg
from ..models import EditSettings, QualityIssue, QualityReport, SfxItem, TextItem, Timeline, Transcript
from ..render import captions as cap

HOOK_LIMIT = 3.0  # l'accroche doit arriver avant 3 s
SPEECH_START_MAX = 1.2  # au-delà, le début paraît vide
LONG_SHOT = {"short": 6.0, "pvp": 5.0, "youtube": 12.0, "vlog": 10.0, "podcast": 40.0}
TEXT_MAX_CHARS = {  # (vertical, paysage) : au-delà, le texte devient trop petit ou trop long sur un téléphone
    "gros_texte": (18, 28), "titre": (28, 42), "bandeau": (34, 60), "legende": (40, 70),
    "texte_3d": (16, 24), "emoji": (6, 6),
}
LUFS_TARGET, LUFS_TOL, PEAK_MAX = -14.0, 2.0, -1.0
FLASH_DELTA = 0.2  # variation de luminosité (0..1) d'une image à l'autre comptée comme un flash
FLASH_MAX_PER_S = 3


def _starts(tl: Timeline) -> list[float]:
    out, t = [], 0.0
    for c in tl.clips:
        out.append(t)
        t += c.duration
    return out


def strongest(tl: Timeline) -> tuple[int, float] | None:
    """Plan le plus fort (meilleur score) et l'instant de son début sur la timeline."""
    if not tl.clips:
        return None
    st = _starts(tl)
    i = max(range(len(tl.clips)), key=lambda k: (tl.clips[k].score, -k))
    return i, st[i]


def _issue(check, level, message, t=None, fix=None, target=None) -> QualityIssue:
    return QualityIssue(check=check, level=level, message=message, t=None if t is None else round(t, 2),
                        fix=fix, target=target)


def plan_checks(tl: Timeline, transcripts: dict[str, Transcript], settings: EditSettings | None = None) -> QualityReport:
    settings = settings or EditSettings()
    issues: list[QualityIssue] = []
    if not tl.clips:
        return QualityReport(score=0, issues=[_issue("duree", "erreur", "Le montage est vide.")])
    starts = _starts(tl)
    total = tl.duration
    words = cap.timeline_words(tl, transcripts)
    vertical = tl.format == "9:16"
    best = strongest(tl)

    # 1. Accroche : de la parole tout de suite, et un début fort (ou un texte d'accroche) avant 3 s.
    if words and words[0].start > SPEECH_START_MAX:
        issues.append(_issue("accroche", "alerte",
                             f"La première parole arrive à {words[0].start:.1f} s : le début paraît vide.",
                             0.0, "accroche_debut", tl.clips[0].id))
    hook_text = any(x.start < HOOK_LIMIT and x.style in ("titre", "gros_texte", "texte_3d") for x in tl.texts)
    scores = [c.score for c in tl.clips]
    if len(tl.clips) > 2 and tl.clips[0].score < 0.6 * max(scores) and not hook_text:
        fix = "accroche_ordre" if settings.style in ("short", "pvp") else "accroche_texte"
        issues.append(_issue("accroche", "alerte" if settings.style not in ("short", "pvp") else "erreur",
                             "Les 3 premières secondes ne sont pas le moment le plus accrocheur.", 0.0, fix))

    # 2. Moment fort préparé : une respiration (pause ou coupe) juste avant, et un appui (zoom, bruitage).
    if best and len(tl.clips) > 1:
        i, t0 = best
        c = tl.clips[i]
        before = [w for w in words if t0 - 1.0 <= w.start < t0]
        pause = (not before) or (t0 - max(w.end for w in before) >= 0.25)
        accent = (c.zoom > 1.04 or c.keyframes or c.transition != "aucune" or c.effects
                  or any(abs(s.start - t0) < 0.3 for s in tl.sfx) or any(abs(x.start - t0) < 0.3 for x in tl.impacts))
        if not (pause and accent):
            issues.append(_issue("moment_fort", "alerte",
                                 "Le moment le plus fort n'est pas préparé : il faut une courte pause juste avant "
                                 "et un appui (zoom ou bruitage) dessus.", t0, "moment_fort", c.id))

    # 3. Plans longs sans mouvement.
    limit = LONG_SHOT.get(settings.style, 12.0)
    for c, t0 in zip(tl.clips, starts):
        static = c.zoom <= 1.01 and not c.keyframes and not any(e in c.effects for e in ("zoom_lent", "pulsation", "3d_flottant"))
        covered = any(o.start < t0 + c.duration and o.start + o.duration > t0 for o in tl.overlays)
        if c.duration > limit and static and not covered:
            issues.append(_issue("plan_long", "alerte",
                                 f"Plan fixe de {c.duration:.0f} s sans mouvement : le regard décroche.",
                                 t0, "zoom_lent", c.id))

    # 4. Textes lisibles sur un téléphone, et assez longtemps.
    for x in tl.texts:
        mx = TEXT_MAX_CHARS.get(x.style, (30, 50))[0 if vertical else 1]
        if len(x.text) > mx:
            issues.append(_issue("texte", "alerte",
                                 f"Texte « {x.text[:40]} » trop long pour rester lisible sur un téléphone "
                                 f"({len(x.text)} caractères, {mx} au plus).", x.start, "texte_court", x.id))
        need = 0.6 + 0.28 * len(x.text.split())
        if x.style != "emoji" and x.duration < need:
            issues.append(_issue("lecture", "alerte",
                                 f"Texte « {x.text[:40]} » affiché {x.duration:.1f} s : il faut {need:.1f} s pour le lire.",
                                 x.start, "texte_duree", x.id))
        if x.start >= total:
            issues.append(_issue("texte", "alerte", f"Texte « {x.text[:40]} » placé après la fin.", x.start,
                                 "texte_retirer", x.id))

    # 5. Flashs prévus dans le plan (transitions flash, impacts flash, glitch) : 3 par seconde au plus.
    flashes = sorted([t0 for c, t0 in zip(tl.clips, starts) if c.transition == "flash"]
                     + [i.start for i in tl.impacts if i.kind in ("flash", "flash_rouge")])
    for k in range(len(flashes)):
        n = sum(1 for f in flashes[k:] if f - flashes[k] < 1.0)
        if n > FLASH_MAX_PER_S:
            issues.append(_issue("flashs", "erreur", f"{n} flashs en moins d'une seconde : risque pour les "
                                 "personnes photosensibles (3 par seconde au plus).", flashes[k], "flashs"))
            break

    # 6. Durée cible (±10 %).
    if settings.target_duration and abs(total - settings.target_duration) > 0.1 * settings.target_duration:
        issues.append(_issue("duree", "alerte", f"Durée {total:.0f} s pour une cible de "
                             f"{settings.target_duration:.0f} s."))

    # 7. Bruitages calés sur une coupe, un texte, un impact ou une parole.
    anchors = sorted(set([round(t, 3) for t in starts[1:]] + [x.start for x in tl.texts] + [i.start for i in tl.impacts]
                         + [w.start for w in words]))
    for s in tl.sfx:
        if s.start >= total:
            issues.append(_issue("bruitage", "alerte", f"Bruitage {s.kind} après la fin.", s.start, "bruitage_retirer", s.id))
            continue
        near = min((abs(a - s.start) for a in anchors), default=9.0)
        if 0.12 < near:
            issues.append(_issue("bruitage", "alerte", f"Bruitage {s.kind} à {s.start:.1f} s ne tombe sur rien "
                                 f"(décalé de {near:.2f} s).", s.start, "bruitage_caler" if near < 0.8 else None, s.id))

    return QualityReport(score=score_of(issues), issues=issues)


def score_of(issues: list[QualityIssue]) -> int:
    pen = sum(18 if i.level == "erreur" else 6 for i in issues if i.level != "ok")
    return int(max(0, 100 - pen))


# --- corrections automatiques ---------------------------------------------------------------

def apply_fixes(tl: Timeline, report: QualityReport, transcripts: dict[str, Transcript],
                settings: EditSettings | None = None) -> tuple[Timeline, list[str]]:
    """Applique les corrections automatiques du rapport. Renvoie la timeline corrigée et ce qui a été fait."""
    settings = settings or EditSettings()
    tl = tl.model_copy(deep=True)
    done: list[str] = []
    by_id = {c.id: c for c in tl.clips}
    for it in report.issues:
        f = it.fix
        if not f:
            continue
        if f == "accroche_debut" and tl.clips:
            c = tl.clips[0]
            tr = transcripts.get(c.media_id)
            first = next((w.start for w in (tr.words if tr else []) if c.src_in <= w.start < c.src_out), None)
            if first is not None and first - c.src_in > SPEECH_START_MAX and c.src_out - first > 1.0:
                before = c.duration
                cut = max(0.0, first - 0.2 - c.src_in)
                c.src_in = round(c.src_in + cut, 3)
                _shift_after(tl, 0.0, c.duration - before)
                done.append(f"début resserré de {cut:.1f} s pour parler tout de suite")
        elif f == "accroche_ordre" and len(tl.clips) > 2:
            i = max(range(len(tl.clips)), key=lambda k: tl.clips[k].score)
            if i:
                c = tl.clips.pop(i)
                c.transition = "aucune"
                tl.clips.insert(0, c)
                done.append("moment le plus fort placé en ouverture")
        elif f == "accroche_texte":
            if not any(x.start < HOOK_LIMIT and x.style in ("titre", "gros_texte") for x in tl.texts):
                txt = _hook_line(tl)
                if txt:
                    tl.texts.append(TextItem(start=0.2, duration=2.4, text=txt, style="gros_texte", position="centre"))
                    done.append(f"accroche écrite à l'écran : « {txt} »")
        elif f == "moment_fort" and it.target in by_id:
            c = by_id[it.target]
            if c.zoom < 1.08 and not c.keyframes:
                c.zoom = 1.12
            t0 = it.t or 0.0
            if not any(abs(s.start - t0) < 0.3 for s in tl.sfx):
                tl.sfx.append(SfxItem(start=round(max(0.0, t0), 3), kind="boom" if settings.style in ("short", "pvp") else "whoosh",
                                      volume=0.6))
            done.append(f"moment fort appuyé à {t0:.1f} s (zoom et bruitage)")
        elif f == "zoom_lent" and it.target in by_id:
            c = by_id[it.target]
            if "zoom_lent" not in c.effects:
                c.effects.append("zoom_lent")
                done.append(f"zoom lent ajouté sur le plan fixe à {it.t or 0:.0f} s")
        elif f in ("texte_court", "texte_duree", "texte_retirer"):
            x = next((x for x in tl.texts if x.id == it.target), None)
            if not x:
                continue
            if f == "texte_retirer":
                tl.texts.remove(x)
                done.append("texte hors montage retiré")
            elif f == "texte_court":
                mx = TEXT_MAX_CHARS.get(x.style, (30, 50))[0 if tl.format == "9:16" else 1]
                x.text = _shorten(x.text, mx)
                done.append(f"texte raccourci : « {x.text} »")
            else:
                x.duration = round(0.6 + 0.28 * len(x.text.split()) + 0.2, 2)
                done.append(f"texte « {x.text[:24]} » affiché plus longtemps")
        elif f == "flashs":
            kept, last = [], -9.0
            for i in sorted(tl.impacts, key=lambda i: i.start):
                if i.kind in ("flash", "flash_rouge") and i.start - last < 0.4:
                    continue
                if i.kind in ("flash", "flash_rouge"):
                    last = i.start
                kept.append(i)
            removed = len(tl.impacts) - len(kept)
            tl.impacts = kept
            done.append(f"{removed} flash(s) retiré(s) pour rester sous 3 par seconde")
        elif f in ("bruitage_caler", "bruitage_retirer"):
            s = next((s for s in tl.sfx if s.id == it.target), None)
            if not s:
                continue
            if f == "bruitage_retirer":
                tl.sfx.remove(s)
                done.append("bruitage hors montage retiré")
            else:
                cuts = _starts(tl)[1:] + [x.start for x in tl.texts] + [i.start for i in tl.impacts]
                if cuts:
                    s.start = round(min(cuts, key=lambda a: abs(a - s.start)), 3)
                    done.append(f"bruitage {s.kind} calé sur {s.start:.1f} s")
    return tl, done


def _shift_after(tl: Timeline, t: float, dt: float) -> None:
    """Décale les éléments posés sur la timeline après t (quand un plan raccourcit)."""
    for coll in (tl.texts, tl.sfx, tl.overlays, tl.impacts, tl.images, tl.audio):
        for x in coll:
            if x.start > t:
                x.start = round(max(0.0, x.start + dt), 3)


def _shorten(text: str, mx: int) -> str:
    words, out = text.split(), ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > mx - 1:
            break
        out = f"{out} {w}".strip()
    return (out or text[:mx - 1]).rstrip(" ,;:") + ("…" if out != text else "")


def _hook_line(tl: Timeline) -> str:
    base = (tl.title or "").strip()
    if not base:
        return ""
    return _shorten(base.upper(), TEXT_MAX_CHARS["gros_texte"][0 if tl.format == "9:16" else 1])


# --- contrôle de la vidéo finie ------------------------------------------------------------------

def measure_loudness(path: str | Path) -> dict:
    """Loudness intégrée (LUFS) et crête vraie (dBTP), mesurées par le filtre ebur128 de FFmpeg."""
    p = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-hide_banner", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    tail = p.stderr[p.stderr.rfind("Summary:"):] if "Summary:" in p.stderr else p.stderr
    m_i = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", tail)
    m_p = re.search(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS", tail)
    val = lambda m: None if not m or m.group(1) == "-inf" else float(m.group(1))  # noqa: E731
    return {"lufs": val(m_i), "true_peak": val(m_p)}


def measure_flashes(path: str | Path, fps: float = 30.0) -> dict:
    """Flashs : une forte variation de luminosité suivie de son inverse. Renvoie le pire nombre par seconde."""
    proc = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(path), "-an",
                           "-vf", f"fps={fps},scale=64:36:flags=area,format=gray", "-f", "rawvideo", "-"],
                          capture_output=True)
    fr = np.frombuffer(proc.stdout, np.uint8)
    n = fr.size // (64 * 36)
    if n < 3:
        return {"flashes_max_per_s": 0, "flash_at": None, "start_luma": None}
    luma = fr[: n * 64 * 36].reshape(n, -1).mean(axis=1) / 255.0
    d = np.diff(luma)
    big = [(k, np.sign(d[k])) for k in range(d.size) if abs(d[k]) >= FLASH_DELTA]
    events = []  # un flash = deux variations fortes de sens opposé à moins de 0,5 s
    for (k1, s1), (k2, s2) in zip(big, big[1:]):
        if s1 != s2 and (k2 - k1) / fps <= 0.5:
            events.append(k1 / fps)
    worst, at = 0, None
    for k, t in enumerate(events):
        cnt = sum(1 for u in events[k:] if u - t < 1.0)
        if cnt > worst:
            worst, at = cnt, t
    return {"flashes_max_per_s": worst, "flash_at": at, "start_luma": float(luma[: int(fps / 2)].mean())}


def measure_start_audio(path: str | Path) -> float | None:
    """Volume moyen (dB) de la première seconde."""
    p = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-hide_banner", "-t", "1", "-i", str(path), "-vn",
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr)
    return float(m.group(1)) if m else None


def render_checks(path: str | Path, tl: Timeline, fps: float = 30.0) -> tuple[list[QualityIssue], dict]:
    issues: list[QualityIssue] = []
    measured: dict = {}
    measured.update(measure_loudness(path))
    measured.update(measure_flashes(path, fps))
    measured["start_volume_db"] = measure_start_audio(path)
    lufs, peak = measured.get("lufs"), measured.get("true_peak")
    if lufs is not None and abs(lufs - LUFS_TARGET) > LUFS_TOL:
        issues.append(_issue("son", "alerte", f"Volume moyen {lufs:.1f} LUFS : les réseaux attendent environ "
                             f"{LUFS_TARGET:.0f} LUFS."))
    if peak is not None and peak > PEAK_MAX:
        issues.append(_issue("son", "alerte", f"Crête à {peak:.1f} dB : risque de saturation."))
    if measured["flashes_max_per_s"] > FLASH_MAX_PER_S:
        issues.append(_issue("flashs", "erreur", f"{measured['flashes_max_per_s']} flashs en une seconde dans la "
                             "vidéo finie (3 au plus).", measured["flash_at"], "flashs"))
    if measured.get("start_luma") is not None and measured["start_luma"] < 0.04:
        issues.append(_issue("image", "alerte", "La vidéo commence sur une image noire.", 0.0))
    sv = measured.get("start_volume_db")
    if sv is not None and sv < -45 and tl.captions != "aucun":
        issues.append(_issue("accroche", "alerte", "La première seconde est silencieuse.", 0.0))
    return issues, measured


def report_text(report: QualityReport) -> str:
    """Rapport lisible (pour le relecteur et le réalisateur IA)."""
    if not report.issues:
        return f"Contrôle qualité : {report.score}/100, aucun problème."
    lines = [f"Contrôle qualité : {report.score}/100."]
    for i in report.issues:
        at = f" à {i.t:.1f} s" if i.t is not None else ""
        lines.append(f"- [{i.level}] {i.check}{at} : {i.message}")
    if report.measured:
        lines.append("Mesures : " + json.dumps({k: v for k, v in report.measured.items() if v is not None}, ensure_ascii=False))
    return "\n".join(lines)


def check_and_fix(tl: Timeline, transcripts: dict[str, Transcript], settings: EditSettings | None = None,
                  rounds: int = 2) -> Timeline:
    """Contrôle du plan, corrections automatiques, puis nouveau contrôle ; le rapport final est rangé dans tl."""
    fixed: list[str] = []
    for _ in range(rounds):
        rep = plan_checks(tl, transcripts, settings)
        if not any(i.fix for i in rep.issues):
            break
        tl, done = apply_fixes(tl, rep, transcripts, settings)
        if not done:
            break
        fixed += done
    rep = plan_checks(tl, transcripts, settings)
    rep.fixed = fixed
    tl.quality = rep
    return tl


