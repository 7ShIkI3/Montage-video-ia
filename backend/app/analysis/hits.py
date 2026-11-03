"""Détection des coups (attaques sonores brèves) et du tempo d'une musique.

- `detect_hits` : flux spectral (montée brutale d'énergie, surtout dans les aigus) -> instants
  des coups, chocs, tirs. C'est ce qui rythme un edit PvP.
- `beat_grid` : tempo par autocorrélation de l'enveloppe d'attaques, puis phase des temps.
"""
from __future__ import annotations

import numpy as np

HOP = 0.01  # 10 ms


def onset_envelope(pcm: np.ndarray, sr: int, hop: float = HOP) -> np.ndarray:
    n_fft = 1024 if sr >= 32000 else 512
    h = max(1, int(sr * hop))
    if pcm.size < n_fft + h:
        return np.zeros(1, np.float32)
    x = pcm.astype(np.float32)
    n = 1 + (x.size - n_fft) // h
    idx = np.arange(n_fft)[None, :] + h * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    # Racine de l'amplitude : sensible au niveau (un pas discret pèse moins qu'un coup), sans écraser les détails.
    cm = np.sqrt(mag)
    w = np.linspace(0.6, 1.6, cm.shape[1], dtype=np.float32)  # les chocs ont beaucoup d'aigus
    flux = np.maximum(0.0, np.diff(cm, axis=0)) @ w
    flux = np.concatenate([[0.0], flux]).astype(np.float32)
    return flux / (np.percentile(flux, 99.5) + 1e-9)


def detect_hits(pcm: np.ndarray, sr: int, min_gap: float = 0.12) -> list[list[float]]:
    """Liste de [instant, force 0..1] des attaques nettement au-dessus du niveau local."""
    env = onset_envelope(pcm, sr)
    if env.size < 20:
        return []
    win = int(0.6 / HOP)
    pad = np.pad(env, (win, win), mode="edge")
    # médiane glissante (sur une version sous-échantillonnée pour aller vite)
    step = 5
    med = np.array([np.median(pad[i:i + 2 * win]) for i in range(0, env.size, step)])
    med = np.repeat(med, step)[: env.size]
    glob = float(np.percentile(env, 90))
    thr = np.maximum(med * 2.2 + 0.08, glob * 0.9)
    out: list[list[float]] = []
    gap = int(min_gap / HOP)
    i = 1
    while i < env.size - 1:
        if env[i] > thr[i] and env[i] >= env[i - 1] and env[i] >= env[i + 1]:
            j = min(env.size, i + gap)
            k = i + int(np.argmax(env[i:j]))
            a = int(k * HOP * sr)
            level = float(np.sqrt(np.mean(pcm[a:a + int(0.08 * sr)].astype(np.float32) ** 2) + 1e-12))
            out.append([round(k * HOP, 3), float(env[k]), level])
            i = k + gap
            continue
        i += 1
    if out:
        # Force = attaque x volume : un pas discret a une attaque nette mais un faible volume.
        ref = float(np.percentile([lv for *_, lv in out], 95)) + 1e-9
        raw = [(t, s * min(1.5, lv / ref) ** 0.8) for t, s, lv in out]
        top = float(np.percentile([s for _, s in raw], 95)) + 1e-9
        out = [[t, round(min(1.0, s / top), 3)] for t, s in raw]
        out = [h for h in out if h[1] >= 0.3]  # écarte les bruits de pas et les petits sons
    return out


def combos(hits: list[list[float]], max_gap: float = 1.6, min_hits: int = 3) -> list[dict]:
    """Regroupe les coups rapprochés en séquences de combat (combos)."""
    out: list[dict] = []
    cur: list[list[float]] = []
    for h in hits:
        if cur and h[0] - cur[-1][0] > max_gap:
            if len(cur) >= min_hits:
                out.append(_combo(cur))
            cur = []
        cur.append(h)
    if len(cur) >= min_hits:
        out.append(_combo(cur))
    return out


def _combo(c: list[list[float]]) -> dict:
    s = [x[1] for x in c]
    dur = c[-1][0] - c[0][0]
    return {"start": c[0][0], "end": c[-1][0], "hits": c, "n": len(c),
            "score": round(len(c) * float(np.mean(s)) * (1 + 0.3 * len(c) / max(1.0, dur)), 3)}


def beat_grid(pcm: np.ndarray, sr: int, lo: float = 75, hi: float = 180) -> tuple[float, float]:
    """(bpm, premier temps en secondes) d'une musique."""
    env = onset_envelope(pcm, sr)
    if env.size < 400:
        return 120.0, 0.0
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[e.size - 1:]
    lags = np.arange(int(60 / hi / HOP), int(60 / lo / HOP) + 1)
    lag = int(lags[np.argmax(ac[lags])])
    period = lag * HOP
    phases = [env[p::lag].sum() for p in range(lag)]
    first = int(np.argmax(phases)) * HOP
    return round(60 / period, 2), round(first, 3)
