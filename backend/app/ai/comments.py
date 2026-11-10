"""Commentaires de montage : le monteur pose des notes sur des moments précis, l'IA les applique.

Avec Claude, tous les commentaires ouverts partent dans une seule consigne (avec leur position,
le plan concerné et les paroles autour). Sans clé, un interprète local comprend les demandes
courantes et les applique au plan visé : couper, raccourcir, ralentir, zoomer, ajouter un texte ou
un bruitage, un effet, une transition, déplacer le plan au début…
"""
from __future__ import annotations

import re

from ..models import Clip, Comment, EditSettings, Mask, Segment, SfxItem, TextItem, Timeline
from ..render import effects as fx

NEG = ("sans", "pas de", "enlève l", "enleve l", "retire l", "supprime l")


def locate(tl: Timeline, c: Comment) -> tuple[int, float] | None:
    """Index du plan qui contient le moment commenté, et sa position sur la timeline."""
    if c.media_id is None:
        return None
    t = 0.0
    for i, clip in enumerate(tl.clips):
        if clip.media_id == c.media_id and clip.src_in - 1e-3 <= c.src_t < clip.src_out + 1e-3:
            return i, t + clip.src_to_local(c.src_t - clip.src_in)
        t += clip.duration
    return None


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02}"


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


VIDEO_WORDS = {
    "noir et blanc": "noir_et_blanc", "sépia": "sepia", "sepia": "sepia", "vintage": "vintage",
    "glitch": "glitch", "tremblement": "tremblement", "secoue": "tremblement", "grain": "grain",
    "vignette": "vignette", "flou": "flou", "miroir": "miroir", "cinéma": "cinema", "cinema": "cinema",
    "couleurs vives": "vif", "plus lumineux": "lumineux", "trop sombre": "lumineux", "contraste": "contraste",
    "zoom lent": "zoom_lent", "ken burns": "zoom_lent", "pulsation": "pulsation",
}
AUDIO_WORDS = {
    "écho": "echo", "echo": "echo", "robot": "robot", "radio": "radio", "téléphone": "telephone",
    "telephone": "telephone", "voix grave": "voix_grave", "voix aiguë": "voix_aigue", "voix aigue": "voix_aigue",
    "réverb": "salle", "reverb": "salle", "bruit de fond": "anti_bruit", "souffle": "anti_bruit",
    "voix claire": "voix_claire",
}
TRANSITION_WORDS = {
    "fondu blanc": "fondu_blanc", "fondu": "fondu_noir", "flash": "flash", "glissement": "glissement",
    "glisse": "glissement", "transition glitch": "glitch", "zoom d'entrée": "zoom_avant",
    "transition": "fondu_noir",
}


QUOTE = r"[«\"“]\s*([^»\"”]+?)\s*[»\"”]"


def face_at(faces: dict | None, t: float) -> float | None:
    if not faces or not faces.get("cx"):
        return None
    i = min(len(faces["cx"]) - 1, max(0, int(round(t / faces["hop"]))))
    return faces["cx"][i]


