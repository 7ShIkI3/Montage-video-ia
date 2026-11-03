"""Le réalisateur : transforme des moments candidats notés en timeline montée.

Deux implémentations :
- `HeuristicDirector` : local, instantané, sans clé API ;
- `ClaudeDirector` : comprend le sens du discours et obéit aux consignes en langage naturel.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import numpy as np

from .. import config
from ..models import Clip, EditSettings, MusicSettings, Segment, Timeline

log = logging.getLogger(__name__)


@dataclass
class StyleProfile:
    include_action: bool
    keep_quantile: float  # on garde les segments au-dessus de ce quantile de score
    default_target: float | None
    max_target: float | None
    hook_first: bool
    max_action_len: float
    merge_gap: float


STYLES: dict[str, StyleProfile] = {
    "podcast": StyleProfile(False, 0.0, None, None, False, 0.0, 0.35),
    "youtube": StyleProfile(True, 0.15, None, None, False, 3.0, 0.15),
    "vlog": StyleProfile(True, 0.1, None, None, False, 3.5, 0.15),
    "short": StyleProfile(True, 0.0, 40.0, 60.0, True, 2.5, 0.1),
}


# ---------------------------------------------------------------------------
# Outils communs
# ---------------------------------------------------------------------------

def _fmt(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def merge_contiguous(clips: list[Clip], gap: float) -> list[Clip]:
    out: list[Clip] = []
    for c in clips:
        if out:
            p = out[-1]
            if (p.media_id == c.media_id and 0 <= c.src_in - p.src_out <= gap
                    and abs(p.zoom - c.zoom) < 1e-3):
                p.src_out = c.src_out
                p.score = max(p.score, c.score)
                continue
        out.append(c.model_copy())
    return out


def apply_jump_cut_zooms(clips: list[Clip], top_scores: float) -> None:
    """Technique YouTube : alterner le cadrage pour masquer les coupes franches."""
    for i, c in enumerate(clips):
        if c.score >= top_scores:
            c.zoom = 1.12
            if "zoom" not in c.reason:
                c.reason += " Zoom d'emphase sur un moment fort."
        elif i > 0:
            p = clips[i - 1]
            jump = p.media_id == c.media_id and 0 <= c.src_in - p.src_out < 3.0
            if jump and abs(p.zoom - 1.0) < 1e-3:
                c.zoom = 1.06


def _reason(seg: Segment) -> str:
    f = seg.features
    bits = []
    if seg.kind == "speech":
        if f.get("hook", 0) >= 0.6:
            bits.append("formule accrocheuse")
        if f.get("energy", 0) > 0.6:
            bits.append("voix énergique")
        if f.get("speech_rate", 0) > 2.8:
            bits.append("débit dynamique")
    else:
        if f.get("motion", 0) > 0.4:
            bits.append("beaucoup de mouvement")
        if f.get("cuts", 0) > 0.3:
            bits.append("plans rythmés")
    if f.get("peak"):
        bits.append("pic sonore : rire ou réaction")
    base = "Parole claire" if seg.kind == "speech" else "Plan d'illustration"
    return f"{base} ({', '.join(bits)})." if bits else f"{base}, score {seg.score:.2f}."


def _clip_from_segment(seg: Segment, reason: str = "", zoom: float = 1.0,
                       trim_start: float = 0.0, trim_end: float = 0.0) -> Clip:
    src_in = min(seg.end - 0.3, seg.start + max(0.0, trim_start))
    src_out = max(src_in + 0.3, seg.end - max(0.0, trim_end))
    return Clip(media_id=seg.media_id, src_in=round(src_in, 3), src_out=round(src_out, 3),
                segment_id=seg.id, score=seg.score, reason=reason or _reason(seg),
                zoom=float(np.clip(zoom, 1.0, 1.5)))


def _rejected(segments: list[Segment], kept_ids: set[str], why: dict[str, str]) -> list[Clip]:
    out = []
    for s in segments:
        if s.id in kept_ids:
            continue
        out.append(Clip(media_id=s.media_id, src_in=s.start, src_out=s.end, segment_id=s.id,
                        score=s.score, reason=why.get(s.id, "Écarté : score plus faible que les moments retenus.")))
    return out


def base_timeline(settings: EditSettings, music_track: str | None) -> Timeline:
    return Timeline(
        format=settings.format,
        captions=settings.captions,
        music=MusicSettings(track=music_track if settings.music else None,
                            volume=0.14 if settings.style in ("youtube", "podcast") else 0.2),
    )


# ---------------------------------------------------------------------------
# Réalisateur heuristique
# ---------------------------------------------------------------------------

class HeuristicDirector:
    name = "heuristique"

    def direct(self, segments: list[Segment], settings: EditSettings, media_order: list[str],
               music_track: str | None = None) -> Timeline:
        prof = STYLES[settings.style]
        order = {m: i for i, m in enumerate(media_order)}
        why: dict[str, str] = {}

        pool = []
        for s in segments:
            if s.duplicate_of:
                why[s.id] = "Écarté : prise répétée, la prise suivante est meilleure."
                continue
            if s.kind == "action" and not prof.include_action:
                why[s.id] = "Écarté : pas de parole (style podcast)."
                continue
            pool.append(s)

        if not pool:
            tl = base_timeline(settings, music_track)
            tl.director = self.name
            tl.rejected = _rejected(segments, set(), why)
            return tl

        scores = np.array([s.score for s in pool])
        thr = float(np.quantile(scores, prof.keep_quantile)) if prof.keep_quantile > 0 else -1
        target = settings.target_duration or prof.default_target
        if prof.max_target and target:
            target = min(target, prof.max_target)

        speech_pool = [s for s in pool if s.kind == "speech"]
        action_pool = [s for s in pool if s.kind == "action"]
        # Les plans d'action sont des respirations : on n'en garde que les meilleurs.
        if action_pool:
            a_scores = np.array([s.score for s in action_pool])
            a_thr = float(np.quantile(a_scores, 0.6 if speech_pool else 0.3))
            kept_action = [s for s in action_pool if s.score >= a_thr]
            for s in action_pool:
                if s not in kept_action:
                    why[s.id] = "Écarté : plan d'illustration moins intéressant que les autres."
        else:
            kept_action = []

        candidates = [s for s in speech_pool if s.score >= thr] + kept_action
        for s in speech_pool:
            if s.score < thr:
                why[s.id] = "Écarté : passage plus faible (énergie, débit, intérêt)."

        def clip_len(s: Segment) -> float:
            return min(s.duration, prof.max_action_len) if s.kind == "action" else s.duration

        if target:
            ranked = sorted(candidates, key=lambda s: s.score, reverse=True)
            chosen, total = [], 0.0
            for s in ranked:
                L = clip_len(s)
                if total + L <= target * 1.05 or not chosen:
                    chosen.append(s)
                    total += L
                else:
                    why[s.id] = f"Écarté : la durée cible ({int(target)} s) est atteinte."
                if total >= target * 0.97:
                    for r in ranked[ranked.index(s) + 1:]:
                        why.setdefault(r.id, f"Écarté : la durée cible ({int(target)} s) est atteinte.")
                    break
        else:
            chosen = candidates

        chosen.sort(key=lambda s: (order.get(s.media_id, 0), s.start))
        hook = None
        if prof.hook_first and len(chosen) > 1:
            hook = max(chosen, key=lambda s: s.score + 0.3 * s.features.get("hook", 0))
            chosen.remove(hook)
            chosen.insert(0, hook)

        clips = []
        for s in chosen:
            c = _clip_from_segment(s)
            if s.kind == "action" and c.duration > prof.max_action_len:
                mid = (c.src_in + c.src_out) / 2
                c.src_in, c.src_out = round(mid - prof.max_action_len / 2, 3), round(mid + prof.max_action_len / 2, 3)
            if s is hook:
                c.reason = "Accroche : le moment le plus fort est placé en ouverture. " + c.reason
            clips.append(c)

        gap = prof.merge_gap if settings.remove_silences else 1.5
        clips = merge_contiguous(clips, gap)
        if settings.punch_zooms and settings.style != "podcast" and clips:
            top = float(np.quantile([c.score for c in clips], 0.85))
            apply_jump_cut_zooms(clips, top)

        tl = base_timeline(settings, music_track)
        tl.clips = clips
        tl.director = self.name
        tl.title = ""
        tl.summary = (f"{len(clips)} plans retenus sur {len(segments)} moments analysés, "
                      f"durée {_fmt(tl.duration)}.")
        tl.rejected = _rejected(segments, {c.segment_id for c in clips if c.segment_id}
                                | {s.id for s in chosen}, why)
        return tl


# ---------------------------------------------------------------------------
# Réalisateur Claude
# ---------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "trim_start": {"type": "number"},
                    "trim_end": {"type": "number"},
                    "zoom": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["segment_id", "trim_start", "trim_end", "zoom", "reason"],
                "additionalProperties": False,
            },
        },
        "format": {"type": "string", "enum": ["16:9", "9:16", "1:1", "inchangé"]},
        "captions": {"type": "string", "enum": ["karaoke", "classique", "aucun", "inchangé"]},
        "music": {"type": "string", "enum": ["oui", "non", "inchangé"]},
    },
    "required": ["title", "summary", "clips", "format", "captions", "music"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu es un monteur vidéo professionnel. On te donne la liste des moments candidats \
repérés dans les rushes (transcription horodatée et signaux mesurés). Tu construis le montage final.

Règles de métier :
- Ne garde que ce qui sert la vidéo : supprime les redites, digressions, faux départs, passages plats.
- Une phrase coupée au milieu est interdite : choisis des segments entiers (trim_start/trim_end servent \
à resserrer de quelques dixièmes de seconde au plus, mets 0 sinon).
- Respecte la durée cible si elle est donnée (tolérance 10 %).
- Short (9:16) : l'accroche la plus forte en premier, rythme très soutenu, 15 à 60 s.
- YouTube : histoire claire début / milieu / fin, rythme soutenu, ordre chronologique sauf raison forte.
- Vlog : garde l'ambiance, intercale des plans d'illustration (kind=action) de 2 à 3 s.
- Podcast : garde presque tout le fond, enlève seulement silences, hésitations et redites.
- zoom : 1.0 par défaut, 1.06 à 1.15 pour souligner un moment fort ou masquer une coupe franche.
- reason : une phrase courte en français qui explique ton choix à l'utilisateur.
- title : un titre accrocheur pour la vidéo ; summary : 1 à 2 phrases sur le montage.
- format / captions / music : "inchangé" sauf si la consigne de l'utilisateur demande un changement.
L'ordre de la liste `clips` est l'ordre de la timeline."""


