"""Habillage automatique : l'IA place transitions, effets, vitesse, bruitages, titres et B-roll.

Utilisé par le réalisateur heuristique, et en complément de Claude s'il n'a rien placé.
"""
from __future__ import annotations

import numpy as np

from ..models import Clip, EditSettings, OverlayItem, Segment, SfxItem, TextItem, Timeline

SPEECH_CLEAN = ["voix_claire"]


def _starts(clips: list[Clip]) -> list[float]:
    out, t = [], 0.0
    for c in clips:
        out.append(t)
        t += c.duration
    return out


def _first_words(text: str, n: int = 5) -> str:
    words = text.replace("\n", " ").split()
    s = " ".join(words[:n])
    return s.rstrip(",;:") + ("…" if len(words) > n else "")


def decorate(tl: Timeline, segments: list[Segment], settings: EditSettings, project_name: str = "") -> Timeline:
    if not settings.auto_effects or not tl.clips:
        return tl
    style = settings.style
    by_id = {s.id: s for s in segments}
    clips = tl.clips
    scores = [c.score for c in clips]
    top = float(np.quantile(scores, 0.85)) if scores else 1.0

    # 1. Étalonnage global selon le style
    tl.look = {"youtube": "vif", "short": "vif", "vlog": "cinema", "podcast": "aucun"}[style]

    # 2. Effets par plan : voix, transitions, vitesse, mouvement
    for i, c in enumerate(clips):
        seg = by_id.get(c.segment_id or "")
        is_speech = bool(seg and seg.kind == "speech")
        if is_speech:
            c.audio_effects = list(dict.fromkeys(c.audio_effects + SPEECH_CLEAN
                                                 + (["anti_bruit"] if style == "podcast" else [])))
        prev = clips[i - 1] if i else None
        changes_media = prev is not None and prev.media_id != c.media_id
        if style == "vlog":
            if changes_media:
                c.transition = "fondu_noir"
                c.transition_duration = 0.4
            if seg and seg.kind == "action":
                c.effects = list(dict.fromkeys(c.effects + ["zoom_lent"]))
                if seg.features.get("motion", 0) > 0.5 and c.source_duration <= 2.5:
                    c.speed = 0.5
                    c.reason += " Ralenti sur un passage d'action."
        elif style == "youtube":
            if changes_media:
                c.transition = "zoom_avant"
                c.transition_duration = 0.3
        elif style == "short":
            if i == 0:
                c.transition = "flash"
            elif changes_media:
                c.transition = "glitch"
                c.transition_duration = 0.25
            if seg and seg.kind == "action" and c.source_duration > 2.5:
                c.speed = 1.5
        if c.score >= top and style in ("youtube", "short") and i > 0 and c.transition == "aucune":
            c.transition = "flash"
            c.transition_duration = 0.15

    starts = _starts(clips)
    total = sum(c.duration for c in clips)

    # 3. Bruitages : whoosh sur les transitions, pop sur les zooms d'emphase, ding sur l'accroche
    sfx: list[SfxItem] = []
    for i, c in enumerate(clips):
        t = starts[i]
        if c.transition in ("zoom_avant", "glissement", "fondu_noir", "glitch") and i > 0:
            sfx.append(SfxItem(start=max(0.0, t - 0.25), kind="whoosh", volume=0.5))
        elif c.transition == "flash" and i > 0:
            sfx.append(SfxItem(start=max(0.0, t - 0.05), kind="swoosh_court", volume=0.45))
        if c.zoom >= 1.1 and style in ("youtube", "short"):
            sfx.append(SfxItem(start=t + 0.05, kind="pop", volume=0.5))
    if style == "short" and clips:
        sfx.append(SfxItem(start=0.0, kind="ding", volume=0.4))
    if style != "podcast":
        # Évite l'empilement : au plus un bruitage toutes les 1,2 s.
        sfx.sort(key=lambda s: s.start)
        dedup: list[SfxItem] = []
        for s in sfx:
            if not dedup or s.start - dedup[-1].start >= 1.2:
                dedup.append(s)
        tl.sfx = dedup

    # 4. Textes : titre d'ouverture, accroche, bandeau, fin
    title = tl.title or project_name
    texts: list[TextItem] = []
    if style == "youtube" and title:
        texts.append(TextItem(start=0.2, duration=2.6, text=title, style="titre", position="centre"))
        if total > 20:
            texts.append(TextItem(start=max(0.0, total - 3.0), duration=2.8,
                                  text="Abonne-toi pour la suite !", style="bandeau", position="bas"))
    elif style == "short":
        hook = by_id.get(clips[0].segment_id or "")
        if hook and hook.text:
            texts.append(TextItem(start=0.0, duration=min(2.2, clips[0].duration), text=_first_words(hook.text, 4),
                                  style="gros_texte", position="haut"))
        elif title:
            texts.append(TextItem(start=0.0, duration=2.0, text=title, style="gros_texte", position="haut"))
        top_clip = max(range(len(clips)), key=lambda k: clips[k].score)
        if top_clip:
            texts.append(TextItem(start=starts[top_clip] + 0.2, duration=1.0, text="⚡", style="emoji", position="haut"))
    elif style == "vlog" and title:
        texts.append(TextItem(start=0.5, duration=3.0, text=title, style="titre", position="bas"))
    elif style == "podcast" and title:
        texts.append(TextItem(start=1.0, duration=4.0, text=title, style="bandeau", position="bas"))
    tl.texts = texts

    # 5. B-roll : on illustre les longues prises de parole avec les meilleurs plans d'action non utilisés
    if style in ("vlog", "youtube"):
        used = {c.segment_id for c in clips}
        broll = sorted((s for s in segments if s.kind == "action" and s.id not in used and s.duration >= 1.5),
                       key=lambda s: s.score, reverse=True)
        overlays: list[OverlayItem] = []
        for i, c in enumerate(clips):
            if not broll:
                break
            seg = by_id.get(c.segment_id or "")
            if not seg or seg.kind != "speech" or c.duration < 6.0:
                continue
            b = broll.pop(0)
            L = min(2.5, b.duration)
            mid = (b.start + b.end) / 2
            overlays.append(OverlayItem(
                media_id=b.media_id, src_in=round(mid - L / 2, 3), src_out=round(mid + L / 2, 3),
                start=round(starts[i] + c.duration / 2 - L / 2, 3),
                mode="plein" if style == "vlog" else "incrustation",
                reason="Plan de coupe pour illustrer une longue prise de parole."))
        tl.overlays = overlays
    return tl
