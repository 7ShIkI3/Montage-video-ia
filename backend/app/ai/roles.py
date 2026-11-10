"""Le réalisateur en rôles nommés : stratège, directeur créatif, storyboardeur, monteur, chargé de publication.

Chaque rôle produit une étape courte et lisible, avec ses vérifications. On voit ainsi ce que l'IA a décidé, à
quelle étape, et ce qui ne colle pas (par exemple une accroche prévue qui n'arrive pas dans les 3 premières
secondes). Le stratège et le directeur créatif travaillent avant le montage : leur brief guide le réalisateur.
Les trois suivants décrivent et contrôlent le résultat.
"""
from __future__ import annotations

import json
import logging

from .. import config
from ..models import EditSettings, Project, RoleStep, Segment, Timeline
from . import quality

log = logging.getLogger(__name__)

PLATFORM_OF = {"youtube": "YouTube", "short": "TikTok, Reels et Shorts", "vlog": "YouTube", "podcast": "YouTube et podcast",
               "pvp": "YouTube et TikTok"}
AUDIENCE_OF = {"youtube": "abonnés et nouveaux spectateurs qui découvrent la chaîne",
               "short": "spectateurs qui font défiler et décident en 2 secondes",
               "vlog": "abonnés qui suivent le quotidien", "podcast": "auditeurs qui veulent le fond",
               "pvp": "joueurs qui veulent de l'action"}
TONE_OF = {"youtube": "naturel et rythmé", "short": "énergique", "vlog": "chaleureux", "podcast": "posé", "pvp": "épique"}


def _words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def _target(settings: EditSettings, total: float) -> float:
    if settings.target_duration:
        return float(settings.target_duration)
    t = 30.0 if settings.style == "short" else min(600.0, max(45.0, total * 0.45))
    return min(t, total) if total > 0 else t


