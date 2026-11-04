"""Mettre en avant ce dont parle la voix, et caler les plans sur la durée des phrases.

Deux méthodes de monteur :
- un plan dure aussi longtemps que la phrase dite dessus : on ne coupe pas au milieu d'une phrase, et un plan de
  coupe finit avec la phrase qu'il illustre ;
- quand la voix montre quelque chose (« regardez ici », « ce bouton », « voilà le résultat »), le reste de l'image
  s'assombrit, la zone est entourée et le cadre s'en approche doucement (projecteur).

La zone est trouvée par Claude sur l'image quand une clé est là, sinon par une carte de saillance locale (la
partie de l'image qui ressort le plus, en dehors du visage de la personne qui parle).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess

import numpy as np

from .. import config
from ..media.ffmpeg import FFMPEG
from ..models import Clip, Keyframe, Mask, Project, Region, Timeline, Transcript, Word

log = logging.getLogger(__name__)

SENT_END = re.compile(r"[.!?…]$")
SENT_GAP = 0.5  # un blanc de 0,5 s termine une phrase
MAX_EXTEND = 3.0  # un plan s'allonge de 3 s au plus pour finir sa phrase
MIN_GAP_S = 7.0  # au plus un projecteur toutes les 7 s
MAX_SPOTS = 6
DIM = 0.55  # assombrissement du reste de l'image

# La voix désigne quelque chose de visible.
MENTION = re.compile(
    r"\b(regarde[sz]?|regardez|vous voyez|tu vois|on voit|voici|voilà|(?:je )?(?:vous|te) montre|derrière moi|juste ici|ici|là[- ]dessus|là[- ]haut|"
    r"là[- ]bas|celui[- ]ci|celle[- ]ci|ceux[- ]ci|ce bouton|cette zone|cet endroit|à gauche|à droite|en haut|"
    r"en bas|look|check this|right here|over here|this one|see this|you can see)\b", re.IGNORECASE)


# ---------------------------------------------------------------- phrases

def sentences(words: list[Word]) -> list[tuple[float, float, str]]:
    """Phrases d'une transcription : (début, fin, texte)."""
    out: list[tuple[float, float, str]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (w.start - cur[-1].end >= SENT_GAP or SENT_END.search(cur[-1].text)):
            out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
    return out


def _shift_after(tl: Timeline, t: float, dt: float) -> None:
    from .quality import _shift_after as shift
    shift(tl, t, dt)


def fit_to_sentences(tl: Timeline, transcripts: dict[str, Transcript]) -> list[str]:
    """Un plan ne finit pas au milieu d'une phrase ; un plan de coupe finit avec sa phrase. Renvoie ce qui a changé."""
    done: list[str] = []
    t0 = 0.0
    for i, c in enumerate(tl.clips):
        nxt = tl.clips[i + 1] if i + 1 < len(tl.clips) else None
        tr = transcripts.get(c.media_id)
        contiguous = nxt is not None and nxt.media_id == c.media_id and abs(nxt.src_in - c.src_out) < 0.05
        if tr and tr.words and not c.ramp and abs(c.speed - 1) < 1e-3 and not contiguous:
            sent = next((s for s in sentences(tr.words) if s[0] < c.src_out - 0.05 < s[1]), None)
            if sent and sent[0] >= c.src_in - 0.05:
                # La phrase commence dans le plan mais finit après : on la laisse finir.
                end = round(sent[1] + 0.15, 3)
                # Sans reprendre un passage déjà monté ailleurs.
                later = [o.src_in for o in tl.clips if o is not c and o.media_id == c.media_id and o.src_in >= c.src_out - 0.05]
                if later:
                    end = min(end, min(later))
                dt = end - c.src_out
                if 0.08 < dt <= MAX_EXTEND:
                    old = t0 + c.duration
                    c.src_out = end
                    _shift_after(tl, old - 1e-3, dt)
                    done.append(f"plan {i + 1} allongé de {dt:.1f} s pour finir la phrase")
        t0 += c.duration
    # Plans de coupe : finir avec la phrase qu'ils couvrent.
    from ..render import captions as cap
    lines = sentences(cap.timeline_words(tl, transcripts))
    for o in tl.overlays:
        end = o.start + o.duration
        s = next((s for s in lines if s[0] < end - 0.05 < s[1]), None)
        if not s:
            continue
        target = s[1] + 0.1
        if target - end <= 2.0:
            o.src_out = round(o.src_in + (target - o.start), 3)
            done.append(f"plan de coupe à {o.start:.1f} s calé sur la fin de la phrase")
        else:
            prev = max((x[1] for x in lines if x[1] <= end and x[1] - o.start >= 1.2), default=None)
            if prev:
                o.src_out = round(o.src_in + (prev + 0.1 - o.start), 3)
                done.append(f"plan de coupe à {o.start:.1f} s raccourci à la fin de la phrase")
    return done


# Sans Claude pour regarder l'image, on ne garde que les désignations explicites d'un endroit.
STRONG = re.compile(r"\b(regarde[sz]?|regardez|voici|voilà|juste ici|ici|là[- ]dessus|là[- ]haut|là[- ]bas|"
                    r"celui[- ]ci|celle[- ]ci|à gauche|à droite|en haut|en bas|look|right here|over here)\b",
                    re.IGNORECASE)

# ---------------------------------------------------------------- mentions

def mentions(tl: Timeline, transcripts: dict[str, Transcript]) -> list[dict]:
    """Instants où la voix montre quelque chose : plan, intervalle local, instant source, phrase."""
    out: list[dict] = []
    t0, last = 0.0, -1e9
    for i, c in enumerate(tl.clips):
        tr = transcripts.get(c.media_id)
        if tr and tr.words and not c.ramp:
            for s0, s1, text in sentences([w for w in tr.words if c.src_in <= (w.start + w.end) / 2 < c.src_out]):
                m = MENTION.search(text)
                if not m:
                    continue
                # Début : le mot qui désigne ; fin : la fin de la phrase (1,2 à 4 s).
                frac = m.start() / max(1, len(text))
                a = s0 + frac * (s1 - s0)
                b = min(max(s1 + 0.2, a + 1.2), a + 4.0, c.src_out)
                la, lb = c.src_to_local(max(0.0, a - c.src_in)), c.src_to_local(max(0.0, b - c.src_in))
                if lb - la < 0.8 or t0 + la - last < MIN_GAP_S:
                    continue
                out.append({"clip": i, "start": round(la, 3), "end": round(lb, 3), "src_t": round(a + 0.3, 3),
                            "t": round(t0 + la, 3), "sentence": text, "word": m.group(0)})
                last = t0 + la
                if len(out) >= MAX_SPOTS:
                    return out
        t0 += c.duration
    return out


# ---------------------------------------------------------------- où regarder

def _gray(path: str, t: float, w: int = 96, h: int = 54) -> np.ndarray | None:
    p = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode != 0 or len(p.stdout) < w * h:
        return None
    return np.frombuffer(p.stdout[: w * h], np.uint8).reshape(h, w).astype(np.float32)


def _blur(a: np.ndarray, k: int = 5) -> np.ndarray:
    ker = np.ones(k) / k
    a = np.apply_along_axis(lambda r: np.convolve(r, ker, mode="same"), 1, a)
    return np.apply_along_axis(lambda r: np.convolve(r, ker, mode="same"), 0, a)


def saliency(g: np.ndarray) -> np.ndarray:
    """Carte de saillance par résidu spectral (Hou & Zhang) : ce qui ressort du reste de l'image."""
    f = np.fft.fft2(g)
    logamp = np.log(np.abs(f) + 1e-6)
    resid = logamp - _blur(logamp, 3)
    s = np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(f)))) ** 2
    return _blur(s, 7)


