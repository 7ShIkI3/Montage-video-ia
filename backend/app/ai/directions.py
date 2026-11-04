"""Trois directions créatives avant de monter.

L'IA regarde ce que contiennent les rushes (parole, action, coups, durée) et propose trois façons différentes
de raconter la vidéo : rythme, ton, style et durée. L'utilisateur en choisit une, ou laisse celle recommandée,
et l'IA monte seule. Trois questions facultatives (plateforme, ton, public) orientent les propositions.
"""
from __future__ import annotations

import json
import logging

from .. import config, storage
from ..models import Direction, EditSettings, Project, Segment

log = logging.getLogger(__name__)

PLATFORMS = {"auto": "peu importe", "youtube": "YouTube", "tiktok": "TikTok / Reels / Shorts",
             "linkedin": "LinkedIn", "instagram": "Instagram"}
TONES = {"auto": "au choix de l'IA", "fun": "drôle et énergique", "pro": "sérieux et professionnel",
         "emotion": "émouvant", "pedago": "pédagogique"}

SCHEMA = {
    "type": "object",
    "properties": {"directions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"}, "pitch": {"type": "string"}, "rhythm": {"type": "string"},
            "tone": {"type": "string"},
            "style": {"type": "string", "enum": ["youtube", "short", "vlog", "podcast", "pvp"]},
            "format": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
            "target_duration": {"type": ["number", "null"]},
            "captions": {"type": "string", "enum": ["karaoke", "classique", "aucun"]},
            "music": {"type": "boolean"},
            "instructions": {"type": "string"},
            "recommended": {"type": "boolean"},
        },
        "required": ["name", "pitch", "rhythm", "tone", "style", "format", "target_duration", "captions", "music",
                     "instructions", "recommended"],
        "additionalProperties": False}}},
    "required": ["directions"], "additionalProperties": False,
}

SYSTEM = """Tu es directeur créatif vidéo. Avant le montage, tu proposes exactement 3 directions VRAIMENT \
différentes (pas trois variantes du même montage) pour ces rushes : par exemple un récit complet, un format court \
qui accroche, et une approche plus originale adaptée au contenu. Chaque direction dit en une phrase ce que la vidéo \
racontera (pitch), son rythme et son ton, et donne les réglages du montage. `instructions` est la consigne précise \
que le monteur IA suivra (ce qu'il garde, l'ordre, l'accroche, le moment fort). Recommande-en une seule \
(recommended=true), celle qui sert le mieux le contenu et la plateforme. Écris en français, sans jargon."""


def content_profile(project: Project, segments: list[Segment]) -> dict:
    total = sum(m.duration for m in project.media)
    speech = sum(s.duration for s in segments if s.kind == "speech")
    action = sum(s.duration for s in segments if s.kind == "action")
    hits = sum(len((storage.load_signals(project.id, m.id) or {}).get("hits") or []) for m in project.media)
    motion = [s.features.get("motion", 0) for s in segments]
    return {"total": total, "speech": speech, "action": action, "hits_per_min": hits / max(1.0, total / 60),
            "motion": sum(motion) / len(motion) if motion else 0.0, "n_media": len(project.media)}


def heuristic(project: Project, segments: list[Segment], platform: str = "auto", tone: str = "auto") -> list[Direction]:
    p = content_profile(project, segments)
    total, talky = p["total"], p["speech"] > 0.4 * max(1.0, p["total"])
    long_cut = int(min(600, max(45, round(total * 0.45 / 15) * 15)))
    tone_txt = TONES.get(tone, "")
    out = [
        Direction(name="Récit complet", pitch="Toute l'histoire, du début à la fin, sans temps morts.",
                  rhythm="soutenu", tone=tone_txt if tone != "auto" else "naturel",
                  settings={"style": "youtube", "format": "16:9", "target_duration": long_cut, "captions": "karaoke",
                            "music": False, "instructions": "Garde le fil de l'histoire, ouvre sur la promesse de la vidéo."}),
        Direction(name="Short qui accroche", pitch="Le meilleur moment d'abord, 30 secondes très rythmées.",
                  rhythm="très rapide", tone=tone_txt if tone != "auto" else "énergique",
                  settings={"style": "short", "format": "9:16", "target_duration": 30, "captions": "karaoke",
                            "music": True, "instructions": "Ouvre sur le moment le plus fort, une idée par plan."}),
    ]
    if p["hits_per_min"] > 25 and p["action"] > p["speech"]:
        third = Direction(name="Edit gaming", pitch="Les actions et les combos calés sur le tempo de la musique.",
                          rhythm="calé sur le beat", tone="épique",
                          settings={"style": "pvp", "format": "16:9", "target_duration": None, "captions": "aucun",
                                    "music": True, "instructions": ""})
    elif talky and total > 600:
        third = Direction(name="Discussion intégrale", pitch="Presque tout le fond, sans silences ni hésitations.",
                          rhythm="posé", tone="conversation",
                          settings={"style": "podcast", "format": "16:9", "target_duration": None, "captions": "classique",
                                    "music": False, "instructions": ""})
    else:
        third = Direction(name="Vlog d'ambiance", pitch="L'atmosphère d'abord : plans d'illustration et musique.",
                          rhythm="fluide", tone="détendu",
                          settings={"style": "vlog", "format": "16:9", "target_duration": int(min(300, max(45, total * 0.35))),
                                    "captions": "classique", "music": True,
                                    "instructions": "Intercale des plans d'illustration entre les phrases."})
    out.append(third)
    rec = 1 if platform in ("tiktok", "instagram") else 2 if third.settings["style"] == "pvp" else 0
    out[rec].recommended = True
    return out


def with_claude(project: Project, segments: list[Segment], platform: str, tone: str, audience: str) -> list[Direction]:
    import anthropic

    from .director import _segments_table
    p = content_profile(project, segments)
    user = (f"Rushes : {p['n_media']} fichier(s), {p['total']:.0f} s au total, dont {p['speech']:.0f} s de parole et "
            f"{p['action']:.0f} s d'action ; {p['hits_per_min']:.0f} coups ou chocs par minute.\n"
            f"Plateforme : {PLATFORMS.get(platform, platform)}. Ton voulu : {TONES.get(tone, tone)}. "
            f"Public : {audience or 'non précisé'}.\n\nMoments candidats :\n{_segments_table(segments, 250)}")
    r = anthropic.Anthropic().beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=4000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": config.CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user}])
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    out = []
    for d in data["directions"][:3]:
        settings = {k: d[k] for k in ("style", "format", "target_duration", "captions", "music", "instructions")}
        out.append(Direction(name=d["name"], pitch=d["pitch"], rhythm=d["rhythm"], tone=d["tone"],
                             settings=settings, recommended=bool(d["recommended"])))
    if len(out) != 3:
        raise RuntimeError("le directeur créatif n'a pas proposé 3 directions")
    if sum(d.recommended for d in out) != 1:
        for k, d in enumerate(out):
            d.recommended = k == 0
    return out


def propose(project: Project, segments: list[Segment], platform: str = "auto", tone: str = "auto",
            audience: str = "") -> tuple[list[Direction], str]:
    if config.claude_available():
        try:
            return with_claude(project, segments, platform, tone, audience), "claude"
        except Exception as e:
            log.warning("Directions Claude impossibles, propositions locales : %s", e)
    return heuristic(project, segments, platform, tone), "local"


def apply(settings: EditSettings, d: Direction) -> EditSettings:
    keep = {k: v for k, v in d.settings.items() if k in EditSettings.model_fields}
    return settings.model_copy(update=keep)
