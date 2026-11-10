"""Catalogue des effets : looks, effets vidéo, transitions, effets audio et bruitages.

Chaque effet a un identifiant stable (utilisé par l'IA, la timeline et l'interface),
un libellé en français et sa traduction en filtres FFmpeg.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .. import config
from ..media.ffmpeg import FFMPEG

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Effets vidéo (par plan) et looks (sur toute la vidéo)
# ---------------------------------------------------------------------------

VIDEO_EFFECTS: dict[str, dict] = {
    "noir_et_blanc": {"label": "Noir et blanc", "group": "couleur"},
    "sepia": {"label": "Sépia", "group": "couleur"},
    "vintage": {"label": "Vintage", "group": "couleur"},
    "vif": {"label": "Couleurs vives", "group": "couleur"},
    "cinema": {"label": "Cinéma (bandes noires)", "group": "couleur"},
    "froid": {"label": "Tons froids", "group": "couleur"},
    "chaud": {"label": "Tons chauds", "group": "couleur"},
    "lumineux": {"label": "Plus lumineux", "group": "couleur"},
    "contraste": {"label": "Contraste fort", "group": "couleur"},
    "vignette": {"label": "Vignette", "group": "style"},
    "grain": {"label": "Grain film", "group": "style"},
    "nettete": {"label": "Netteté", "group": "style"},
    "flou": {"label": "Flou artistique", "group": "style"},
    "miroir": {"label": "Miroir", "group": "style"},
    "glitch": {"label": "Glitch", "group": "mouvement"},
    "tremblement": {"label": "Tremblement caméra", "group": "mouvement"},
    "zoom_lent": {"label": "Zoom lent (Ken Burns)", "group": "mouvement"},
    "pulsation": {"label": "Pulsation (zoom au rythme)", "group": "mouvement"},
    # Rendus par le moteur 3D (render/scene3d.py)
    "3d_orbite": {"label": "Orbite 3D (caméra qui tourne)", "group": "3d"},
    "3d_bascule": {"label": "Bascule 3D d'entrée", "group": "3d"},
    "3d_rotation": {"label": "Rotation 3D d'entrée", "group": "3d"},
    "3d_flottant": {"label": "Écran flottant 3D", "group": "3d"},
    # Détourage IA sans fond vert (analysis/segment.py, appliqué par le moteur 3D)
    "detourage_flou": {"label": "Fond flouté (détourage IA)", "group": "ia"},
    "detourage_studio": {"label": "Fond studio (détourage IA)", "group": "ia"},
    "detourage_noir": {"label": "Fond noir (détourage IA)", "group": "ia"},
    "detourage_blanc": {"label": "Fond blanc (détourage IA)", "group": "ia"},
    "detourage_image": {"label": "Fond image (détourage IA)", "group": "ia"},
}

LOOKS = ["aucun", "cinema", "vif", "vintage", "noir_et_blanc", "froid", "chaud", "sepia"]


def video_effect_filter(name: str, W: int, H: int, d: float, fps: int = 30) -> str | None:
    frames = max(1, int(d * fps))
    f = {
        "noir_et_blanc": "hue=s=0",
        "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
        "vintage": "curves=preset=vintage,noise=alls=7:allf=t",
        "vif": "eq=contrast=1.1:saturation=1.35",
        "cinema": (f"colorbalance=rs=-0.04:bs=0.07:rh=0.07:bh=-0.05,eq=contrast=1.08:saturation=1.08,"
                   f"drawbox=x=0:y=0:w=iw:h={int(H * 0.09)}:color=black:t=fill,"
                   f"drawbox=x=0:y={H - int(H * 0.09)}:w=iw:h={int(H * 0.09)}:color=black:t=fill"),
        "froid": "colorbalance=rs=-0.08:bs=0.1:rm=-0.04:bm=0.06",
        "chaud": "colorbalance=rs=0.1:bs=-0.08:rm=0.05:bm=-0.05",
        "lumineux": "eq=brightness=0.05:gamma=1.1",
        "contraste": "eq=contrast=1.3:saturation=1.1",
        "vignette": "vignette=PI/4.5",
        "grain": "noise=alls=14:allf=t+u",
        "nettete": "unsharp=5:5:0.8",
        "flou": "gblur=sigma=7",
        "miroir": "hflip",
        "glitch": ("rgbashift=rh=10:bh=-10:enable='lt(mod(t,1.1),0.12)',"
                   "noise=alls=40:allf=t:enable='lt(mod(t,1.1),0.12)'"),
        "tremblement": f"crop={W - 28}:{H - 28}:x='14+11*sin(t*57)':y='14+11*cos(t*81)',scale={W}:{H}",
        "zoom_lent": (f"zoompan=z='1+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d=1:s={W}x{H}:fps={fps}"),
        "pulsation": (f"zoompan=z='1.04+0.04*sin(2*PI*on/{fps / 2:.1f})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d=1:s={W}x{H}:fps={fps}"),
    }.get(name)
    return f


def color_filter(color) -> str | None:
    """Correction colorimétrique d'un plan (ColorAdjust) en filtres FFmpeg ; None si elle est neutre.

    L'aperçu (frontend/src/fxPreview.ts) utilise les mêmes coefficients : exposition = luminosité ajoutée
    0,2 par unité, contraste = 1 + 0,6 par unité, saturation = 1 + 1 par unité."""
    if color is None or color.neutral:
        return None
    out = []
    eq = []
    if color.exposure:
        eq.append(f"brightness={0.2 * color.exposure:.4f}")
    if color.contrast:
        eq.append(f"contrast={1 + 0.6 * color.contrast:.4f}")
    if color.saturation:
        eq.append(f"saturation={1 + color.saturation:.4f}")
    if eq:
        out.append("eq=" + ":".join(eq))
    if color.temperature:
        t = color.temperature  # chaud : plus de rouge, moins de bleu (et l'inverse pour le froid)
        out.append(f"colorbalance=rs={0.15 * t:.4f}:bs={-0.15 * t:.4f}:"
                   f"rm={0.15 * t:.4f}:bm={-0.15 * t:.4f}:rh={0.08 * t:.4f}:bh={-0.08 * t:.4f}")
    return ",".join(out)


# ---------------------------------------------------------------------------
# Transitions d'entrée (sans chevauchement : la durée de la timeline ne change pas)
# ---------------------------------------------------------------------------

TRANSITIONS: dict[str, str] = {
    "aucune": "Coupe franche",
    "fondu_noir": "Fondu au noir",
    "fondu_blanc": "Fondu au blanc",
    "flash": "Flash",
    "zoom_avant": "Zoom d'entrée",
    "glissement": "Glissement",
    "flou": "Flou d'entrée",
    "glitch": "Glitch",
    "cube_3d": "Cube 3D",
    "flip_3d": "Retournement 3D",
    "porte_3d": "Porte 3D",
}


def transition_filters(name: str, td: float, W: int, H: int, fps: int = 30) -> list[str]:
    td = max(0.1, min(td, 1.5))
    if name == "fondu_noir":
        return [f"fade=t=in:st=0:d={td:.2f}"]
    if name == "fondu_blanc":
        return [f"fade=t=in:st=0:d={td:.2f}:color=white"]
    if name == "flash":
        return [f"fade=t=in:st=0:d={min(td, 0.18):.2f}:color=white"]
    if name == "zoom_avant":
        fr = max(2, int(td * fps))
        return [f"zoompan=z='if(lt(on,{fr}),1.3-0.3*on/{fr},1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={W}x{H}:fps={fps}"]
    if name == "flou":
        return [f"gblur=sigma=24:enable='lt(t,{td / 2:.2f})'",
                f"gblur=sigma=9:enable='between(t,{td / 2:.2f},{td:.2f})'"]
    if name == "glitch":
        return [f"rgbashift=rh=14:bh=-14:gv=6:enable='lt(t,{td:.2f})'",
                f"noise=alls=50:allf=t:enable='lt(t,{td:.2f})'"]
    if name == "glissement":
        # L'image entre par la droite : on la place à droite d'un fond noir et la fenêtre glisse.
        return [f"pad=w={2 * W}:h={H}:x={W}:y=0:color=black",
                f"crop=w={W}:h={H}:x='if(lt(t,{td:.2f}),{W}*t/{td:.2f},{W})':y=0"]
    return []


def fades_out_before(next_transition: str) -> str | None:
    """Couleur du fondu de sortie à appliquer au plan précédent."""
    return {"fondu_noir": "black", "fondu_blanc": "white"}.get(next_transition)


# ---------------------------------------------------------------------------
# Effets audio (par plan)
# ---------------------------------------------------------------------------

AUDIO_EFFECTS: dict[str, dict] = {
    "voix_claire": {"label": "Voix claire (EQ + compression)",
                    "filter": "highpass=f=80,equalizer=f=3000:t=q:w=1:g=3,"
                              "acompressor=threshold=0.1:ratio=3:attack=10:release=150"},
    # Chaîne de studio : filtre les graves, enlève le bruit de fond, compresse doucement, ajoute de la présence.
    "voix_studio": {"label": "Voix studio (nettoyage + présence)",
                    "filter": "highpass=f=80,afftdn=nf=-25,acompressor=threshold=0.12:ratio=2.5:attack=15:release=200,"
                              "equalizer=f=4000:t=q:w=1:g=2.5"},
    "anti_bruit": {"label": "Réduction de bruit", "filter": "afftdn=nf=-25"},
    "echo": {"label": "Écho", "filter": "aecho=0.8:0.7:90:0.35"},
    "salle": {"label": "Réverbération", "filter": "aecho=0.8:0.6:30|47|75:0.3|0.25|0.2"},
    "radio": {"label": "Radio", "filter": "highpass=f=400,lowpass=f=3200,acompressor=threshold=0.2:ratio=4"},
    "telephone": {"label": "Téléphone", "filter": "highpass=f=500,lowpass=f=2500,volume=1.4"},
    "voix_grave": {"label": "Voix grave", "filter": "asetrate=48000*0.84,aresample=48000,atempo=1.19"},
    "voix_aigue": {"label": "Voix aiguë", "filter": "asetrate=48000*1.22,aresample=48000,atempo=0.82"},
    "robot": {"label": "Robot",
              "filter": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75"},
    "muet": {"label": "Couper le son", "filter": "volume=0"},
}


def atempo_chain(speed: float) -> str:
    """atempo n'accepte que 0.5..2 : on enchaîne pour les vitesses extrêmes."""
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Bruitages, synthétisés localement (aucun fichier sous droits)
# ---------------------------------------------------------------------------