def _segments_table(segments: list[Segment], limit: int = 700) -> str:
    segs = [s for s in segments]
    if len(segs) > limit:
        keep = set(id(s) for s in sorted(segs, key=lambda s: s.score, reverse=True)[:limit])
        segs = [s for s in segs if id(s) in keep]
    lines = ["id | type | début-fin (s) | durée | score | énergie | mouvement | doublon | texte"]
    for s in segs:
        f = s.features
        lines.append(
            f"{s.id} | {s.kind} | {s.start:.2f}-{s.end:.2f} | {s.duration:.1f} | {s.score:.2f} | "
            f"{f.get('energy', 0):.2f} | {f.get('motion', 0):.2f} | {'oui' if s.duplicate_of else 'non'} | "
            f"{s.text[:300]}"
        )
    return "\n".join(lines)


class ClaudeDirector:
    name = "claude"

    def __init__(self) -> None:
        import anthropic

        self.client = anthropic.Anthropic()

    def _call(self, user_content: str) -> dict:
        response = self.client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": config.CLAUDE_EFFORT,
                "format": {"type": "json_schema", "schema": PLAN_SCHEMA},
            },
            messages=[{"role": "user", "content": user_content}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Le modèle a refusé la demande.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Réponse tronquée du réalisateur IA.")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def _to_timeline(self, plan: dict, segments: list[Segment], settings: EditSettings,
                     music_track: str | None) -> Timeline:
        by_id = {s.id: s for s in segments}
        clips = []
        for item in plan.get("clips", []):
            seg = by_id.get(item.get("segment_id", ""))
            if not seg:
                continue
            clips.append(_clip_from_segment(seg, item.get("reason", ""), item.get("zoom", 1.0),
                                            item.get("trim_start", 0.0), item.get("trim_end", 0.0)))
        if not clips:
            raise RuntimeError("Le réalisateur IA n'a retenu aucun segment valide.")
        s2 = settings.model_copy()
        if plan.get("format") in ("16:9", "9:16", "1:1"):
            s2.format = plan["format"]
        if plan.get("captions") in ("karaoke", "classique", "aucun"):
            s2.captions = plan["captions"]
        if plan.get("music") == "oui":
            s2.music = True
        elif plan.get("music") == "non":
            s2.music = False
        tl = base_timeline(s2, music_track)
        tl.clips = merge_contiguous(clips, 0.1)
        tl.title = plan.get("title", "")
        tl.summary = plan.get("summary", "")
        tl.director = self.name
        tl.rejected = _rejected(segments, {c.segment_id for c in clips}, {
            s.id: "Écarté : prise répétée." for s in segments if s.duplicate_of})
        return tl

    def direct(self, segments: list[Segment], settings: EditSettings, media_order: list[str],
               music_track: str | None = None) -> Timeline:
        target = settings.target_duration or STYLES[settings.style].default_target
        user = (
            f"Style : {settings.style}. Format : {settings.format}. "
            f"Durée cible : {f'{int(target)} s' if target else 'libre (ce qui est nécessaire)'}.\n"
            f"Ordre des fichiers : {', '.join(media_order)}.\n"
            f"Consignes de l'utilisateur : {settings.instructions or 'aucune'}\n\n"
            f"Moments candidats :\n{_segments_table(segments)}"
        )
        return self._to_timeline(self._call(user), segments, settings, music_track)

    def command(self, instruction: str, timeline: Timeline, segments: list[Segment],
                settings: EditSettings, music_track: str | None = None) -> Timeline:
        current = "\n".join(
            f"{i + 1}. {c.segment_id} [{c.src_in:.2f}-{c.src_out:.2f}] zoom {c.zoom:.2f}"
            for i, c in enumerate(timeline.clips)
        )
        user = (
            f"Voici le montage actuel (style {settings.style}, format {timeline.format}, "
            f"sous-titres {timeline.captions}, durée {timeline.duration:.1f} s) :\n{current}\n\n"
            f"Consigne de l'utilisateur à appliquer : « {instruction} »\n"
            "Renvoie le montage complet modifié (garde tel quel ce que la consigne ne touche pas).\n\n"
            f"Moments candidats disponibles :\n{_segments_table(segments)}"
        )
        tl = self._to_timeline(self._call(user), segments, settings, music_track)
        if not any(k in instruction.lower() for k in ("musique", "music")):
            tl.music = timeline.music
        return tl