def local_region(path: str, t: float, face_cx: float | None = None) -> dict | None:
    """Zone qui ressort le plus de l'image à l'instant t (hors visage de la personne qui parle)."""
    g = _gray(path, t)
    if g is None or g.std() < 4:
        return None
    s = saliency(g)
    h, w = s.shape
    # Bords ignorés (artefacts de la FFT), visage atténué : on cherche ce qu'on montre, pas qui parle.
    m = 3
    s[:m], s[-m:], s[:, :m], s[:, -m:] = 0, 0, 0, 0
    if face_cx is not None:
        cols = np.arange(w) / w
        s *= np.where(np.abs(cols - face_cx) < 0.13, 0.35, 1.0)[None, :]
    peak = float(s.max())
    if peak <= 0 or (peak - float(np.median(s))) < 1.5 * float(s.std()):
        return None  # rien ne ressort nettement (image uniforme ou bruit partout)
    y, x = np.unravel_index(int(np.argmax(s)), s.shape)
    # La saillance dit où regarder ; l'image dit où s'arrête l'objet : pixels qui tranchent sur le fond local.
    ry, rx = int(h * 0.2), int(w * 0.16)
    y0w, y1w, x0w, x1w = max(0, y - ry), min(h, y + ry + 1), max(0, x - rx), min(w, x + rx + 1)
    win = g[y0w:y1w, x0w:x1w]
    border = np.concatenate([win[0], win[-1], win[:, 0], win[:, -1]])
    diff = np.abs(win - np.median(border))
    ys, xs = np.where(diff > max(12.0, 0.4 * float(diff.max())))
    if len(ys):
        x0, x1 = (x0w + xs.min()) / w, (x0w + xs.max() + 1) / w
        y0, y1 = (y0w + ys.min()) / h, (y0w + ys.max() + 1) / h
    else:
        x0, x1, y0, y1 = (x - 4) / w, (x + 5) / w, (y - 4) / h, (y + 5) / h
    bw, bh = min(0.45, max(0.12, x1 - x0 + 0.04)), min(0.5, max(0.12, y1 - y0 + 0.06))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # Une zone sans détail (mur, épaule, ciel) ne mérite pas le projecteur.
    zy0, zy1 = int(max(0, cy - bh / 2) * h), int(min(1, cy + bh / 2) * h) + 1
    zx0, zx1 = int(max(0, cx - bw / 2) * w), int(min(1, cx + bw / 2) * w) + 1
    patch = g[zy0:zy1, zx0:zx1]
    edges = np.abs(np.diff(patch, axis=1)).mean() + np.abs(np.diff(patch, axis=0)).mean()
    whole = np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()
    if edges < 0.8 * whole:
        return None
    return {"x": float(cx - bw / 2), "y": float(cy - bh / 2), "w": float(bw), "h": float(bh), "label": ""}


