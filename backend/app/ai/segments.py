"""Segmentation des rushes en moments candidats, puis notation des moments forts."""
from __future__ import annotations

import difflib
import re

import numpy as np

from ..models import Media, Segment, Transcript, Word

SENTENCE_END = re.compile(r"[.!?…]$")
HOOK_WORDS = {
    # français
    "incroyable", "secret", "attention", "jamais", "toujours", "meilleur", "pire", "énorme", "fou",
    "folle", "dingue", "astuce", "erreur", "important", "pourquoi", "comment", "vraiment", "personne",
    "gratuit", "argent", "choc", "regardez", "imaginez", "voici", "premier", "dernier", "top",
    # anglais
    "amazing", "secret", "never", "always", "best", "worst", "crazy", "insane", "mistake", "why", "how",
}

PAD_BEFORE = 0.08
PAD_AFTER = 0.15
MAX_SPEECH_SEG = 14.0
MAX_ACTION_SEG = 5.0


def _clean(t: str) -> str:
    return re.sub(r"[^\wà-ÿ' ]", "", t.lower()).strip()


def speech_segments(media: Media, transcript: Transcript, split_fillers: bool = True) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in transcript.words:
        if w.filler and split_fillers:
            if cur:
                groups.append(cur)
            cur = []
            continue
        if cur:
            gap = w.start - cur[-1].end
            seg_len = cur[-1].end - cur[0].start
            ends_sentence = bool(SENTENCE_END.search(cur[-1].text))
            if gap > 0.7 or seg_len > MAX_SPEECH_SEG or (ends_sentence and seg_len > 2.0 and gap > 0.15):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _window(arr: list[float], rate: float, start: float, end: float) -> np.ndarray:
    if not arr:
        return np.zeros(1)
    a = int(start * rate)
    b = max(a + 1, int(end * rate))
    sl = np.asarray(arr[a:b], dtype=np.float32)
    return sl if sl.size else np.zeros(1)


def build_segments(media: Media, transcript: Transcript | None, signals: dict,
                   split_fillers: bool = True, include_action: bool = True) -> list[Segment]:
    audio = signals.get("audio") or {}
    video = signals.get("video") or {}
    segs: list[Segment] = []
    idx = 0

    def add(start, end, kind, text="", words=None):
        nonlocal idx
        start = max(0.0, start)
        end = min(media.duration or end, end)
        if end - start < 0.35:
            return
        segs.append(Segment(id=f"{media.id}:{idx}", media_id=media.id, start=round(start, 3),
                            end=round(end, 3), text=text, kind=kind,
                            features={"_words": float(len(words or []))}))
        idx += 1

    speech_spans: list[tuple[float, float]] = []
    if transcript and transcript.words:
        groups = speech_segments(media, transcript, split_fillers)
        prev_end = 0.0
        for g in groups:
            s = max(prev_end, g[0].start - PAD_BEFORE)
            e = g[-1].end + PAD_AFTER
            add(s, e, "speech", " ".join(w.text for w in g), g)
            speech_spans.append((s, e))
            prev_end = e
    elif media.has_audio:
        # Pas de transcription : on s'appuie sur les zones actives de l'audio.
        for s, e in audio.get("active", []):
            t = s
            while e - t > 0.35:
                chunk_end = min(e, t + MAX_SPEECH_SEG)
                add(t - PAD_BEFORE, chunk_end + PAD_AFTER, "speech")
                speech_spans.append((t, chunk_end))
                t = chunk_end

    if include_action and media.has_video:
        # Passages sans parole mais visuellement intéressants (vlog, B-roll, action).
        cuts = [0.0, *video.get("cuts", []), media.duration]
        spans = sorted(speech_spans)
        for a, b in zip(cuts, cuts[1:]):
            t = a
            while b - t > 1.0:
                e = min(b, t + MAX_ACTION_SEG)
                overlaps = any(not (e <= s0 or t >= e0) for s0, e0 in spans)
                if not overlaps:
                    add(t, e, "action")
                t = e

    segs.sort(key=lambda s: s.start)
    score_segments(segs, signals)
    mark_retakes(segs)
    return segs


def score_segments(segs: list[Segment], signals: dict) -> None:
    audio = signals.get("audio") or {}
    video = signals.get("video") or {}
    env = audio.get("envelope") or []
    hop = audio.get("hop", 0.05)
    env_arr = np.asarray(env, dtype=np.float32) if env else np.zeros(1)
    floor = float(np.percentile(env_arr, 10))
    loud = float(np.percentile(env_arr, 95))
    span = max(6.0, loud - floor)
    peaks = audio.get("peaks", [])
    motion = video.get("motion", [])
    vfps = video.get("fps", 5)
    cuts = video.get("cuts", [])

    for s in segs:
        e = _window(env, 1 / hop, s.start, s.end)
        energy = float(np.clip((np.mean(e) - floor) / span, 0, 1))
        dyn = float(np.clip((np.percentile(e, 90) - np.percentile(e, 30)) / 20, 0, 1))
        peak = 1.0 if any(s.start <= p <= s.end for p in peaks) else 0.0
        mot = float(np.clip(np.mean(_window(motion, vfps, s.start, s.end)) * 1.5, 0, 1)) if motion else 0.0
        n_words = s.features.pop("_words", 0.0)
        rate = n_words / max(0.5, s.duration)
        rate_n = float(np.clip(rate / 3.5, 0, 1))
        words = set(_clean(s.text).split())
        hook = 0.0
        if words & HOOK_WORDS:
            hook += 0.6
        if "?" in s.text or "!" in s.text:
            hook += 0.4
        hook = min(1.0, hook)
        cut_density = float(np.clip(sum(1 for c in cuts if s.start <= c <= s.end) / max(1.0, s.duration) * 2, 0, 1))
        length_fit = float(np.clip(s.duration / 2.0, 0, 1))
        if s.kind == "speech":
            score = 0.28 * energy + 0.12 * dyn + 0.12 * peak + 0.08 * mot + 0.2 * rate_n + 0.2 * hook
        else:
            score = 0.25 * energy + 0.1 * peak + 0.45 * mot + 0.2 * cut_density
            score *= 0.85  # la parole prime par défaut
        score *= 0.5 + 0.5 * length_fit
        s.features.update({
            "energy": round(energy, 3), "dynamics": round(dyn, 3), "peak": peak, "motion": round(mot, 3),
            "speech_rate": round(rate, 2), "hook": round(hook, 2), "cuts": round(cut_density, 3),
        })
        s.score = round(float(np.clip(score, 0, 1)), 3)


def mark_retakes(segs: list[Segment], window: float = 45.0) -> None:
    """Prises répétées : si une phrase est redite peu après, on garde la dernière."""
    speech = [s for s in segs if s.kind == "speech" and len(s.text.split()) >= 4]
    for i, a in enumerate(speech):
        ta = _clean(a.text)
        for b in speech[i + 1:]:
            if b.media_id != a.media_id or b.start - a.end > window:
                break
            tb = _clean(b.text)
            # Une prise ratée est souvent le début de la bonne prise.
            ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
            prefix = tb.startswith(ta[: max(12, len(ta) // 2)])
            if ratio > 0.72 or (prefix and len(ta) <= len(tb)):
                a.duplicate_of = b.id
                break