# ---------------------------------------------------------------------------
# Consignes sans clé API : quelques commandes courantes comprises localement
# ---------------------------------------------------------------------------

def heuristic_command(instruction: str, timeline: Timeline, segments: list[Segment],
                      settings: EditSettings, media_order: list[str],
                      music_track: str | None) -> tuple[Timeline, str, EditSettings]:
    text = instruction.lower()
    s2 = settings.model_copy()
    changed = []
    m = re.search(r"(\d+)\s*(s\b|sec|secondes?|seconds?)", text)
    mm = re.search(r"(\d+)\s*(min|minutes?)", text)
    if m or mm:
        s2.target_duration = float(m.group(1)) if m else float(mm.group(1)) * 60
        changed.append(f"durée cible {int(s2.target_duration)} s")
    if any(k in text for k in ("vertical", "9:16", "tiktok", "reel", "short")):
        s2.format = "9:16"
        changed.append("format vertical")
    elif any(k in text for k in ("horizontal", "16:9", "paysage")):
        s2.format = "16:9"
        changed.append("format horizontal")
    elif any(k in text for k in ("carré", "carre", "1:1")):
        s2.format = "1:1"
        changed.append("format carré")
    if "sous-titre" in text or "sous titre" in text:
        if any(k in text for k in ("sans", "enlève", "enleve", "supprime", "retire")):
            s2.captions = "aucun"
            changed.append("sans sous-titres")
        else:
            s2.captions = "karaoke"
            changed.append("sous-titres karaoké")
    if "musique" in text:
        s2.music = not any(k in text for k in ("sans", "enlève", "enleve", "supprime", "retire", "coupe"))
        changed.append("musique " + ("ajoutée" if s2.music else "retirée"))
    if "zoom" in text:
        s2.punch_zooms = not any(k in text for k in ("sans", "enlève", "enleve", "supprime", "pas de"))
        changed.append("zooms " + ("activés" if s2.punch_zooms else "désactivés"))

    remove = re.findall(r"[«\"']([^»\"']+)[»\"']", instruction)
    if changed:
        tl = HeuristicDirector().direct(segments, s2, media_order, music_track)
    else:
        tl = timeline.model_copy(deep=True)
    if remove and any(k in text for k in ("enlève", "enleve", "supprime", "retire", "coupe")):
        by_id = {s.id: s for s in segments}
        before = len(tl.clips)
        tl.clips = [c for c in tl.clips
                    if not any(r.lower() in by_id.get(c.segment_id or "", Segment(id="", media_id="", start=0, end=0)).text.lower()
                               for r in remove)]
        changed.append(f"{before - len(tl.clips)} plan(s) contenant {', '.join(remove)} retiré(s)")
    if not changed:
        raise ValueError(
            "Sans clé API Claude, je comprends seulement : une durée (« 30 secondes »), un format "
            "(« vertical »), les sous-titres, la musique, les zooms, ou « enlève \"mot\" ». "
            "Ajoute ANTHROPIC_API_KEY pour des consignes libres."
        )
    return tl, ", ".join(changed), s2


def get_director():
    if config.claude_available():
        try:
            return ClaudeDirector()
        except Exception as e:  # pragma: no cover
            log.warning("Claude indisponible, repli heuristique : %s", e)
    return HeuristicDirector()
