"""Vision : Claude regarde vraiment les rushes.

L'analyse locale mesure du son et du mouvement, mais n'interprète pas l'image. Ici, des images sont extraites
aux instants qui comptent (fin des combos, écrans rouges, changements de plan, puis une grille régulière) et
envoyées à Claude, qui note pour chacune ce qu'on voit d'utile au montage et le texte lisible à l'écran
(message de kill, score, titre). Le résultat est gardé avec les signaux du rush et entre dans le dossier du
projet : le réalisateur (chat, montage automatique, recherche) sait alors ce qu'il y a dans les images.

Appel payant, donc jamais lancé tout seul : seulement sur action de l'utilisateur.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time

from .. import config, storage
from ..analysis import hits as hits_an
from ..media.ffmpeg import FFMPEG
from ..models import Media, Project

log = logging.getLogger(__name__)

MAX_FRAMES = 36  # images par rush et par passe
WIDTH = 768  # largeur des images envoyées (≈ 450 tokens chacune en 16:9)
TYPES = ["action", "elimination", "degats_recus", "victoire", "defaite", "dialogue", "texte_ecran", "menu",
         "paysage", "autre"]
ZONES = {  # (x, y, largeur, hauteur) en fractions de l'image : pour lire un détail en pleine définition
    "entier": None,
    "haut_gauche": (0.0, 0.0, 0.5, 0.5), "haut_droite": (0.5, 0.0, 0.5, 0.5),
    "bas_gauche": (0.0, 0.5, 0.5, 0.5), "bas_droite": (0.5, 0.5, 0.5, 0.5),
    "centre": (0.25, 0.25, 0.5, 0.5),
}

SYSTEM = (
    "Tu es l'œil d'un monteur vidéo. On te montre des images extraites d'un rush, chacune précédée de son numéro "
    "et de son instant. Pour chaque image, écris une phrase factuelle sur ce qu'on voit d'utile au montage : "
    "l'action, qui a l'avantage, ce que montre l'interface (barre de vie, score, viseur), et recopie le texte "
    "lisible qui compte (message d'élimination, chat, titre, sous-titre incrusté). Classe l'image. Ne déduis pas "
    "ce qui n'est pas visible : si un texte est illisible ou si l'issue d'un combat ne se voit pas, dis-le. "
    "Termine par un résumé du rush en deux ou trois phrases, utile pour choisir les meilleurs passages."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "resume": {"type": "string"},
        "observations": {"type": "array", "items": {"type": "object", "properties": {
            "index": {"type": "integer"}, "vu": {"type": "string"}, "type": {"type": "string", "enum": TYPES},
            "texte": {"type": "string"}},
            "required": ["index", "vu", "type", "texte"], "additionalProperties": False}},
    },
    "required": ["resume", "observations"], "additionalProperties": False,
}


def jpeg(path: str, t: float, zone: str = "entier", width: int = WIDTH) -> bytes | None:
    """Une image du rush à l'instant t, en JPEG ; `zone` recadre un quart de l'image avant la mise à l'échelle."""
    z = ZONES.get(zone)
    vf = f"scale={width}:-2"
    if z:
        x, y, w, h = z
        vf = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},{vf}"
    p = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
                        "-frames:v", "1", "-vf", vf, "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"],
                       capture_output=True)
    return p.stdout if p.returncode == 0 and p.stdout else None


def image_block(data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(data).decode()}}


def sample_instants(m: Media, signals: dict | None, limit: int = MAX_FRAMES) -> list[float]:
    """Instants à regarder, les plus parlants d'abord : fins de combos (l'issue du combat), écrans rouges,
    changements de plan, puis une grille régulière. Dédoublonnés à 0,6 s près, rendus dans l'ordre du temps."""
    sig = signals or {}
    dur = max(0.0, m.duration)
    wanted: list[float] = []
    for c in hits_an.combos(sig.get("hits") or []):
        wanted += [c["end"] + 0.6, c["start"] + 0.2, (c["start"] + c["end"]) / 2, c["end"] + 1.6]
    video = sig.get("video") or {}
    wanted += [t + 0.1 for t in (video.get("red_flashes") or [])]
    wanted += [t + 0.4 for t in (video.get("cuts") or [])]
    step = max(3.0, dur / 14) if dur else 3.0
    k = step / 2
    while k < dur:
        wanted.append(k)
        k += step
    kept: list[float] = []
    for t in wanted:
        t = round(min(max(0.0, t), max(0.0, dur - 0.05)), 2)
        if all(abs(t - u) > 0.6 for u in kept):
            kept.append(t)
        if len(kept) >= limit:
            break
    return sorted(kept)


