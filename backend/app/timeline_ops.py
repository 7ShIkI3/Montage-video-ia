"""Opérations sur la timeline indépendantes de l'interface : coupe d'un plan, extraction d'une zone.

`slice_timeline` sert à l'export « zone seule » : il garde ce qui est entre l'entrée et la sortie de la
zone (plans, textes, bruitages, images, impacts, pistes audio, plans de coupe, repères) et recale tout sur
un nouveau zéro, musique comprise, pour que le rendu de la zone soit identique à ce passage du montage
complet.
"""
from __future__ import annotations

from .models import Clip, Keyframe, Timeline, new_id

MIN_CLIP = 0.05


def kf_at(c: Clip, t: float) -> Keyframe | None:
    """Image clé interpolée à l'instant t du plan (même courbe que l'aperçu et le rendu)."""
    ks = sorted(c.keyframes, key=lambda k: k.t)
    if not ks:
        return None
    if t <= ks[0].t:
        return ks[0].model_copy()
    for a, b in zip(ks, ks[1:]):
        if t < b.t:
            u = max(0.0, min(1.0, (t - a.t) / max(1e-3, b.t - a.t)))
            if b.ease == "doux":
                u = u * u * (3 - 2 * u)
            return Keyframe(t=t, zoom=a.zoom + (b.zoom - a.zoom) * u, x=a.x + (b.x - a.x) * u,
                            y=a.y + (b.y - a.y) * u, ease=b.ease)
    return ks[-1].model_copy()


def cut_clip(c: Clip, t: float) -> tuple[Clip, Clip]:
    """Coupe le plan à l'instant local t (secondes de timeline depuis son début), comme la touche S :
    la rampe de vitesse et les images clés continuent de l'autre côté de la coupe."""
    cut = c.src_in + c.local_to_src(t)
    a = c.model_copy(deep=True)
    b = c.model_copy(deep=True, update={"id": new_id("c_"), "src_in": cut, "transition": "aucune", "fade_in": 0.0})
    a.fade_out = 0.0
    if c.ramp:
        mid = c.speed_at(t)
        a.speed_end = mid
        b.speed = mid
    if c.keyframes:
        here = kf_at(c, t)
        assert here is not None
        b.keyframes = [Keyframe(**{**here.model_dump(), "t": 0.0})] + \
            [k.model_copy(update={"t": round(k.t - t, 3)}) for k in c.keyframes if k.t > t]
        a.keyframes = [k for k in c.keyframes if k.t < t] + [Keyframe(**{**here.model_dump(), "t": round(t, 3)})]
    a.src_out = cut
    return a, b


def _clips_between(tl: Timeline, start: float, end: float) -> list[Clip]:
    out: list[Clip] = []
    t = 0.0
    for c in tl.clips:
        d = c.duration
        c0, c1 = t, t + d
        t = c1
        if c1 <= start + 1e-6 or c0 >= end - 1e-6:
            continue
        piece = c
        if start > c0 + 1e-6:
            _, piece = cut_clip(piece, start - c0)
            c0 = start
        if end < c1 - 1e-6:
            piece, _ = cut_clip(piece, end - c0)
        if piece.duration >= MIN_CLIP:
            out.append(piece)
    return out


def slice_timeline(tl: Timeline, start: float, end: float) -> Timeline:
    """Nouvelle timeline réduite à [start, end], recalée à zéro. Le titre et les réglages sont gardés."""
    total = tl.duration
    start = max(0.0, min(start, total))
    end = max(start, min(end, total))
    out = tl.model_copy(deep=True)
    out.clips = _clips_between(tl, start, end)
    out.range = None
    out.quality = None
    out.review = None
    span = end - start

    def shift(items, dur_of=None, trim=None):
        kept = []
        for it in items:
            d = dur_of(it) if dur_of else None
            it_end = it.start + d if d is not None else it.start
            if it.start >= end - 1e-6 or (d is not None and it_end <= start + 1e-6) or (d is None and it.start < start - 1e-6):
                continue
            lead = max(0.0, start - it.start)
            it.start = round(max(0.0, it.start - start), 3)
            if d is not None:
                new_d = min(d - lead, span - it.start)
                if new_d <= 1e-3:
                    continue
                if trim:
                    trim(it, lead, new_d)
            kept.append(it)
        return kept

    def trim_duration(it, lead, new_d):
        it.duration = round(new_d, 3)

    def trim_src(it, lead, new_d):
        it.src_in = round(it.src_in + lead, 3)
        it.src_out = round(it.src_in + new_d, 3)

    out.texts = shift(out.texts, lambda x: x.duration, trim_duration)
    out.sfx = shift(out.sfx)
    out.impacts = shift(out.impacts)
    out.images = shift(out.images, lambda g: g.duration if g.duration is not None else max(0.0, total - g.start),
                       lambda g, lead, new_d: setattr(g, "duration", None if g.duration is None else round(new_d, 3)))
    out.audio = shift(out.audio, lambda a: a.src_out - a.src_in, trim_src)
    out.overlays = shift(out.overlays, lambda o: o.src_out - o.src_in, trim_src)
    markers = []
    for k in out.markers:
        if start - 1e-6 <= k.t < end - 1e-6:
            k.t = round(k.t - start, 3)
            markers.append(k)
    out.markers = markers
    if out.music.track:
        out.music.start = round(out.music.start + start, 3)  # la musique reste calée sur le même passage
    return out