SFX: dict[str, dict] = {
    "whoosh": {"label": "Whoosh", "src": "anoisesrc=d=0.7:c=pink:a=0.6",
               "post": "highpass=f=300,lowpass=f=5000,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.35"},
    "swoosh_court": {"label": "Swoosh court", "src": "anoisesrc=d=0.3:c=white:a=0.4",
                     "post": "highpass=f=1200,afade=t=in:d=0.12,afade=t=out:st=0.12:d=0.18"},
    "pop": {"label": "Pop", "src": "aevalsrc='0.8*sin(2*PI*(700-600*t)*t)*exp(-28*t)':d=0.18:s=48000"},
    "ding": {"label": "Ding", "src": "aevalsrc='0.45*(sin(2*PI*1320*t)+0.5*sin(2*PI*2640*t))*exp(-4*t)':d=1.2:s=48000"},
    "boom": {"label": "Boom", "src": "aevalsrc='0.95*sin(2*PI*(70-35*t)*t)*exp(-3.2*t)':d=1.3:s=48000",
             "post": "lowpass=f=300"},
    "riser": {"label": "Montée (riser)", "src": "aevalsrc='0.3*sin(2*PI*(200+700*t*t)*t)*(t/1.6)':d=1.6:s=48000"},
    "clic": {"label": "Clic", "src": "aevalsrc='0.9*sin(2*PI*2200*t)*exp(-90*t)':d=0.06:s=48000"},
    "notification": {"label": "Notification",
                     "src": "aevalsrc='0.4*sin(2*PI*988*t)*exp(-9*t)*lt(t,0.15)+0.4*sin(2*PI*1319*(t-0.15))"
                            "*exp(-7*(t-0.15))*gte(t,0.15)':d=0.7:s=48000"},
    "tambour": {"label": "Roulement de tambour",
                "src": "anoisesrc=d=1.2:c=brown:a=0.8",
                "post": "volume='0.5+0.5*sin(2*PI*22*t)':eval=frame,lowpass=f=900,afade=t=in:d=0.8"},
}