def estimate(project: Project) -> dict:
    """Combien d'images une passe enverrait, et quels rushes ont déjà été regardés."""
    per = []
    for m in project.media:
        if not m.has_video:
            continue
        sig = storage.load_signals(project.id, m.id) or {}
        per.append({"media_id": m.id, "filename": m.filename, "images": len(sample_instants(m, sig)),
                    "done": bool((sig.get("vision") or {}).get("observations"))})
    todo = [p for p in per if not p["done"]]
    return {"media": per, "images": sum(p["images"] for p in todo), "pending": len(todo),
            "claude": config.claude_available()}


def observe_media(project: Project, m: Media, ctx=None, lo: float = 0.0, hi: float = 1.0) -> dict:
    """Envoie les images d'un rush à Claude et enregistre ses observations avec les signaux du rush."""
    import anthropic
    sig = storage.load_signals(project.id, m.id) or {}
    instants = sample_instants(m, sig)
    content: list = []
    sent: list[float] = []
    for k, t in enumerate(instants):
        if ctx:
            ctx.check()
        data = jpeg(m.path, t)
        if not data:
            continue
        content.append({"type": "text", "text": f"Image {len(sent)} — {t:.1f} s"})
        content.append(image_block(data))
        sent.append(t)
        if ctx and k % 6 == 0:
            ctx.step(f"{m.filename} : extraction des images ({k + 1}/{len(instants)})", lo + (hi - lo) * 0.3 * k / max(1, len(instants)))
    if not sent:
        raise RuntimeError(f"Aucune image lisible dans {m.filename}.")
    content.append({"type": "text", "text": f"Rush « {m.filename} », {m.duration:.0f} s. Décris chaque image, puis résume le rush."})
    if ctx:
        ctx.step(f"{m.filename} : Claude regarde {len(sent)} images", lo + (hi - lo) * 0.35)
    r = anthropic.Anthropic().beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=8000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if r.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Analyse des images interrompue ({r.stop_reason}).")
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    obs = []
    for o in data.get("observations", []):
        k = o.get("index")
        if isinstance(k, int) and 0 <= k < len(sent) and (o.get("vu") or "").strip():
            obs.append({"t": sent[k], "vu": o["vu"].strip()[:300], "type": o.get("type", "autre"),
                        "texte": (o.get("texte") or "").strip()[:200]})
    out = {"model": getattr(r, "model", config.CLAUDE_MODEL), "at": time.time(), "images": len(sent),
           "resume": (data.get("resume") or "").strip()[:600], "observations": sorted(obs, key=lambda o: o["t"])}
    sig = storage.load_signals(project.id, m.id) or {}  # relu : l'analyse a pu écrire entre-temps
    sig["vision"] = out
    storage.save_signals(project.id, m.id, sig)
    if ctx:
        ctx.step(f"{m.filename} : {len(obs)} images décrites", hi)
    return out


def observe_project(project_id: str, ctx, media_ids: list[str] | None = None, force: bool = False) -> dict:
    """Tâche « vision » : fait regarder par Claude les rushes pas encore vus (ou ceux demandés)."""
    if not config.claude_available():
        raise RuntimeError("Regarder les images demande une clé Claude (Réglages).")
    project = storage.load_project(project_id)
    todo = [m for m in project.media if m.has_video and (not media_ids or m.id in media_ids)]
    if not force:
        todo = [m for m in todo if not ((storage.load_signals(project_id, m.id) or {}).get("vision") or {}).get("observations")]
    if not todo:
        ctx.step("Tous les rushes ont déjà été regardés", 1.0)
        return {"media": 0, "images": 0, "observations": 0}
    images = n_obs = 0
    for i, m in enumerate(todo):
        ctx.check()
        out = observe_media(project, m, ctx, i / len(todo), (i + 1) / len(todo))
        images += out["images"]
        n_obs += len(out["observations"])
    ctx.step(f"{len(todo)} rush(es) regardé(s) : {n_obs} observations", 1.0)
    return {"media": len(todo), "images": images, "observations": n_obs}


def notes_as_instructions(project: Project, max_chars: int = 1600) -> str:
    """Ce que Claude a vu, en consigne pour le montage automatique (moments marquants d'abord)."""
    lines: list[str] = []
    for m in project.media:
        v = (storage.load_signals(project.id, m.id) or {}).get("vision") or {}
        if not v.get("observations"):
            continue
        if v.get("resume"):
            lines.append(f"{m.filename} : {v['resume']}")
        strong = [o for o in v["observations"] if o["type"] in ("elimination", "victoire", "defaite", "degats_recus")]
        for o in strong[:12]:
            lines.append(f"- {m.filename} à {o['t']:.1f} s [{o['type']}] {o['vu']}" + (f" « {o['texte']} »" if o["texte"] else ""))
    if not lines:
        return ""
    text = "Ce qu'on voit dans les images (analyse visuelle, à respecter) :\n" + "\n".join(lines)
    return text[:max_chars]