def apply_local(tl: Timeline, c: Comment, segments: list[Segment], media_duration: dict[str, float],
                phase: str = "all", faces: dict[str, dict] | None = None) -> str | None:
    """Applique un commentaire au plan visé. Renvoie ce qui a été fait, ou None si rien n'est compris.

    phase « edit » : coupes, durée, ordre, vitesse, effets ; phase « add » : textes et bruitages, posés
    une fois la timeline stabilisée pour qu'ils tombent au bon endroit."""
    quoted = re.findall(QUOTE, c.text)
    # Le texte entre guillemets est du contenu (« Trop fort ! »), pas une consigne.
    text = re.sub(QUOTE, " ", c.text).lower().strip()
    hit = locate(tl, c)
    done: list[str] = []
    neg = _has(text, *NEG)

    # Éléments posés à l'instant du commentaire (même hors plan) : texte, bruitage.
    t_at = hit[1] if hit and phase != "edit" else None
    if t_at is not None and quoted and _has(text, "texte", "titre", "écri", "ecri", "affiche", "bandeau", "mets", "ajoute"):
        style = "bandeau" if "bandeau" in text else "gros_texte" if _has(text, "gros", "énorme", "enorme") else \
            "legende" if "légende" in text or "legende" in text else "emoji" if len(quoted[0]) <= 2 else "titre"
        tl.texts.append(TextItem(start=max(0.0, t_at - 0.1), duration=2.5, text=quoted[0], style=style,
                                 position="bas" if style in ("bandeau", "legende") else "centre"))
        done.append(f"texte « {quoted[0]} » ajouté à {_fmt(t_at)}")
    if t_at is not None and not neg:
        kinds = [k for k in fx.SFX if k.replace("_", " ") in text or k.split("_")[0] in text]
        if not kinds and _has(text, "bruitage", "effet sonore", "son de"):
            kinds = ["whoosh"]
        for k in kinds[:1]:
            tl.sfx.append(SfxItem(start=max(0.0, t_at - 0.1), kind=k, volume=0.7))
            done.append(f"bruitage {k.replace('_', ' ')} à {_fmt(t_at)}")

    if hit is None or phase == "add":
        return "; ".join(done) or None
    i, t_tl = hit
    clip = tl.clips[i]

    # Zone sélectionnée sur l'image : flou, cache, cadre, zoom ou cadrage sur l'élément choisi.
    if c.region is not None:
        r = c.region.clamp()
        cx, cy = r.x + r.w / 2, r.y + r.h / 2
        region_done = []
        kind = None
        if _has(text, "pixel"):
            kind = "pixel"
        elif _has(text, "noir", "censure", "cache complètement", "masque en noir"):
            kind = "noir"
        elif _has(text, "flou", "floute", "cache", "masque", "anonym", "plaque", "logo", "visage"):
            kind = "flou"
        elif _has(text, "entoure", "cercle", "encadre", "surligne", "montre", "souligne", "flèche", "fleche", "met en avant", "mets en avant"):
            kind = "cadre"
        if kind:
            clip.masks = [m for m in clip.masks if not (abs(m.x - r.x) < 0.02 and abs(m.y - r.y) < 0.02)]
            if not neg:
                # Un visage suivi dans la zone : la zone le suit pendant tout le plan.
                fc = face_at((faces or {}).get(clip.media_id), c.src_t)
                follow = fc is not None and r.x - 0.05 <= fc <= r.x + r.w + 0.05
                clip.masks.append(Mask(x=r.x, y=r.y, w=r.w, h=r.h, kind=kind,
                                       follow_dx=round(r.x - fc, 4) if follow else None))
                if follow:
                    region_done.append("elle suit le visage")
            label = {"flou": "zone floutée", "pixel": "zone pixelisée", "noir": "zone cachée en noir",
                     "cadre": "zone entourée en jaune"}[kind]
            region_done.insert(0, ("zone libérée" if neg else label) + " sur ce plan")
        if _has(text, "zoom", "rapproche", "gros plan", "plus près", "plus pres"):
            clip.zoom = 1.0 if neg else round(min(2.5, max(1.15, 0.75 / max(r.w, r.h))), 2)
            clip.focus_x, clip.focus_y = (None, None) if neg else (round(cx, 4), round(cy, 4))
            region_done.append("zoom retiré" if neg else f"zoom ×{clip.zoom} sur la zone")
        elif _has(text, "suis", "suivre", "cadre sur", "centre", "recadre", "garde-le", "garde la", "focus"):
            clip.focus_x, clip.focus_y = round(cx, 4), round(cy, 4)
            region_done.append("cadrage centré sur la zone")
        if region_done:
            return "; ".join(done + region_done)

    # Supprimer / couper
    removal = _has(text, "supprime", "enlève", "enleve", "retire", "vire", "inutile", "pas intéressant",
                   "pas interessant", "à jeter", "a jeter", "nul") and not quoted
    removal = removal or (_has(text, "coupe") and _has(text, "passage", "ce plan", "ça", "ca ", "ce moment", "tout"))
    targets_detail = list(VIDEO_WORDS) + list(AUDIO_WORDS) + list(TRANSITION_WORDS) + list(fx.SFX) + [
        "musique", " son", "zoom", "ralenti", "accél", "accel", "texte", "titre", "bruitage", "sous-titre"]
    if removal and not any(w in text for w in targets_detail):
        tl.clips.pop(i)
        tl.rejected.insert(0, clip.model_copy(update={"reason": f"Retiré suite à ton commentaire : « {c.text} »"}))
        return "; ".join(done + [f"plan de {_fmt(t_tl)} retiré du montage"])
    if _has(text, "coupe ici", "coupe à", "coupe a ", "sépare", "separe", "scinde"):
        if c.src_t - clip.src_in > 0.1 and clip.src_out - c.src_t > 0.1:
            b = clip.model_copy(update={"id": Clip(media_id=clip.media_id, src_in=0, src_out=1).id,
                                        "src_in": c.src_t, "transition": "aucune"})
            clip.src_out = c.src_t
            tl.clips.insert(i + 1, b)
            done.append(f"plan coupé en deux à {_fmt(t_tl)}")

    # Durée du plan
    if _has(text, "raccourci", "plus court", "trop long", "trop lent", "abrège", "abrege"):
        new_out = max(c.src_t + 0.5, clip.src_in + 0.7 * clip.source_duration)
        if new_out < clip.src_out - 0.1:
            done.append(f"plan raccourci de {clip.src_out - new_out:.1f} s")
            clip.src_out = round(new_out, 3)
    if _has(text, "rallonge", "plus long", "trop court", "coupé trop tôt", "coupe trop tot", "laisse finir", "trop tôt"):
        new_out = min(media_duration.get(clip.media_id, clip.src_out + 1.5), clip.src_out + 1.5)
        if new_out > clip.src_out + 0.05:
            done.append(f"plan rallongé de {new_out - clip.src_out:.1f} s")
            clip.src_out = round(new_out, 3)
    if _has(text, "commence trop tôt", "démarre trop tôt", "trop de blanc au début"):
        clip.src_in = round(min(clip.src_out - 0.5, clip.src_in + 0.7), 3)
        done.append("début du plan resserré")

    # Ordre
    if _has(text, "en premier", "au début", "commence par", "accroche", "ouvre avec", "intro"):
        tl.clips.insert(0, tl.clips.pop(i))
        done.append("plan placé en ouverture")
    elif _has(text, "à la fin", "a la fin", "en dernier", "pour finir", "conclusion"):
        tl.clips.append(tl.clips.pop(i))
        done.append("plan placé à la fin")

    # Vitesse, cadrage, son
    if _has(text, "rampe", "ralenti progressif", "ralentit progressivement", "speed ramp"):
        if neg:
            clip.speed, clip.speed_end = 1.0, None
        else:
            clip.speed, clip.speed_end = 1.0, 0.4
        done.append("rampe de vitesse " + ("retirée" if neg else "×1 → ×0,4"))
    elif _has(text, "ralenti", "slow"):
        clip.speed = 1.0 if neg else 0.5
        done.append("ralenti " + ("retiré" if neg else "×0,5"))
    elif _has(text, "accélère", "accelere", "accéléré", "accelere", "plus vite", "x2"):
        clip.speed = 1.0 if neg else 2.0
        done.append("accéléré " + ("retiré" if neg else "×2"))
    elif "vitesse normale" in text:
        clip.speed = 1.0
        done.append("vitesse normale")
    if _has(text, "zoom progressif", "zoom avant progressif", "zoome progressivement", "zoom qui avance"):
        from ..models import Keyframe
        d = clip.duration
        clip.keyframes = [] if neg else [Keyframe(t=0, zoom=1.0), Keyframe(t=round(d, 3), zoom=1.35,
                                                                       x=clip.focus_x or 0.5, y=clip.focus_y or 0.45)]
        done.append("zoom progressif " + ("retiré" if neg else "ajouté (images clés)"))
    elif "zoom" in text and "zoom lent" not in text and "zoom d'entrée" not in text:
        clip.zoom = 1.0 if neg or "dézoom" in text or "dezoom" in text else 1.2
        done.append("zoom " + ("retiré" if clip.zoom == 1.0 else "ajouté"))
    if _has(text, "plus fort", "monte le son", "on entend pas", "on n'entend pas", "trop bas"):
        clip.volume = round(min(2.0, clip.volume * 1.4), 2)
        done.append(f"volume à {int(clip.volume * 100)} %")
    elif _has(text, "moins fort", "trop fort", "baisse le son"):
        clip.volume = round(clip.volume * 0.7, 2)
        done.append(f"volume à {int(clip.volume * 100)} %")
    if _has(text, "sans son", "muet", "coupe le son"):
        clip.audio_effects = list(dict.fromkeys(clip.audio_effects + ["muet"]))
        done.append("son coupé")

    # Effets, voix, transitions
    for w, e in VIDEO_WORDS.items():
        if w in text:
            clip.effects = [x for x in clip.effects if x != e] + ([] if neg else [e])
            done.append(f"effet {w} {'retiré' if neg else 'ajouté'}")
            break
    for w, e in AUDIO_WORDS.items():
        if w in text:
            clip.audio_effects = [x for x in clip.audio_effects if x != e] + ([] if neg else [e])
            done.append(f"voix : {w} {'retiré' if neg else 'ajouté'}")
            break
    for w, e in TRANSITION_WORDS.items():
        if w in text and ("transition" in text or w in ("fondu", "fondu blanc", "glissement", "glisse")):
            clip.transition = "aucune" if neg else e
            done.append(f"transition {'retirée' if neg else w}")
            break

    if _has(text, "garde", "top", "parfait", "génial", "genial", "j'adore", "super moment", "important"):
        clip.score = min(1.0, clip.score + 0.25)
        clip.reason = f"Validé par toi : « {c.text} ». " + clip.reason
        done.append("passage marqué comme important (gardé en priorité)")
    return "; ".join(done) or None


