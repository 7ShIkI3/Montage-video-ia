"""Analyse audio : enveloppe d'énergie, silences, pics d'intensité."""
from __future__ import annotations

import numpy as np

from .. import config


def envelope_db(audio: np.ndarray, sr: int = config.AUDIO_SR, hop: float = config.ENVELOPE_HOP) -> np.ndarray:
    n = int(sr * hop)
    if audio.size < n:
        return np.full(1, -90.0, dtype=np.float32)
    frames = audio[: audio.size // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    return (20 * np.log10(rms + 1e-9)).astype(np.float32)


def noise_floor(env: np.ndarray) -> float:
    return float(np.percentile(env, 10)) if env.size else -90.0


def silence_threshold(env: np.ndarray) -> float:
    floor = noise_floor(env)
    loud = float(np.percentile(env, 90)) if env.size else -20.0
    # Seuil adaptatif : entre le bruit de fond et la voix, jamais au-dessus de -30 dB.
    return min(floor + max(6.0, (loud - floor) * 0.35), -30.0)


def find_silences(env: np.ndarray, hop: float = config.ENVELOPE_HOP, min_len: float = 0.45) -> list[tuple[float, float]]:
    thr = silence_threshold(env)
    quiet = env < thr
    out: list[tuple[float, float]] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * hop >= min_len:
                out.append((start * hop, i * hop))
            start = None
    if start is not None and (len(quiet) - start) * hop >= min_len:
        out.append((start * hop, len(quiet) * hop))
    return out


def active_regions(silences: list[tuple[float, float]], duration: float, min_len: float = 0.3) -> list[tuple[float, float]]:
    """Complément des silences : les zones où il se passe quelque chose."""
    regions = []
    t = 0.0
    for s, e in silences:
        if s - t >= min_len:
            regions.append((t, s))
        t = e
    if duration - t >= min_len:
        regions.append((t, duration))
    return regions


def find_peaks(env: np.ndarray, hop: float = config.ENVELOPE_HOP) -> list[float]:
    """Moments d'intensité exceptionnelle (rires, cris, applaudissements)."""
    if env.size < 10:
        return []
    thr = max(float(np.percentile(env, 97)), float(np.median(env)) + 12.0)
    idx = np.where(env >= thr)[0]
    peaks: list[float] = []
    last = -1e9
    for i in idx:
        t = i * hop
        if t - last > 2.0:
            peaks.append(round(t, 2))
            last = t
    return peaks


def analyze(audio: np.ndarray, duration: float) -> dict:
    env = envelope_db(audio)
    silences = find_silences(env)
    return {
        "hop": config.ENVELOPE_HOP,
        "envelope": [round(float(x), 1) for x in env],
        "silence_threshold": round(silence_threshold(env), 1),
        "silences": [[round(s, 3), round(e, 3)] for s, e in silences],
        "active": [[round(s, 3), round(e, 3)] for s, e in active_regions(silences, duration)],
        "peaks": find_peaks(env),
    }