def sfx_dir() -> Path:
    d = config.DATA_DIR / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_sfx() -> dict[str, Path]:
    """Génère les bruitages manquants en WAV (une fois)."""
    out = {}
    for name, spec in SFX.items():
        path = sfx_dir() / f"{name}.wav"
        if not path.exists():
            filt = ["-af", f"aresample=48000,aformat=channel_layouts=stereo,{spec['post']}"] if spec.get("post") \
                else ["-af", "aresample=48000,aformat=channel_layouts=stereo"]
            r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", spec["src"],
                                *filt, str(path)], capture_output=True, text=True)
            if r.returncode != 0:
                log.warning("Bruitage %s non généré : %s", name, r.stderr[-300:])
                continue
        out[name] = path
    return out


def catalog() -> dict:
    from .scene3d import IMPACTS
    return {
        "video_effects": [{"id": k, **v} for k, v in VIDEO_EFFECTS.items()],
        "looks": [{"id": k, "label": "Aucun" if k == "aucun" else VIDEO_EFFECTS[k]["label"]} for k in LOOKS],
        "transitions": [{"id": k, "label": v} for k, v in TRANSITIONS.items()],
        "audio_effects": [{"id": k, "label": v["label"]} for k, v in AUDIO_EFFECTS.items()],
        "sfx": [{"id": k, "label": v["label"]} for k, v in SFX.items()],
        "text_styles": [
            {"id": "titre", "label": "Titre animé"}, {"id": "bandeau", "label": "Bandeau (bas d'écran)"},
            {"id": "legende", "label": "Légende"}, {"id": "gros_texte", "label": "Gros texte choc"},
            {"id": "emoji", "label": "Emoji"},
            {"id": "texte_3d", "label": "Texte 3D en relief"},
        ],
        "impacts": [{"id": k, "label": v["label"]} for k, v in IMPACTS.items()],
        "speeds": [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0],
    }