def context_line(tl: Timeline, c: Comment, segments: list[Segment], n: int) -> str:
    """Description d'un commentaire pour Claude : où il est, sur quel plan, ce qui s'y dit."""
    if c.media_id is None:
        return f"{n}. Commentaire général : « {c.text} »"
    zone = ""
    if c.region is not None:
        r = c.region
        zone = (f" [zone sélectionnée sur l'image : x {r.x:.2f}-{r.x + r.w:.2f}, y {r.y:.2f}-{r.y + r.h:.2f} "
                f"(fractions de la largeur et de la hauteur), visible en jaune sur l'image n°{n}]")
    hit = locate(tl, c)
    seg = next((s for s in segments if s.media_id == c.media_id and s.start <= c.src_t < s.end), None)
    where = (f"à {_fmt(hit[1])} de la timeline, plan n°{hit[0] + 1}" if hit else "sur un passage absent du montage")
    said = f", segment {seg.id} « {seg.text[:160]} »" if seg else ""
    return f"{n}. {where} (rush {c.media_id} à {c.src_t:.2f} s{said}){zone} : « {c.text} »"


def apply_all(tl: Timeline, segments: list[Segment], settings: EditSettings, media_duration: dict[str, float],
              comments: list[Comment] | None = None, faces: dict[str, dict] | None = None) -> list[str]:
    """Interprète local : applique chaque commentaire ouvert, met à jour statut et réponse."""
    msgs = []
    todo = comments if comments is not None else [x for x in tl.comments if x.status == "ouvert"]
    edits = {c.id: apply_local(tl, c, segments, media_duration, "edit", faces) for c in todo}
    adds = {c.id: apply_local(tl, c, segments, media_duration, "add") for c in todo}
    for c in todo:
        r = "; ".join(x for x in (edits[c.id], adds[c.id]) if x)
        if r:
            c.status, c.reply = "applique", r[:1].upper() + r[1:] + "."
            msgs.append(r)
        else:
            c.status = "incompris"
            c.reply = ("Je n'ai pas compris ce commentaire sans Claude. Essaie par exemple : « coupe ce passage », "
                       "« raccourcis », « ralenti », « zoom », « texte \"Wow\" », « whoosh », « noir et blanc », "
                       "« fondu », « mets ce plan au début », « garde ce passage ».")
    return msgs


