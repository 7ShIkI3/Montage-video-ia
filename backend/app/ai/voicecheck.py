"""Voix IA calée au mot près, et réécoutée avant d'être gardée.

Après chaque voix de synthèse (voix off IA ou doublage) :
- on la réécoute avec Whisper : ce qu'on entend doit ressembler au texte voulu (taux d'erreur sur les mots).
  Une voix mal comprise est signalée au contrôle qualité ;
- on obtient l'instant de chaque mot, pour que les sous-titres animés et les effets suivent la voix IA.
Sans Whisper, l'instant des mots est estimé sur l'énergie du son (les mots se partagent les passages parlés,
au prorata de leur longueur) et la compréhension n'est pas vérifiée.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import numpy as np

from .. import config
from ..media import ffmpeg
from ..models import Word

log = logging.getLogger(__name__)

MAX_WER = 0.35  # au-delà, la voix est jugée mal comprise


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^\w' ]+", " ", t).replace("'", " ")


def tokens(text: str) -> list[str]:
    return _norm(text).split()


def wer(ref: str, hyp: str) -> float:
    """Taux d'erreur sur les mots (distance d'édition / nombre de mots voulus)."""
    r, h = tokens(ref), tokens(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def voiced_runs(pcm: np.ndarray, sr: int, hop_s: float = 0.02, min_gap: float = 0.12) -> list[tuple[float, float]]:
    """Passages parlés d'un son : (début, fin) en secondes."""
    hop = max(1, int(hop_s * sr))
    n = pcm.size // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(pcm[: n * hop].astype(np.float32).reshape(n, hop) ** 2, axis=1) + 1e-12)
    on = rms > max(1e-4, 0.12 * float(np.percentile(rms, 95)))
    runs, start, last = [], None, -1
    for k, v in enumerate(on):
        if v:
            if start is None:
                start = k
            elif (k - last) * hop_s >= min_gap:
                runs.append((start * hop_s, (last + 1) * hop_s))
                start = k
            last = k
    if start is not None:
        runs.append((start * hop_s, (last + 1) * hop_s))
    return [(round(a, 3), round(b, 3)) for a, b in runs if b - a >= 0.04]


def estimate_words(text: str, runs: list[tuple[float, float]]) -> list[Word]:
    """Instant de chaque mot, estimé : chaque mot va dans le passage parlé où tombe son milieu (au prorata des
    longueurs), puis les mots d'un même passage se le partagent. Un mot ne chevauche jamais un silence."""
    words = text.split()
    if not words or not runs:
        return []
    voiced = sum(b - a for a, b in runs)
    weights = [len(w) + 1 for w in words]
    total = float(sum(weights))
    bounds, acc = [], 0.0  # temps parlé cumulé à la fin de chaque passage
    for a, b in runs:
        acc += b - a
        bounds.append(acc)
    groups: list[list[int]] = [[] for _ in runs]
    acc = 0.0
    for i, wt in enumerate(weights):
        mid = (acc + wt / 2) / total * voiced
        k = next((j for j, e in enumerate(bounds) if mid <= e), len(runs) - 1)
        groups[k].append(i)
        acc += wt
    out: list[Word | None] = [None] * len(words)
    for (a, b), idx in zip(runs, groups):
        if not idx:
            continue
        tw = float(sum(weights[i] for i in idx))
        t = a
        for i in idx:
            d = (b - a) * weights[i] / tw
            out[i] = Word(start=round(t, 3), end=round(t + max(0.04, d * 0.92), 3), text=words[i])
            t += d
    return [w for w in out if w is not None]


def verify(path: str | Path, text: str, lang: str | None = None) -> tuple[list[Word], dict]:
    """Réécoute une voix de synthèse. Renvoie (mots horodatés, rapport {ok, wer, heard, method})."""
    pcm = ffmpeg.decode_audio(path, config.AUDIO_SR)
    try:
        from ..analysis import transcribe as tr_mod
        t = tr_mod.transcribe(pcm, "voix_ia", pcm.size / config.AUDIO_SR, language=lang)
        heard = " ".join(w.text for w in t.words)
        e = wer(text, heard)
        words = t.words
        # Les mots entendus servent d'horodatage s'ils collent au texte ; sinon on garde le texte voulu, estimé.
        if e > MAX_WER or not words:
            words = estimate_words(text, voiced_runs(pcm, config.AUDIO_SR))
        return words, {"ok": e <= MAX_WER, "wer": round(e, 2), "heard": heard[:300], "method": "whisper"}
    except Exception as ex:  # Whisper absent ou désactivé
        log.info("Réécoute Whisper impossible (%s) : horodatage estimé sur l'énergie", ex)
        runs = voiced_runs(pcm, config.AUDIO_SR)
        rep = {"ok": None, "wer": None, "heard": "", "method": "energie"}
        if not runs:
            rep["ok"] = False
            rep["heard"] = "(silence)"
        return estimate_words(text, runs), rep
