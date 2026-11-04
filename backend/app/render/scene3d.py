"""Compositeur 3D de la timeline : effets 3D par plan, transitions 3D, impacts (secousse, zoom,
flash, étincelles, onde de choc) et textes en relief.

Il travaille image par image entre deux passes FFmpeg (voir render.render) :
FFmpeg décode et monte -> ce module compose la 3D -> FFmpeg ajoute les sous-titres et encode.
"""
from __future__ import annotations

import bisect
import math
import zlib

import cv2
import numpy as np

from ..models import Timeline
from . import engine3d as e3

CUTOUT = {
    "detourage_flou": "Fond flouté (IA)",
    "detourage_studio": "Fond studio (IA)",
    "detourage_noir": "Fond noir (IA)",
    "detourage_blanc": "Fond blanc (IA)",
    "detourage_image": "Fond image (IA)",
}

EFFECTS_3D = {
    "3d_orbite": "Orbite 3D (caméra qui tourne)",
    "3d_bascule": "Bascule 3D d'entrée",
    "3d_rotation": "Rotation 3D d'entrée",
    "3d_flottant": "Écran flottant 3D",
}
TRANSITIONS_3D = {
    "cube_3d": "Cube 3D",
    "flip_3d": "Retournement 3D",
    "porte_3d": "Porte 3D",
}
IMPACTS = {
    "secousse": {"label": "Secousse 3D", "life": 0.42},
    "zoom": {"label": "Zoom choc", "life": 0.34},
    "flash": {"label": "Flash", "life": 0.14},
    "flash_rouge": {"label": "Flash rouge (dégât)", "life": 0.2},
    "etincelles": {"label": "Étincelles 3D", "life": 0.6},
    "onde": {"label": "Onde de choc", "life": 0.45},
}
TEXT_PALETTES = [
    ((255, 245, 120), (255, 140, 40), (110, 40, 10), (255, 150, 40)),   # or
    ((140, 240, 255), (60, 120, 255), (15, 30, 90), (60, 140, 255)),    # bleu
    ((255, 150, 210), (190, 60, 255), (60, 15, 80), (210, 80, 255)),    # violet
    ((255, 120, 110), (220, 20, 40), (80, 5, 10), (255, 60, 60)),       # rouge
]


def needs_3d(tl: Timeline) -> bool:
    return bool(tl.impacts or any(t.style == "texte_3d" for t in tl.texts)
                or any(c.transition in TRANSITIONS_3D or any(e in EFFECTS_3D or e in CUTOUT for e in c.effects)
                       for c in tl.clips))