REGION_ACTIONS = ("flou", "floute", "pixel", "cache", "masque", "anonym", "noir", "censure", "entoure", "cercle",
                  "encadre", "surligne", "montre", "zoom", "rapproche", "gros plan", "suis", "cadre sur", "centre",
                  "recadre", "focus", "plaque", "logo", "visage")


def is_region_action(c: Comment) -> bool:
    return c.region is not None and _has(re.sub(QUOTE, " ", c.text).lower(), *REGION_ACTIONS)


def carry_overrides(old: Timeline, new: Timeline) -> None:
    """Après une réécriture du montage par Claude, on garde les réglages faits à la main sur les mêmes passages
    des rushes : zones (flous, cadres, cadrages) et correction des couleurs."""
    for n in new.clips:
        for o in old.clips:
            if o.media_id != n.media_id or not (o.masks or o.focus_x is not None or o.color is not None):
                continue
            overlap = min(o.src_out, n.src_out) - max(o.src_in, n.src_in)
            if overlap > 0.5 * min(o.source_duration, n.source_duration):
                if not (n.masks or n.focus_x is not None):
                    n.masks = [m.model_copy() for m in o.masks]
                    if o.focus_x is not None:
                        n.focus_x, n.focus_y, n.zoom = o.focus_x, o.focus_y, o.zoom
                if n.color is None and o.color is not None:
                    n.color = o.color.model_copy()
                break


def frame_with_region(path: str, src_t: float, region) -> bytes:
    """Image du rush à l'instant commenté, zone sélectionnée entourée en jaune (pour Claude)."""
    import subprocess

    from ..media.ffmpeg import FFMPEG

    r = region.clamp()
    vf = (f"scale=768:-2,drawbox=x=iw*{r.x:.4f}:y=ih*{r.y:.4f}:w=iw*{r.w:.4f}:h=ih*{r.h:.4f}"
          ":color=yellow@1:t=4")
    out = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{max(0.0, src_t):.3f}", "-i", path, "-frames:v", "1",
                          "-vf", vf, "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"], capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("image introuvable")
    return out.stdout