LOCATE_SYSTEM = """Tu aides un monteur vidéo. Pour chaque image, on te donne la phrase dite à ce moment. \
Si la phrase désigne quelque chose de visible sur l'image (un objet, un bouton, une zone de l'écran, un détail), \
donne le cadre de cet élément en coordonnées normalisées (x, y du coin haut gauche, largeur w, hauteur h, entre 0 \
et 1) et un label de 1 à 3 mots. Le visage de la personne qui parle n'est pas une réponse. Si rien de visible \
n'est désigné, found = false."""

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "found": {"type": "boolean"}, "x": {"type": "number"}, "y": {"type": "number"},
        "w": {"type": "number"}, "h": {"type": "number"}, "label": {"type": "string"}},
        "required": ["index", "found", "x", "y", "w", "h", "label"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def _jpeg(path: str, t: float) -> bytes | None:
    p = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=768:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"], capture_output=True)
    return p.stdout if p.returncode == 0 and p.stdout else None


class ClaudeLocator:
    def __init__(self) -> None:
        import anthropic
        self.client = anthropic.Anthropic()

    def locate(self, items: list[tuple[str, float, str]]) -> list[dict | None]:
        """items : (chemin du rush, instant source, phrase). Une zone (ou None) par élément."""
        content: list = []
        for k, (path, t, sentence) in enumerate(items):
            jpg = _jpeg(path, t)
            if not jpg:
                continue
            content.append({"type": "text", "text": f"Image {k} — phrase : « {sentence} »"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(jpg).decode()}})
        if not content:
            return [None] * len(items)
        content.append({"type": "text", "text": "Donne la zone désignée pour chaque image."})
        r = self.client.beta.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=3000, system=LOCATE_SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": LOCATE_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        if r.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"repérage interrompu ({r.stop_reason})")
        data = json.loads(next(b.text for b in r.content if b.type == "text"))
        out: list[dict | None] = [None] * len(items)
        for it in data.get("items", []):
            k = it.get("index")
            if isinstance(k, int) and 0 <= k < len(items) and it.get("found") and it.get("w", 0) > 0.02:
                out[k] = {"x": it["x"], "y": it["y"], "w": it["w"], "h": it["h"], "label": it.get("label", "")[:24]}
        return out


