"""Monter « comme cette vidéo de référence ».

On analyse une vidéo que l'utilisateur aime : quand chaque plan change (détection de scènes FFmpeg), la durée
des plans, l'accélération du rythme au fil de la vidéo, les fondus au noir et le mouvement. On en tire un profil
de rythme. Après le montage, l'IA compare son rythme à celui de la référence et corrige ce qui est le plus
éloigné, en 2 à 5 tours, puis dit ce qu'elle n'a pas pu égaler.

Par défaut, seul le mouvement (le rythme) est copié, pas le look : les couleurs restent celles de l'utilisateur.
"""
from __future__ import annotations

import re
import statistics
import subprocess
from pathlib import Path

import numpy as np

from ..media import ffmpeg
from ..models import Clip, Timeline, Transcript, new_id

SCENE_THRESHOLD = 0.15  # score de scène minimal (FFmpeg) pour compter une coupe
MIN_ROUNDS, MAX_ROUNDS = 2, 5
TOLERANCE = 0.15  # 15 % d'écart sur la durée moyenne d'un plan


def analyze(path: str | Path) -> dict:
    """Profil de rythme d'une vidéo de référence."""
    info = ffmpeg.probe(path)
    dur = float(info.get("duration") or 0.0)
    p = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-hide_banner", "-i", str(path), "-an",
                        "-vf", "scale=320:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
                        "-f", "null", "-"], capture_output=True, text=True)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr)]
    scores = [float(x) for x in re.findall(r"lavfi\.scene_score=([\d.]+)", p.stderr)]
    n = min(len(times), len(scores))
    sc = np.array(scores[:n])
    # Seuil adaptatif : une coupe ressort nettement du mouvement ordinaire de la vidéo.
    thr = max(SCENE_THRESHOLD, 6 * float(np.median(sc))) if n else 1.0
    cuts = [times[k] for k in range(n) if sc[k] >= thr
            and sc[k] >= sc[max(0, k - 2):k + 3].max()]
    cuts = [t for t in cuts if 0.2 < t < dur - 0.2]
    b = subprocess.run([ffmpeg.FFMPEG, "-nostdin", "-hide_banner", "-i", str(path), "-an",
                        "-vf", "scale=160:-2,blackdetect=d=0.08:pic_th=0.92", "-f", "null", "-"],
                       capture_output=True, text=True)
    blacks = [(float(a), float(e)) for a, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", b.stderr)]
    fades = [a for a, e in blacks if 0.1 < a < dur - 0.3]
    bounds = [0.0] + cuts + [dur]
    shots = [round(b2 - b1, 3) for b1, b2 in zip(bounds, bounds[1:]) if b2 - b1 > 0.05]
    thirds = []
    for k in range(3):
        lo, hi = dur * k / 3, dur * (k + 1) / 3
        n = sum(1 for t in cuts if lo <= t < hi)
        thirds.append(round((hi - lo) / (n + 1), 2))
    motion = None
    try:
        g = ffmpeg.decode_gray_frames(path, fps=5, w=64, h=36)
        if len(g) > 2:
            motion = float(np.mean(np.abs(np.diff(g, axis=0))) / 255.0)
    except Exception:
        pass
    return {
        "duration": round(dur, 2), "cuts": [round(t, 3) for t in cuts], "shots": len(shots),
        "mean_shot": round(statistics.mean(shots), 2) if shots else round(dur, 2),
        "median_shot": round(statistics.median(shots), 2) if shots else round(dur, 2),
        "shortest": round(min(shots), 2) if shots else None, "longest": round(max(shots), 2) if shots else None,
        "pace_thirds": thirds,  # durée moyenne d'un plan au début, au milieu, à la fin
        "fades": len(fades), "motion": motion,
    }


def describe(ref: dict) -> str:
    th = ref.get("pace_thirds") or []
    trend = ""
    if len(th) == 3 and th[0] > 0 and th[2] > 0:
        trend = (" Le rythme accélère vers la fin." if th[2] < 0.75 * th[0] else
                 " Le rythme ralentit vers la fin." if th[2] > 1.33 * th[0] else "")
    return (f"Vidéo de référence : {ref['duration']:.0f} s, {ref['shots']} plans, un plan dure en moyenne "
            f"{ref['mean_shot']:.1f} s (médiane {ref['median_shot']:.1f} s), {ref['fades']} fondu(s) au noir.{trend}")


def visual_shots(tl: Timeline) -> list[float]:
    """Durée des plans vus à l'écran : coupes entre clips et entrées/sorties des plans de coupe plein cadre."""
    bounds = {0.0, round(tl.duration, 3)}
    t = 0.0
    for c in tl.clips:
        bounds.add(round(t, 3))
        t += c.duration
    for o in tl.overlays:
        if o.mode == "plein":
            bounds.add(round(o.start, 3))
            bounds.add(round(min(tl.duration, o.start + o.duration), 3))
    b = sorted(x for x in bounds if x <= tl.duration + 1e-6)
    return [y - x for x, y in zip(b, b[1:]) if y - x > 0.05]


def _split_point(c: Clip, transcripts: dict[str, Transcript]) -> float:
    """Instant source où couper un plan en deux : un blanc entre deux mots près du milieu."""
    mid = (c.src_in + c.src_out) / 2
    tr = transcripts.get(c.media_id)
    best = mid
    if tr:
        gaps = [(a.end + b.start) / 2 for a, b in zip(tr.words, tr.words[1:])
                if c.src_in + 0.6 < a.end and b.start < c.src_out - 0.6 and b.start - a.end >= 0.05]
        if gaps:
            best = min(gaps, key=lambda g: abs(g - mid))
    return round(best, 3)


def _split(tl: Timeline, i: int, transcripts: dict[str, Transcript]) -> bool:
    c = tl.clips[i]
    if c.duration < 1.6 or c.ramp or c.keyframes:
        return False
    s = _split_point(c, transcripts)
    if not (c.src_in + 0.5 < s < c.src_out - 0.5):
        return False
    b = c.model_copy(deep=True)
    b.id = new_id("c_")
    c.src_out, b.src_in = s, s
    b.transition = "aucune"
    # Coupe dans la même prise : on change le cadre pour qu'elle se voie (zoom alterné).
    b.zoom = 1.0 if c.zoom > 1.05 else 1.12
    b.reason = (c.reason + " (coupé pour suivre le rythme de la référence)").strip()
    tl.clips.insert(i + 1, b)
    return True


def _merge(tl: Timeline, i: int) -> bool:
    """Recolle le plan i et le suivant s'ils se suivent dans le même rush."""
    if i + 1 >= len(tl.clips):
        return False
    a, b = tl.clips[i], tl.clips[i + 1]
    if a.media_id != b.media_id or abs(b.src_in - a.src_out) > 0.05 or a.ramp or b.ramp or a.speed != b.speed:
        return False
    a.src_out = b.src_out
    a.score = max(a.score, b.score)
    tl.clips.pop(i + 1)
    return True


def match(tl: Timeline, ref: dict, transcripts: dict[str, Transcript], copy_look: bool = False,
          ref_look: str | None = None) -> tuple[Timeline, dict]:
    """Rapproche le rythme du montage de celui de la référence, en 2 à 5 tours. Renvoie (timeline, rapport)."""
    tl = tl.model_copy(deep=True)
    target = max(0.4, float(ref.get("mean_shot") or 3.0))
    history = []
    for rnd in range(1, MAX_ROUNDS + 1):
        shots = visual_shots(tl)
        mean = sum(shots) / len(shots) if shots else tl.duration
        history.append(round(mean, 2))
        if abs(mean - target) <= TOLERANCE * target and rnd > MIN_ROUNDS - 1:
            break
        if mean > target:
            # Plans trop longs : on coupe les plus longs (dans un blanc entre deux mots).
            order = sorted(range(len(tl.clips)), key=lambda k: -tl.clips[k].duration)
            need = max(1, int(round(tl.duration / target)) - len(shots))
            done = 0
            for k in sorted(order[: need * 2], reverse=True):
                if done >= need:
                    break
                if tl.clips[k].duration > 1.6 * target and _split(tl, k, transcripts):
                    done += 1
            if not done:
                break
        else:
            # Plans trop courts : on recolle les coupes qui se suivent dans le même rush.
            need = max(1, len(shots) - int(round(tl.duration / target)))
            done = 0
            k = 0
            while k < len(tl.clips) - 1 and done < need:
                if tl.clips[k].duration + tl.clips[k + 1].duration < 2.2 * target and _merge(tl, k):
                    done += 1
                else:
                    k += 1
            if not done:
                break
    shots = visual_shots(tl)
    final = sum(shots) / len(shots) if shots else tl.duration
    # Fondus au noir : autant (en proportion) que la référence.
    want = int(round((ref.get("fades") or 0) * tl.duration / max(1.0, ref["duration"])))
    have = sum(1 for c in tl.clips if c.transition == "fondu_noir")
    for c in tl.clips[1:]:
        if have >= want:
            break
        if c.transition == "aucune":
            c.transition, have = "fondu_noir", have + 1
    if copy_look and ref_look:
        tl.look = ref_look
    unmatched = []
    if abs(final - target) > TOLERANCE * target:
        unmatched.append(
            f"plans de {final:.1f} s en moyenne contre {target:.1f} s dans la référence : "
            + ("les rushes n'ont pas assez de coupes possibles (phrases trop longues)" if final > target
               else "les moments gardés sont trop courts pour être allongés"))
    if want > have:
        unmatched.append(f"{want - have} fondu(s) au noir de moins que la référence")
    report = {"target_shot": round(target, 2), "result_shot": round(final, 2), "rounds": len(history),
              "history": history, "unmatched": unmatched, "summary": describe(ref)}
    return tl, report