# ---------------------------------------------------------------- avant le montage

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "objectif": {"type": "string"}, "public": {"type": "string"}, "plateforme": {"type": "string"},
        "promesse": {"type": "string"}, "angle": {"type": "string"}, "ton": {"type": "string"},
        "accroche": {"type": "string"}, "moment_fort": {"type": "string"}, "fin": {"type": "string"},
    },
    "required": ["objectif", "public", "plateforme", "promesse", "angle", "ton", "accroche", "moment_fort", "fin"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = """Tu joues deux rôles avant un montage vidéo, l'un après l'autre.
1. Stratège : l'objectif de la vidéo, son public, la plateforme, et la promesse faite au spectateur (honnête : \
rien que la vidéo ne tient pas).
2. Directeur créatif : l'angle, le ton, l'accroche des 3 premières secondes (cite la phrase ou le moment à \
utiliser), le moment fort à préparer et appuyer, et la fin (comment la vidéo se termine).
Chaque champ : une phrase courte, en français, concrète, appuyée sur les rushes fournis."""


def _local_brief(project: Project, segments: list[Segment], settings: EditSettings) -> dict:
    total = sum(m.duration for m in project.media)
    speech = [s for s in segments if s.kind == "speech" and s.text]
    best = max(segments, key=lambda s: s.score, default=None)
    hook = max(speech[: max(3, len(speech) // 3)], key=lambda s: s.score, default=None) if speech else best
    last = speech[-1] if speech else None
    target = _target(settings, total)
    sty = settings.style
    return {
        "objectif": f"Garder l'essentiel de {total / 60:.0f} min de rushes en une vidéo de {target:.0f} s."
                    if total >= 120 else f"Garder l'essentiel de {total:.0f} s de rushes, sans temps mort.",
        "public": AUDIENCE_OF.get(sty, "spectateurs curieux"),
        "plateforme": PLATFORM_OF.get(sty, "YouTube") + (f", format {settings.format}" if settings.format else ""),
        "promesse": f"« {_words(hook.text, 10)} »" if hook and hook.text else "Le meilleur des rushes, sans temps mort.",
        "angle": "le moment fort d'abord, puis le contexte" if sty == "short" else "le fil de l'histoire, sans temps mort",
        "ton": TONE_OF.get(sty, "naturel"),
        "accroche": (f"« {_words(hook.text, 8)} »" if hook and hook.text else "le plan le plus fort") + " dans les 3 premières secondes",
        "moment_fort": (f"« {_words(best.text, 8)} »" if best and best.text else "le plan le plus fort")
                       + ", préparé par une courte pause et appuyé (zoom, bruitage)",
        "fin": f"finir sur « {_words(last.text, 8)} »" if last else "finir sur une image forte, sans slogan creux",
    }


def _pvp_brief(project: Project, settings: EditSettings) -> dict:
    """Brief d'un edit PvP : pas de parole à suivre, le montage se joue sur les combos et le tempo."""
    total = sum(m.duration for m in project.media)
    return {
        "objectif": f"Tirer de {total / 60:.0f} min de combats un edit court fait des meilleurs combos du joueur.",
        "public": AUDIENCE_OF.get("pvp", "joueurs PvP"),
        "plateforme": PLATFORM_OF.get("pvp", "YouTube") + (f", format {settings.format}" if settings.format else ""),
        "promesse": "Seulement des combos portés par le joueur, sans menu, sans lobby, sans temps mort.",
        "angle": "les rafales de coups les plus nettes, choisies sur les images et pas seulement au son",
        "ton": TONE_OF.get("pvp", "nerveux, calé sur la musique"),
        "accroche": "un combo fort dès la première seconde",
        "moment_fort": "le meilleur combo, ralenti sur son coup final",
        "fin": "le dernier coup final, coupé juste après l'impact",
    }


def brief(project: Project, segments: list[Segment], settings: EditSettings) -> tuple[dict, str]:
    """Brief du stratège et du directeur créatif. Renvoie (brief, auteur : « claude » ou « local »)."""
    if settings.style == "pvp":  # les segments notés ici sont de la parole : ils égareraient le brief
        return _pvp_brief(project, settings), "local"
    if config.claude_available() and segments:
        try:
            import anthropic
            client = anthropic.Anthropic()
            rows = "\n".join(f"- {s.start:.1f}-{s.end:.1f} s (score {s.score:.2f}, {s.kind}) {s.text[:140]}"
                             for s in sorted(segments, key=lambda s: -s.score)[:60])
            text = (f"Style demandé : {settings.style}, format {settings.format}, durée cible "
                    f"{settings.target_duration or 'automatique'}.\nConsignes : {settings.instructions or '(aucune)'}\n\n"
                    f"Meilleurs moments des rushes :\n{rows}")
            r = client.beta.messages.create(
                model=config.CLAUDE_MODEL, max_tokens=2000, system=BRIEF_SYSTEM,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
                messages=[{"role": "user", "content": text}])
            if r.stop_reason not in ("refusal", "max_tokens"):
                return json.loads(next(b.text for b in r.content if b.type == "text")), "claude"
        except Exception as e:
            log.warning("Brief Claude indisponible, brief local : %s", e)
    return _local_brief(project, segments, settings), "local"


def brief_instructions(b: dict) -> str:
    """Le brief, en consigne pour le réalisateur."""
    return (f"Brief — objectif : {b['objectif']} Public : {b['public']}. Promesse : {b['promesse']}. "
            f"Angle : {b['angle']}. Ton : {b['ton']}. Accroche : {b['accroche']}. Moment fort : {b['moment_fort']}. "
            f"Fin : {b['fin']}.")


def before_steps(b: dict, by: str) -> list[RoleStep]:
    return [
        RoleStep(role="stratege", title="Stratège", by=by, items=[
            f"Objectif : {b['objectif']}", f"Public : {b['public']}", f"Plateforme : {b['plateforme']}",
            f"Promesse : {b['promesse']}"]),
        RoleStep(role="creatif", title="Directeur créatif", by=by, items=[
            f"Angle : {b['angle']}", f"Ton : {b['ton']}", f"Accroche : {b['accroche']}",
            f"Moment fort : {b['moment_fort']}", f"Fin : {b['fin']}"]),
    ]


# ---------------------------------------------------------------- après le montage

def storyboard(tl: Timeline, segments: list[Segment]) -> list[dict]:
    """Découpage en scènes du montage : accroche, développement, moment fort, fin."""
    by_id = {s.id: s for s in segments}
    starts = quality._starts(tl)
    best = quality.strongest(tl)
    total = tl.duration
    scenes: list[dict] = []
    for i, c in enumerate(tl.clips):
        t0, t1 = starts[i], starts[i] + c.duration
        if i == 0:
            kind = "accroche"
        elif best and i == best[0]:
            kind = "moment fort"
        elif i == len(tl.clips) - 1 and len(tl.clips) > 2:
            kind = "fin"
        else:
            kind = "développement"
        seg = by_id.get(c.segment_id or "")
        what = _words(seg.text, 9) if seg and seg.text else (c.reason or "plan sans parole")
        extras = [x.text for x in tl.texts if t0 <= x.start < t1][:1]
        sounds = [s.kind for s in tl.sfx if t0 <= s.start < t1][:2]
        if scenes and scenes[-1]["kind"] == kind == "développement" and t1 - scenes[-1]["t0"] < 25:
            scenes[-1]["t1"] = t1
            continue
        scenes.append({"kind": kind, "t0": round(t0, 2), "t1": round(min(total, t1), 2), "what": what,
                       "text": extras[0] if extras else "", "sound": ", ".join(sounds)})
    return scenes


def after_steps(project: Project, tl: Timeline, segments: list[Segment], settings: EditSettings,
                b: dict) -> list[RoleStep]:
    from . import publish
    scenes = storyboard(tl, segments)
    total_src = sum(m.duration for m in project.media)
    target = _target(settings, total_src)
    sb_checks = []
    hook = next((s for s in scenes if s["kind"] == "accroche"), None)
    if hook and hook["t1"] > 3.5 and tl.clips and tl.clips[0].duration > 3.5:
        sb_checks.append("l'accroche dure plus de 3 s : le spectateur doit comprendre plus tôt où on va")
    if not any(s["kind"] == "moment fort" for s in scenes) and len(tl.clips) > 2:
        sb_checks.append("le moment fort est en ouverture : il n'est plus préparé")
    steps = [RoleStep(role="storyboard", title="Storyboard", by="local", ok=not sb_checks, items=[
        f"{s['t0']:.0f}-{s['t1']:.0f} s · {s['kind']} : {s['what']}" + (f" · texte « {s['text']} »" if s["text"] else "")
        + (f" · {s['sound']}" if s["sound"] else "") for s in scenes[:14]], notes=sb_checks)]

    dur = tl.duration
    fx = sum(len(c.effects) for c in tl.clips) + sum(c.transition != "aucune" for c in tl.clips)
    m_checks = []
    if target and abs(dur - target) > 0.25 * target:
        m_checks.append(f"durée {dur:.0f} s pour une cible de {target:.0f} s")
    q = tl.quality.score if tl.quality else None
    steps.append(RoleStep(role="montage", title="Montage", by=tl.director or "local", ok=not m_checks, items=[
        f"{len(tl.clips)} plans, {dur:.0f} s (cible {target:.0f} s)",
        f"effets et transitions : {fx} · textes : {len(tl.texts)} · bruitages : {len(tl.sfx)} · plans de coupe : "
        f"{len(tl.overlays)}",
        f"musique : {'oui' if tl.music.track else 'non'}, sous-titres : {tl.captions}"]
        + ([f"contrôle qualité : {q}/100"] if q is not None else [])
        + ([f"relecteur : {tl.review.score:.1f}/10"] if tl.review else []), notes=m_checks))

    kit = publish.heuristic_kit(project, tl, segments)
    p_checks = []
    from . import claims
    for t in kit["titles"][:3]:
        if claims.overclaims(t):
            p_checks.append(f"titre « {t} » à adoucir")
    steps.append(RoleStep(role="publication", title="Publication", by="local", ok=not p_checks, items=[
        f"Titre : {kit['titles'][0]}" if kit["titles"] else "Titre : (à écrire)",
        f"Miniature : « {kit['thumbnail_text']} »", f"Hashtags : {' '.join(kit['hashtags'][:6]) or '(aucun)'}",
        f"Promesse tenue ? {b.get('promesse', '')}"], notes=p_checks))
    return steps