def get_locator():
    if config.claude_available():
        try:
            return ClaudeLocator()
        except Exception as e:  # pragma: no cover
            log.warning("Repérage Claude indisponible : %s", e)
    return None


# ---------------------------------------------------------------- projecteur

def _face_cx(faces: dict | None, t: float) -> float | None:
    if not faces or not faces.get("cx"):
        return None
    k = int(round(t / max(1e-6, faces.get("hop", 1 / 3))))
    cx = faces["cx"]
    return float(cx[min(max(0, k), len(cx) - 1)])


def _push_in(c: Clip, start: float, end: float, cx: float, cy: float) -> None:
    """Le cadre s'approche doucement de la zone pendant le projecteur, puis revient."""
    z0 = max(1.0, c.zoom)
    z1 = min(4.0, z0 * 1.15)
    bx, by = c.focus_x if c.focus_x is not None else 0.5, c.focus_y if c.focus_y is not None else 0.5
    tx, ty = bx + (cx - bx) * 0.6, by + (cy - by) * 0.6
    d = c.duration
    pts = [(0.0, z0, bx, by), (max(0.0, start - 0.05), z0, bx, by), (min(d, start + 0.5), z1, tx, ty),
           (max(start + 0.5, end - 0.1), z1, tx, ty), (min(d, end + 0.5), z0, bx, by), (d, z0, bx, by)]
    kfs: list[Keyframe] = []
    for t, z, x, y in pts:
        if kfs and t - kfs[-1].t < 0.05:
            continue
        kfs.append(Keyframe(t=round(t, 3), zoom=round(z, 3), x=round(min(1, max(0, x)), 3),
                            y=round(min(1, max(0, y)), 3), ease="doux"))
    c.keyframes = kfs


def add_spotlights(project: Project, tl: Timeline, transcripts: dict[str, Transcript], faces: dict | None = None,
                   locator=None, narrow: set[str] | None = None) -> list[str]:
    """Pose un projecteur sur ce que la voix désigne. narrow : rushes recadrés (pas de zoom ajouté)."""
    found = mentions(tl, transcripts)
    if not found:
        return []
    media = {m.id: m for m in project.media}
    found = [f for f in found if (m := media.get(tl.clips[f["clip"]].media_id)) and m.has_video]
    items = [(media[tl.clips[f["clip"]].media_id].proxy or media[tl.clips[f["clip"]].media_id].path,
              f["src_t"], f["sentence"]) for f in found]
    zones: list[dict | None] = [None] * len(found)
    if locator is not None:
        try:
            zones = locator.locate(items)
        except Exception as e:
            log.warning("Repérage Claude en échec, repérage local : %s", e)
    done: list[str] = []
    for f, zone, (path, t, _) in zip(found, zones, items):
        c = tl.clips[f["clip"]]
        if zone is None and locator is None and STRONG.search(f["sentence"]):
            zone = local_region(path, t, _face_cx((faces or {}).get(c.media_id), t))
        if zone is None:
            continue
        if any(mk.kind == "projecteur" and mk.start is not None and mk.start < f["end"] and f["start"] < (mk.end or 0)
               for mk in c.masks):
            continue
        r = Region(x=zone["x"], y=zone["y"], w=zone["w"], h=zone["h"]).clamp()
        mk = Mask(kind="projecteur", x=r.x, y=r.y, w=r.w, h=r.h, start=f["start"], end=f["end"],
                  label=zone.get("label", ""), reason=f"« {f['word']} » : {f['sentence'][:60]}")
        c.masks.append(mk)
        if not c.keyframes and c.media_id not in (narrow or set()):
            _push_in(c, f["start"], f["end"], mk.x + mk.w / 2, mk.y + mk.h / 2)
            mk.push_in = True
        done.append(f"projecteur à {f['t']:.1f} s{(' sur « ' + mk.label + ' »') if mk.label else ''}")
    return done
