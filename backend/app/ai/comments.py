"""Commentaires de montage : le monteur pose des notes sur des moments précis, l'IA les applique.

Avec Claude, tous les commentaires ouverts partent dans une seule consigne (avec leur position,
le plan concerné et les paroles autour). Sans clé, un interprète local comprend les demandes
courantes et les applique au plan visé : couper, raccourcir, ralentir, zoomer, ajouter un texte ou
un bruitage, un effet, une transition, déplacer le plan au début…
"""
from __future__ import annotations

import re

from ..models import Clip, Comment, EditSettings, Segment, SfxItem, TextItem, Timeline
from ..render import effects as fx

NEG = ("sans", "pas de", "enlève l", "enleve l", "retire l", "supprime l")


def locate(tl: Timeline, c: Comment) -> tuple[int, float] | None:
    """Index du plan qui contient le moment commenté, et sa position sur la timeline."""
    if c.media_id is None:
        return None
    t = 0.0
    for i, clip in enumerate(tl.clips):
        if clip.media_id == c.media_id and clip.src_in - 1e-3 <= c.src_t < clip.src_out + 1e-3:
            return i, t + (c.src_t - clip.src_in) / max(0.05, clip.speed)
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


def apply_local(tl: Timeline, c: Comment, segments: list[Segment], media_duration: dict[str, float],
                phase: str = "all") -> str | None:
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
    if _has(text, "ralenti", "slow"):
        clip.speed = 1.0 if neg else 0.5
        done.append("ralenti " + ("retiré" if neg else "×0,5"))
    elif _has(text, "accélère", "accelere", "accéléré", "accelere", "plus vite", "x2"):
        clip.speed = 1.0 if neg else 2.0
        done.append("accéléré " + ("retiré" if neg else "×2"))
    elif "vitesse normale" in text:
        clip.speed = 1.0
        done.append("vitesse normale")
    if "zoom" in text and "zoom lent" not in text and "zoom d'entrée" not in text:
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
    hit = locate(tl, c)
    seg = next((s for s in segments if s.media_id == c.media_id and s.start <= c.src_t < s.end), None)
    where = (f"à {_fmt(hit[1])} de la timeline, plan n°{hit[0] + 1}" if hit else "sur un passage absent du montage")
    said = f", segment {seg.id} « {seg.text[:160]} »" if seg else ""
    return f"{n}. {where} (rush {c.media_id} à {c.src_t:.2f} s{said}) : « {c.text} »"


def apply_all(tl: Timeline, segments: list[Segment], settings: EditSettings, media_duration: dict[str, float],
              comments: list[Comment] | None = None) -> list[str]:
    """Interprète local : applique chaque commentaire ouvert, met à jour statut et réponse."""
    msgs = []
    todo = comments if comments is not None else [x for x in tl.comments if x.status == "ouvert"]
    edits = {c.id: apply_local(tl, c, segments, media_duration, "edit") for c in todo}
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
