"""Chat avec le réalisateur IA : il connaît le projet (dossier des rushes) et peut appliquer une consigne.

Avec Claude : une réponse JSON {reply, consigne}. Si `consigne` est remplie, elle est appliquée au montage par
le monteur automatique (comme l'ancienne barre « Consigne »). Sans clé : pas de discussion ; le message est
appliqué comme une consigne simple par le réalisateur local, et la réponse le dit.
"""
from __future__ import annotations

import json
import logging

from .. import config, storage
from ..models import ChatMessage, Project
from .dossier import project_dossier

log = logging.getLogger(__name__)

HISTORY = 12  # derniers messages donnés au modèle

SYSTEM = (
    "Tu es le réalisateur IA de Monteur IA, un logiciel de montage vidéo. Tu discutes avec le monteur de SA vidéo. "
    "Tu reçois un dossier : ce que l'app a mesuré dans ses rushes (durées, coups détectés au son, combos, écrans "
    "rouges = coups probablement encaissés, changements de plan, phrases transcrites avec horodatage), le montage "
    "actuel plan par plan, ses repères, ses commentaires, ses notes de recherche. Appuie-toi dessus et cite des "
    "horodatages précis (m:ss). Ne dis jamais que tu ne peux pas voir les rushes : dis ce que le dossier contient, "
    "et ce qu'il ne mesure pas (le dossier le précise) en présentant tes déductions comme probables.\n"
    "Réponds en français, court et concret (3 à 10 phrases ou puces), en tutoyant. Pas de titres Markdown.\n"
    "Si le monteur demande un changement du montage (durée, format, garder ou couper un passage, ordre des plans, "
    "ralenti, effets, musique, sous-titres, texte à l'écran…), remplis `consigne` avec une consigne précise et "
    "autonome pour le monteur automatique (horodatages et noms de fichiers inclus) et dis dans `reply` ce que tu "
    "fais ; sinon `consigne` vaut null. Une question, un avis ou un tri de passages n'est pas une consigne. "
    "Pour un fait extérieur au projet, renvoie vers l'onglet Recherche."
)
SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "consigne": {"type": ["string", "null"]}},
    "required": ["reply", "consigne"],
    "additionalProperties": False,
}


def _claude(project: Project, text: str) -> dict:
    import anthropic
    from .research import notes_as_instructions
    dossier = project_dossier(project)
    notes = notes_as_instructions(project.research) if project.research else ""
    messages: list[dict] = []
    for m in project.chat[-HISTORY:]:
        role = "user" if m.role == "monteur" else "assistant"
        content = m.text if role == "user" else json.dumps({"reply": m.text, "consigne": m.consigne}, ensure_ascii=False)
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + content
        else:
            messages.append({"role": role, "content": content})
    user = f"DOSSIER DU PROJET (mesures de l'app) :\n{dossier}\n\n" + (f"{notes}\n\n" if notes else "") + f"Message du monteur : {text}"
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + user
    else:
        messages.append({"role": "user", "content": user})
    if messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "(début de la conversation)"})
    r = anthropic.Anthropic().beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=2500, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": config.CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=messages)
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    reply = (data.get("reply") or "").strip()
    consigne = (data.get("consigne") or "").strip() or None
    if not reply:
        raise RuntimeError("réponse vide du réalisateur")
    return {"reply": reply, "consigne": consigne}


def talk(project_id: str, text: str, ctx) -> dict:
    """Tâche « chat » : enregistre le message, répond, applique la consigne s'il y en a une."""
    from .. import pipeline
    text = text.strip()
    if not text:
        raise ValueError("Message vide.")
    mine = ChatMessage(role="monteur", text=text)
    storage.update_project(project_id, lambda p: p.chat.append(mine))
    project = storage.load_project(project_id)
    ctx.step("Le réalisateur relit le dossier des rushes", 0.15)
    by = "local"
    if config.claude_available():
        try:
            ctx.step("Le réalisateur répond (Claude)", 0.3)
            out = _claude(project, text)
            by = "claude"
        except Exception as e:
            log.warning("Chat réalisateur Claude en échec : %s", e)
            out = {"reply": f"Je n'ai pas pu répondre ({e}). Réessaie, ou donne une consigne simple.", "consigne": None}
    elif project.timeline.clips:
        out = {"reply": "Sans clé Claude (Réglages), je ne peux pas discuter du projet : j'applique ta demande comme une "
                        "consigne simple (durée, format, sous-titres, musique, zooms, couper un passage).", "consigne": text}
    else:
        out = {"reply": "Sans clé Claude (Réglages), je ne peux pas discuter du projet. Lance d'abord « Monter "
                        "automatiquement », puis donne-moi des consignes simples : durée, format, sous-titres, musique.",
               "consigne": None}
    applied = False
    if out["consigne"] and project.timeline.clips:
        try:
            res = pipeline.ai_command(project_id, out["consigne"], pipeline._Scoped(ctx, 0.5, 0.95))
            applied = True
            if by == "local" and res.get("message"):
                out["reply"] += f"\n\n{res['message']}"
        except Exception as e:
            log.warning("Consigne du chat non appliquée : %s", e)
            out["reply"] += f"\n\n(La consigne n'a pas pu être appliquée : {e})"
    elif out["consigne"]:
        out["reply"] += "\n\n(Pas encore de montage à modifier : lance d'abord « Monter automatiquement ».)"
    answer = ChatMessage(role="realisateur", text=out["reply"], consigne=out["consigne"], applied=applied, by=by)
    storage.update_project(project_id, lambda p: p.chat.append(answer))
    ctx.step("Réponse du réalisateur", 1.0)
    return {"message": answer.model_dump(), "applied": applied}
