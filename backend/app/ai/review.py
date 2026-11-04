"""Relecteur indépendant : un second avis qui n'a pas vu la construction du montage.

Il reçoit seulement le résultat (images clés, paroles, feuille son/image, rapport qualité) et note la version
sur 10. Sous la note de passage, le réalisateur reprend le montage avec ses remarques, 3 tours au plus.
Sans clé Claude, un relecteur local note à partir du contrôle qualité.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess

from .. import config
from ..media.ffmpeg import FFMPEG
from ..models import Project, QualityReport, Review, ReviewRemark, Timeline, Transcript
from ..render import captions as cap
from . import quality

log = logging.getLogger(__name__)

PASS_SCORE = 7.0
MAX_ROUNDS = 3

SYSTEM = """Tu es un relecteur exigeant de vidéos pour YouTube et les réseaux sociaux. Tu n'as pas vu comment \
ce montage a été construit : tu juges seulement le résultat, comme un spectateur attentif et un chef monteur.

Note de 0 à 10 :
- 9-10 : prêt à publier, rien à reprocher ;
- 7-8 : bon, quelques détails ;
- 5-6 : correct mais générique ou mou par endroits ;
- moins de 5 : problèmes qui font décrocher.

Regarde en priorité :
- l'accroche : les 3 premières secondes donnent-elles envie de rester ?
- le rythme : plans trop longs, temps morts, ou au contraire tout trop rapide pour suivre ;
- le moment fort : est-il préparé (une courte pause avant) et appuyé (zoom, bruitage, musique) ?
- la synchro son/image (lis la feuille son/image : un impact sonore sans rien à l'image, un bruitage qui tombe \
à côté, un pic musical loin du moment fort) ;
- les textes à l'écran : lisibles sur un téléphone, assez longtemps, sans fautes ;
- les affirmations des textes et du titre : signale celles qui exagèrent ou promettent trop ;
- le rendu « template » : effets plaqués partout, cartes génériques, slogan final creux. Pénalise-le.
Sois concret : chaque remarque dit l'instant (en secondes sur la timeline), le problème et la correction.
`instructions` : une consigne de retouche unique, en français, que le réalisateur appliquera (vide si la note \
est d'au moins 8)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "verdict": {"type": "string"},
        "remarks": {"type": "array", "items": {"type": "object", "properties": {
            "t": {"type": ["number", "null"]}, "problem": {"type": "string"}, "fix": {"type": "string"}},
            "required": ["t", "problem", "fix"], "additionalProperties": False}},
        "instructions": {"type": "string"},
    },
    "required": ["score", "verdict", "remarks", "instructions"],
    "additionalProperties": False,
}


def describe(tl: Timeline) -> str:
    t, lines = 0.0, []
    for i, c in enumerate(tl.clips):
        extras = [f"zoom {c.zoom:.2f}" if c.zoom > 1.01 else "", f"vitesse {c.speed}" if c.speed != 1 else "",
                  f"transition {c.transition}" if c.transition != "aucune" else "", ",".join(c.effects)]
        lines.append(f"plan {i + 1} : {t:.1f}-{t + c.duration:.1f} s{' · ' + ' · '.join(x for x in extras if x) if any(extras) else ''}")
        t += c.duration
    lines += [f"texte {x.style} {x.start:.1f}-{x.start + x.duration:.1f} s : « {x.text} »" for x in tl.texts]
    lines += [f"impact {x.kind} à {x.start:.1f} s" for x in tl.impacts]
    lines += [f"plan de coupe {o.start:.1f}-{o.start + o.duration:.1f} s ({o.mode})" for o in tl.overlays]
    lines.append(f"format {tl.format}, sous-titres {tl.captions}, étalonnage {tl.look}, musique "
                 f"{'oui' if tl.music.track else 'non'}, titre « {tl.title} »")
    return "\n".join(lines)


def keyframes(project: Project, tl: Timeline, n: int = 6) -> list[tuple[float, bytes]]:
    """Images du rush aux instants clés : ouverture, moment fort, puis réparties sur la durée."""
    if not tl.clips:
        return []
    starts = quality._starts(tl)
    total = tl.duration
    times = [min(0.5, total / 2)]
    best = quality.strongest(tl)
    if best:
        times.append(best[1] + 0.3)
    times += [total * k / (n - 1) for k in range(1, n - 1)]
    times = sorted({round(min(max(0.0, x), total - 0.05), 1) for x in times})[:n]
    media = {m.id: m for m in project.media}
    out = []
    for t in times:
        i = max(k for k, s in enumerate(starts) if s <= t + 1e-6)
        c = tl.clips[i]
        m = media.get(c.media_id)
        if not m or not m.has_video:
            continue
        src = c.src_in + c.local_to_src(t - starts[i])
        p = subprocess.run([FFMPEG, "-loglevel", "error", "-ss", f"{src:.3f}", "-i", m.proxy or m.path,
                            "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
                           capture_output=True)
        if p.returncode == 0 and p.stdout:
            out.append((t, p.stdout))
    return out


def local_review(report: QualityReport) -> Review:
    issues = [i for i in report.issues if i.level != "ok"]
    score = max(0.0, 10.0 - 2.5 * sum(i.level == "erreur" for i in issues) - 0.8 * sum(i.level == "alerte" for i in issues))
    remarks = [ReviewRemark(t=i.t, problem=i.message, fix="corrigé automatiquement au prochain tour" if i.fix else "")
               for i in issues]
    verdict = ("Rien à signaler au contrôle." if not issues else
               f"{len(issues)} point(s) relevé(s) par le contrôle qualité.")
    return Review(score=round(score, 1), verdict=verdict, remarks=remarks, reviewer="local")


class ClaudeReviewer:
    def __init__(self) -> None:
        import anthropic
        self.client = anthropic.Anthropic()

    def review(self, project: Project, tl: Timeline, transcripts: dict[str, Transcript], report: QualityReport,
               sound_sheet: str, style: str) -> tuple[Review, str]:
        lines = cap.group_lines(cap.timeline_words(tl, transcripts), 12, 80)
        speech = "\n".join(f"{ln.start:.1f} s : {ln.text}" for ln in lines[:120]) or "(pas de parole)"
        text = (f"Vidéo de style {style}, durée {tl.duration:.1f} s.\n\nCe qui est à l'écran :\n{describe(tl)}\n\n"
                f"Paroles :\n{speech}\n\nFeuille son/image :\n{sound_sheet}\n\n{quality.report_text(report)}\n\n"
                "Note cette version.")
        content: list = []
        for t, jpg in keyframes(project, tl):
            content.append({"type": "text", "text": f"Image à {t:.1f} s :"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(jpg).decode()}})
        content.append({"type": "text", "text": text})
        r = self.client.beta.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=6000, system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": config.CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        if r.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"relecture interrompue ({r.stop_reason})")
        data = json.loads(next(b.text for b in r.content if b.type == "text"))
        rev = Review(score=round(max(0.0, min(10.0, float(data["score"]))), 1), verdict=data.get("verdict", ""),
                     remarks=[ReviewRemark(t=x.get("t"), problem=x["problem"], fix=x.get("fix", ""))
                              for x in data.get("remarks", [])], reviewer="claude")
        return rev, data.get("instructions", "")


def get_reviewer():
    if config.claude_available():
        try:
            return ClaudeReviewer()
        except Exception as e:  # pragma: no cover
            log.warning("Relecteur Claude indisponible : %s", e)
    return None