class Scene3D:
    def __init__(self, tl: Timeline, W: int, H: int, fps: int, background: np.ndarray | None = None,
                 assets_dir=None):
        self.tl, self.W, self.H, self.fps = tl, W, H, fps
        self.assets_dir = assets_dir
        self.background = background  # image de fond du détourage, déjà à la taille W x H
        self._seg = None
        self.cam = e3.Camera(W, H, 50.0)
        self.starts: list[float] = []
        t = 0.0
        for c in tl.clips:
            self.starts.append(t)
            t += c.duration
        self.total = t
        self.cur = -1
        self.last_raw: np.ndarray | None = None
        self.prev_last: np.ndarray | None = None
        self.impacts = sorted(tl.impacts, key=lambda i: i.start)
        self.impact_starts = [i.start for i in self.impacts]
        self.bursts = {i.id: e3.burst(zlib.crc32(i.id.encode()), n=int(30 + 30 * min(2.0, i.intensity)))
                       for i in self.impacts if i.kind == "etincelles"}
        self.texts = [x for x in tl.texts if x.style == "texte_3d"]

    # -- outils -------------------------------------------------------------
    def _clip_at(self, t: float) -> int:
        return max(0, bisect.bisect_right(self.starts, t) - 1)

    def _card(self, canvas, img, R, scale=1.0, offset=(0.0, 0.0, 0.0), pivot=(0.0, 0.0), shadow=False,
              shade=None) -> bool:
        dst = e3.card(self.cam, R, scale, offset, pivot)
        if not e3.faces_camera(dst):
            return False
        if shadow:
            e3.draw_shadow(canvas, dst, offset=(0, int(self.H * 0.025)))
        e3.draw_image(canvas, img, dst, shade=e3.light(R, 0.72, 0.3) if shade is None else shade)
        return True

    # -- effets par plan ------------------------------------------------------
    def _effect(self, frame, name: str, u: float, d: float):
        if name == "3d_orbite":
            T = max(3.0, d)
            k = min(e3.ease_out_cubic(u / 0.45), e3.ease_out_cubic((d - u) / 0.35))
            R = e3.rotation(5 * math.sin(2 * math.pi * u / (T * 0.8)) * k, 15 * math.sin(2 * math.pi * u / T) * k, 0)
            out = e3.backdrop(frame)
            self._card(out, frame, R, scale=1 - 0.16 * k, shadow=True)
            return out
        if name == "3d_flottant":
            k = min(e3.ease_out_cubic(u / 0.5), e3.ease_out_cubic((d - u) / 0.35))
            R = e3.rotation(7 * k, (-17 + 3 * math.sin(u * 1.3)) * k, -2 * k)
            out = e3.backdrop(frame)
            self._card(out, frame, R, scale=1 - 0.2 * k, offset=(0, 8 * math.sin(u * 2.1) * k, 0), shadow=True)
            return out
        if name == "3d_bascule" and u < 0.65:
            a = 78 * (1 - e3.ease_out_back(u / 0.65, 1.3))
            out = e3.backdrop(frame, 0.25)
            self._card(out, frame, e3.rotation(a, 0, 0), pivot=(0, -0.5), shadow=True)
            return out
        if name == "3d_rotation" and u < 0.6:
            v = e3.ease_out_back(u / 0.6, 1.2)
            out = e3.backdrop(frame, 0.25)
            self._card(out, frame, e3.rotation(0, 105 * (1 - v), -8 * (1 - v)),
                       offset=(0, 0, 1.4 * self.cam.f * (1 - e3.ease_out_cubic(u / 0.6))), shadow=True)
            return out
        return frame

    # -- transitions ---------------------------------------------------------
    def _transition(self, prev, new, name: str, u: float):
        """u : progression 0..1 de la transition d'entrée."""
        W, f = self.W, self.cam.f
        out = e3.backdrop(new, 0.22)
        if name == "cube_3d":
            th = 90 * e3.ease_in_out(u)
            push = 0.45 * W * math.sin(math.pi * u)
            C = np.array([0.0, 0.0, f + W / 2 + push])
            faces = []
            for img, base, Rl in ((prev, np.array([0, 0, -W / 2]), 0.0), (new, np.array([W / 2, 0, 0]), -90.0)):
                R = e3.rotation(0, th + Rl, 0)
                Rc = e3.rotation(0, th, 0)
                center = C + Rc @ base
                faces.append((center[2], img, R, center))
            for _, img, R, center in sorted(faces, key=lambda x: -x[0]):
                dst = self.cam.project(e3.quad(center, W, self.H, R))
                if e3.faces_camera(dst):
                    e3.draw_image(out, img, dst, shade=e3.light(R, 0.45, 0.55))
            return out
        if name == "flip_3d":
            a = 180 * e3.ease_in_out(u)
            push = 0.55 * f * math.sin(math.pi * u)
            if a < 90:
                self._card(out, prev, e3.rotation(0, a, 0), offset=(0, 0, push), shadow=True)
            else:
                self._card(out, new, e3.rotation(0, a - 180, 0), offset=(0, 0, push), shadow=True)
            return out
        if name == "porte_3d":
            bg = prev.astype(np.float32) * (1 - 0.6 * u)
            out = bg.astype(np.uint8)
            a = -100 * (1 - e3.ease_out_cubic(u))
            self._card(out, new, e3.rotation(0, a, 0), pivot=(-0.5, 0), shadow=True)
            return out
        return new

    # -- impacts --------------------------------------------------------------
    def _active_impacts(self, t: float):
        j = bisect.bisect_right(self.impact_starts, t)
        out = []
        for i in self.impacts[max(0, j - 12):j]:
            dt = t - i.start
            if 0 <= dt <= IMPACTS.get(i.kind, {"life": 0.4})["life"]:
                out.append((i, dt))
        return out

    def _camera_hit(self, frame, active):
        rx = ry = rz = 0.0
        scale = 1.0
        for i, dt in active:
            A = max(0.2, min(2.5, i.intensity))
            if i.kind == "secousse":
                u = dt / IMPACTS["secousse"]["life"]
                k = e3.decay(u, 4.5) * A
                rz += 2.6 * k * math.sin(2 * math.pi * 7.5 * dt)
                rx += 2.2 * k * math.sin(2 * math.pi * 6.1 * dt + 1.3)
                ry += 2.4 * k * math.sin(2 * math.pi * 6.9 * dt + 2.1)
                scale += 0.035 * k
            elif i.kind == "zoom":
                u = dt / IMPACTS["zoom"]["life"]
                rise = min(1.0, dt / 0.05)
                scale += 0.2 * A * rise * e3.decay(u, 3.5)
        if abs(rx) + abs(ry) + abs(rz) < 1e-3 and scale <= 1.0005:
            return frame
        # Agrandissement pour que les bords ne se voient jamais pendant la secousse.
        over = 1 + 0.022 * (abs(rx) + abs(ry) + abs(rz))
        dst = e3.card(self.cam, e3.rotation(rx, ry, rz), scale * over)
        M = cv2.getPerspectiveTransform(np.float32([[0, 0], [self.W, 0], [self.W, self.H], [0, self.H]]),
                                        np.float32(dst))
        return cv2.warpPerspective(frame, M, (self.W, self.H), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)

    def _overlays(self, frame, active):
        for i, dt in active:
            A = max(0.2, min(2.5, i.intensity))
            if i.kind in ("flash", "flash_rouge"):
                u = dt / IMPACTS[i.kind]["life"]
                a = min(0.85, 0.7 * A) * (1 - u) ** 1.5
                if i.kind == "flash_rouge":
                    # Vignette rouge : plus forte sur les bords, comme un écran de dégât.
                    m = self._vignette_mask() * a
                    frame[:] = cv2.blendLinear(self._red(), frame, m, 1.0 - m)
                else:
                    frame[:] = cv2.addWeighted(frame, 1 - a, frame, 0, 255 * a)
            elif i.kind == "etincelles":
                e3.draw_particles(frame, self.cam, self.bursts[i.id], (i.x * self.W, i.y * self.H), dt)
            elif i.kind == "onde":
                e3.draw_shockwave(frame, self.cam, (i.x * self.W, i.y * self.H), dt, IMPACTS["onde"]["life"])
        return frame

    _vig = None
    _redimg = None

    def _red(self):
        if self._redimg is None:
            self._redimg = np.empty((self.H, self.W, 3), np.uint8)
            self._redimg[:] = (40, 30, 235)
        return self._redimg

    def _vignette_mask(self):
        if self._vig is None:
            y, x = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
            r = np.sqrt(((x - self.W / 2) / (self.W / 2)) ** 2 + ((y - self.H / 2) / (self.H / 2)) ** 2)
            self._vig = np.clip((r - 0.35) / 0.9, 0.15, 1.0).astype(np.float32)
        return self._vig

    # -- textes 3D ------------------------------------------------------------
    def _texts(self, frame, t: float):
        for k, x in enumerate(self.texts):
            u = t - x.start
            if not 0 <= u <= x.duration:
                continue
            top, bottom, side, glow = TEXT_PALETTES[k % len(TEXT_PALETTES)]
            n = max(1, len(x.text))
            size = int(min(self.H / 6.2, self.W * 0.95 / (0.62 * n)))
            sprite = e3.text_sprite(x.text, max(18, size), top, bottom, side)
            fit = min(1.0, self.W * 0.9 / sprite.w)
            y_px = {"haut": 0.2, "centre": 0.5, "bas": 0.78}.get(x.position, 0.5) * self.H
            inn = e3.ease_out_back(u / 0.45, 1.4)
            out_u = max(0.0, (u - (x.duration - 0.28)) / 0.28)
            ry = -85 * (1 - inn) + 9 * math.sin(2 * math.pi * u / 2.4) * inn
            rx = 6 * math.sin(2 * math.pi * u / 3.1) * inn - 12 * (1 - inn)
            z = self.cam.f * (1.6 * (1 - e3.ease_out_cubic(u / 0.45)) - 0.35 * e3.ease_in_out(out_u))
            center = (0.0, self.H / 2 - y_px, self.cam.f + z)
            e3.draw_text3d(frame, self.cam, sprite, center, e3.rotation(rx, ry, 0), scale=fit,
                           opacity=1 - out_u, glow_color=glow)
        return frame

    def _cutout(self, frame, kind: str, t: float):
        """Détoure le plan principal. Les calques posés par FFmpeg (plan de coupe, logo) sont déjà dans l'image :
        on remet leurs pixels d'origine après le détourage pour ne pas les effacer."""
        from ..analysis import segment
        keep = self._protected(t)
        if keep is None:
            return frame  # plan de coupe plein écran : le plan principal n'est pas visible
        if self._seg is None:
            self._seg = segment.Segmenter()
        out = segment.composite(frame, self._seg.mask(frame), kind, self.background)
        for x0, y0, x1, y1 in keep:
            out[y0:y1, x0:x1] = frame[y0:y1, x0:x1]
        return out

    def _protected(self, t: float):
        """Rectangles (x0, y0, x1, y1) à ne pas détourer à l'instant t, ou None si rien du plan principal n'est visible."""
        W, H = self.W, self.H
        even = lambda x: max(2, int(round(x / 2)) * 2)  # noqa: E731 (même arrondi que render.py)
        rects = []
        for o in self.tl.overlays:
            if not (o.start <= t < o.start + o.duration):
                continue
            if o.mode == "plein":
                return None
            if o.mode == "partage":
                rects.append((W - even(W / 2), 0, W, H) if W >= H else (0, H - even(H / 2), W, H))
            else:
                pw = even(W * 0.34)
                ph = even(pw * H / W)
                if o.chroma == "aucun":
                    pw, ph = pw + 8, ph + 8
                mx, my = int(W * 0.04), int(H * 0.05)
                x0 = W - pw - mx if o.corner.endswith("droite") else mx
                y0 = my if o.corner.startswith("haut") else H - ph - my
                rects.append((x0, y0, x0 + pw, y0 + ph))
        for g in self.tl.images:
            if not (g.start <= t < g.start + (g.duration or 1e9)):
                continue
            gw = even(W * max(0.02, min(1.0, g.scale))) * (1.2 if g.anim == "pop" else 1.0)
            gh = gw * self._image_ratio(g.asset)
            cx, cy = W * g.x, H * g.y
            rects.append((max(0, int(cx - gw / 2) - 2), max(0, int(cy - gh / 2) - 2),
                          min(W, int(cx + gw / 2) + 2), min(H, int(cy + gh / 2) + 2)))
        return rects

    def _image_ratio(self, asset: str) -> float:
        cache = self.__dict__.setdefault("_ratios", {})
        if asset not in cache:
            img = cv2.imread(str(self.assets_dir / asset), cv2.IMREAD_UNCHANGED) if self.assets_dir else None
            cache[asset] = img.shape[0] / img.shape[1] if img is not None else 1.0
        return cache[asset]

    # -- image par image ---------------------------------------------------------
    def process(self, n: int, frame: np.ndarray) -> np.ndarray:
        t = n / self.fps
        i = self._clip_at(t)
        if i != self.cur:
            self.prev_last = self.last_raw
            self.cur = i
            if self._seg is not None:
                self._seg.reset()  # nouveau plan : pas de lissage avec le masque du plan précédent
        if self.tl.clips:
            cut = next((e for e in self.tl.clips[i].effects if e in CUTOUT), None)
            if cut:
                frame = self._cutout(frame, cut, t)
        self.last_raw = frame
        out = frame
        if self.tl.clips:
            c = self.tl.clips[i]
            u = t - self.starts[i]
            td = max(0.25, min(1.2, c.transition_duration if c.transition_duration > 0.2 else 0.6))
            if c.transition in TRANSITIONS_3D and self.prev_last is not None and u < td:
                out = self._transition(self.prev_last, frame, c.transition, u / td)
            else:
                for e in c.effects:
                    if e in EFFECTS_3D:
                        out = self._effect(out, e, u, c.duration)
        active = self._active_impacts(t)
        if active:
            out = self._camera_hit(out, active)
            if out is frame:
                out = frame.copy()
            out = self._overlays(out, active)
        if self.texts:
            if out is frame:
                out = frame.copy()
            out = self._texts(out, t)
        return out
