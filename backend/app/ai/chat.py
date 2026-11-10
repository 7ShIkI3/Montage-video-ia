"""Chat avec le réalisateur IA : un agent Claude qui connaît le projet, regarde les images et agit sur le montage.

Avec Claude, c'est une boucle d'outils (« tool use ») : le modèle reçoit le dossier du projet, puis décide
d'appeler des outils que l'app exécute pour lui, autant de fois qu'il faut, avant de répondre :
- voir_images : des images du rush à des instants précis (c'est ce qui lui permet de dire qui gagne un combat) ;
- lire_paroles : la transcription d'un passage ;
- definir_plans, retoucher_plan, ajouter_texte, poser_reperes : modifications directes et précises du montage ;
- modifier_montage : une consigne libre confiée au monteur automatique (effets, musique, format, durée…) ;
- web_search : une recherche web faite côté Anthropic, pour un fait extérieur.
La réponse arrive au fil de l'eau (aperçu dans la tâche). Sans clé : pas de discussion ; le message est appliqué
comme une consigne simple par le réalisateur local, et la réponse le dit.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .. import config, storage
from ..models import ChatMessage, Clip, ColorAdjust, Marker, Project, TextItem
from . import vision
from .dossier import _phrases, mmss, montage_section, project_dossier

log = logging.getLogger(__name__)

HISTORY = 12  # derniers messages donnés au modèle
MAX_TURNS = 12  # allers-retours modèle ↔ outils par message
MAX_IMAGES = 30  # images par message du monteur (coût maîtrisé)
MAX_IMAGES_PER_CALL = 8

SYSTEM = (
    "Tu es le réalisateur IA de Monteur IA, un logiciel de montage vidéo. Tu travailles avec le monteur sur SA "
    "vidéo. Tu reçois le dossier du projet (mesures de l'app, analyse visuelle quand elle existe, montage actuel "
    "plan par plan) et tu disposes d'outils pour regarder les rushes et agir sur le montage.\n\n"
    "Regarder avant d'affirmer : pour tout ce qui se voit à l'image (qui gagne un combat, ce qui est écrit à "
    "l'écran, ce qu'il se passe à un instant), utilise voir_images au lieu de supposer. L'issue d'un combat se lit "
    "en général juste après la fin du combo (0,5 à 2 s après) ; les messages d'élimination et le chat sont souvent "
    "en bas à gauche, le fil des éliminations en haut à droite : la zone correspondante donne une image plus nette. "
    "Regarde peu d'images à la fois, bien choisies, et arrête-toi dès que tu sais.\n\n"
    "Agir quand on te le demande : une question, un avis ou un tri de passages n'est pas une demande de "
    "modification. Quand le monteur veut un changement, fais-le avec les outils : definir_plans pour construire ou "
    "refaire le découpage à des instants précis, retoucher_plan pour un plan (durée, vitesse, couleurs), "
    "regler_mixage pour l'équilibre sonore, ajouter_texte, poser_reperes, et modifier_montage pour tout le reste "
    "(effets, transitions, choix de la musique, format, sous-titres, durée), décrit en une "
    "consigne précise. Toutes les modifications sont annulables par le monteur.\n\n"
    "Répondre : en français, en tutoyant, court et concret (quelques phrases ou puces), avec des horodatages au "
    "format m:ss. Dis ce que tu as regardé et ce que tu as changé. Quand tu n'as pas pu vérifier quelque chose, "
    "dis-le. Pas de titres Markdown."
)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoirImages(_In):
    media: str
    instants: list[float]
    zone: Literal["entier", "haut_gauche", "haut_droite", "bas_gauche", "bas_droite", "centre"] = "entier"


class LireParoles(_In):
    media: str
    debut: float = 0.0
    fin: float | None = None


class PlanIn(_In):
    media: str
    debut: float
    fin: float
    vitesse: float = 1.0
    raison: str = ""


class DefinirPlans(_In):
    plans: list[PlanIn]
    titre: str = ""
    resume: str = ""


class CouleurIn(_In):
    exposition: float = 0.0
    contraste: float = 0.0
    saturation: float = 0.0
    temperature: float = 0.0


class RetoucherPlan(_In):
    numero: int
    debut: float | None = None
    fin: float | None = None
    vitesse: float | None = None
    couleur: CouleurIn | None = None
    supprimer: bool = False


class ReglageMix(_In):
    groupe: Literal["plans", "musique", "bruitages", "pistes"]
    volume: float | None = None
    coupe: bool | None = None


class ReglerMixage(_In):
    reglages: list[ReglageMix]


class AjouterTexte(_In):
    texte: str
    debut: float
    duree: float = 2.5
    style: Literal["titre", "bandeau", "legende", "gros_texte", "emoji", "texte_3d"] = "titre"
    position: Literal["haut", "centre", "bas"] = "centre"


class RepereIn(_In):
    t: float
    label: str = ""


class PoserReperes(_In):
    reperes: list[RepereIn]


class ModifierMontage(_In):
    consigne: str


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    # Requête diffusée (stream) : les entrées d'outil arrivent au fil de l'eau, non validées côté serveur.
    # Chaque entrée est donc validée ici (modèles pydantic ci-dessus) avant d'exécuter l'outil.
    return {"name": name, "description": description, "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


_MEDIA = {"type": "string", "description": "Nom de fichier du rush, tel qu'il apparaît dans le dossier."}
TOOLS: list[dict] = [
    _tool("voir_images",
          "Regarde des images d'un rush à des instants précis (secondes dans le rush, pas dans le montage). À "
          "appeler dès qu'une réponse dépend de ce qu'on voit : issue d'un combat, texte à l'écran, contenu d'un "
          f"passage. Jusqu'à {MAX_IMAGES_PER_CALL} instants par appel. `zone` recadre un quart de l'image en pleine "
          "définition pour lire un petit texte (chat en bas à gauche, fil des éliminations en haut à droite).",
          {"media": _MEDIA, "instants": {"type": "array", "items": {"type": "number"}, "description": "Instants en secondes."},
           "zone": {"type": "string", "enum": list(vision.ZONES)}}, ["media", "instants"]),
    _tool("lire_paroles",
          "Lit la transcription d'un rush entre deux instants (secondes), phrase par phrase avec leurs horodatages. "
          "À appeler quand le dossier n'a qu'un échantillon des paroles et qu'il faut le texte exact d'un passage.",
          {"media": _MEDIA, "debut": {"type": "number"}, "fin": {"type": "number"}}, ["media"]),
    _tool("definir_plans",
          "Remplace le découpage du montage par cette liste de plans, dans l'ordre (chaque plan : rush, début et fin "
          "en secondes dans le rush, vitesse 0.25 à 4). À appeler quand le monteur demande de construire ou refaire "
          "le montage autour de passages précis. Les textes, bruitages, impacts et plans de coupe posés sur l'ancien "
          "découpage sont retirés (ils ne tomberaient plus au bon endroit) ; musique, format, sous-titres et repères "
          "restent. Renvoie le nouveau montage avec la position de chaque plan.",
          {"plans": {"type": "array", "items": {"type": "object", "properties": {
              "media": _MEDIA, "debut": {"type": "number"}, "fin": {"type": "number"},
              "vitesse": {"type": "number"}, "raison": {"type": "string", "description": "Pourquoi ce plan, en quelques mots."}},
              "required": ["media", "debut", "fin"]}},
           "titre": {"type": "string"}, "resume": {"type": "string", "description": "Une ou deux phrases sur le parti pris."}},
          ["plans"]),
    _tool("retoucher_plan",
          "Modifie un plan du montage actuel, désigné par son numéro dans le dossier : nouveau début ou nouvelle fin "
          "(secondes dans le rush), vitesse, correction des couleurs, ou suppression. À appeler pour une retouche "
          "ciblée, par exemple un plan trop sombre ou trop terne que tu viens de regarder. Couleur : chaque valeur va "
          "de -1 à 1, 0 = image d'origine (exposition : plus clair ou plus sombre ; température : -1 froid, 1 chaud).",
          {"numero": {"type": "integer"}, "debut": {"type": "number"}, "fin": {"type": "number"},
           "vitesse": {"type": "number"}, "supprimer": {"type": "boolean"},
           "couleur": {"type": "object", "properties": {
               "exposition": {"type": "number"}, "contraste": {"type": "number"},
               "saturation": {"type": "number"}, "temperature": {"type": "number"}}}}, ["numero"]),
    _tool("regler_mixage",
          "Règle le mixage audio du montage par groupe de sons : « plans » (son des vidéos), « musique », "
          "« bruitages », « pistes » (voix off, sons importés). Volume de 0 à 2 (1 = inchangé), `coupe` pour rendre "
          "un groupe muet. À appeler quand le monteur parle d'équilibre sonore : musique trop forte, voix couverte, "
          "couper le son du jeu.",
          {"reglages": {"type": "array", "items": {"type": "object", "properties": {
              "groupe": {"type": "string", "enum": ["plans", "musique", "bruitages", "pistes"]},
              "volume": {"type": "number"}, "coupe": {"type": "boolean"}}, "required": ["groupe"]}}}, ["reglages"]),
    _tool("ajouter_texte",
          "Pose un texte à l'écran sur le montage (début en secondes sur la timeline du montage, pas dans le rush).",
          {"texte": {"type": "string"}, "debut": {"type": "number"}, "duree": {"type": "number"},
           "style": {"type": "string", "enum": ["titre", "bandeau", "legende", "gros_texte", "emoji", "texte_3d"]},
           "position": {"type": "string", "enum": ["haut", "centre", "bas"]}}, ["texte", "debut"]),
    _tool("poser_reperes",
          "Pose des repères nommés sur la timeline du montage (secondes sur la timeline) : chapitres, moments à "
          "revoir. À appeler quand le monteur demande de marquer des moments.",
          {"reperes": {"type": "array", "items": {"type": "object", "properties": {
              "t": {"type": "number"}, "label": {"type": "string"}}, "required": ["t", "label"]}}}, ["reperes"]),
    _tool("modifier_montage",
          "Confie une consigne libre au monteur automatique, qui refait le montage en conséquence : durée cible, "
          "format (vertical, carré), rythme, effets, transitions, musique, sous-titres, retrait d'un sujet. À appeler "
          "pour les changements qui ne sont pas un découpage précis. Donne une consigne autonome et précise.",
          {"consigne": {"type": "string"}}, ["consigne"]),
]
_INPUTS: dict[str, type[_In]] = {
    "voir_images": VoirImages, "lire_paroles": LireParoles, "definir_plans": DefinirPlans,
    "retoucher_plan": RetoucherPlan, "ajouter_texte": AjouterTexte, "poser_reperes": PoserReperes,
    "modifier_montage": ModifierMontage, "regler_mixage": ReglerMixage,
}
_MIX_GROUPS = {"plans": "rushes", "musique": "music", "bruitages": "sfx", "pistes": "tracks"}


def web_search_tool(max_uses: int = 3) -> dict:
    """Outil de recherche web d'Anthropic : la version récente (filtrage dynamique) sauf pour les modèles Haiku."""
    kind = "web_search_20250305" if "haiku" in config.CLAUDE_MODEL else "web_search_20260209"
    return {"type": kind, "name": "web_search", "max_uses": max_uses}


class ToolError(Exception):
    """Erreur renvoyée au modèle (il peut corriger son appel), pas un plantage de la tâche."""


class Agent:
    """Un message du monteur : la boucle modèle ↔ outils, et ce qu'elle a fait."""

    def __init__(self, project_id: str, ctx) -> None:
        self.pid, self.ctx = project_id, ctx
        self.actions: list[str] = []
        self.applied = False
        self.images = 0
        self.sources: list[dict] = []

    # --- utilitaires -----------------------------------------------------------------------------

    def _media(self, project: Project, name: str):
        name = (name or "").strip()
        for m in project.media:
            if m.filename == name or m.id == name:
                return m
        low = [m for m in project.media if m.filename.lower() == name.lower()]
        if len(low) == 1:
            return low[0]
        if len(project.media) == 1 and not name:
            return project.media[0]
        raise ToolError(f"Rush inconnu : « {name} ». Rushes du projet : {', '.join(m.filename for m in project.media) or 'aucun'}.")

    def _state(self) -> str:
        return "\n".join(montage_section(storage.load_project(self.pid)))

    # --- outils ----------------------------------------------------------------------------------

    def voir_images(self, a: VoirImages) -> list[dict]:
        project = storage.load_project(self.pid)
        m = self._media(project, a.media)
        if not m.has_video:
            raise ToolError(f"{m.filename} n'a pas d'image (fichier audio).")
        instants = [max(0.0, min(float(t), max(0.0, m.duration - 0.05))) for t in a.instants][:MAX_IMAGES_PER_CALL]
        if not instants:
            raise ToolError("Donne au moins un instant.")
        left = MAX_IMAGES - self.images
        if left <= 0:
            raise ToolError(f"Limite de {MAX_IMAGES} images atteinte pour ce message : réponds avec ce que tu as vu.")
        instants = instants[:left]
        self.ctx.step(f"Le réalisateur regarde {m.filename} à " + ", ".join(mmss(t) for t in instants)
                      + ("" if a.zone == "entier" else f" ({a.zone.replace('_', ' ')})"))
        out: list[dict] = []
        for t in instants:
            data = vision.jpeg(m.path, t, a.zone)
            if not data:
                out.append({"type": "text", "text": f"{mmss(t)} ({t:.1f} s) : image illisible"})
                continue
            out.append({"type": "text", "text": f"{m.filename} à {mmss(t)} ({t:.1f} s)" + ("" if a.zone == "entier" else f", zone {a.zone}")})
            out.append(vision.image_block(data))
            self.images += 1
        self.actions.append(f"A regardé {len(instants)} image(s) de {m.filename}")
        return out

    def lire_paroles(self, a: LireParoles) -> str:
        project = storage.load_project(self.pid)
        m = self._media(project, a.media)
        tr = storage.load_transcript(self.pid, m.id)
        if not tr or not tr.words:
            return f"Pas de transcription pour {m.filename} (pas de voix, ou pas encore analysé)."
        hi = a.fin if a.fin is not None else m.duration
        lines = [f"{mmss(t)} « {text} »" for t, text in _phrases(tr) if a.debut - 0.5 <= t <= hi]
        self.actions.append(f"A lu les paroles de {m.filename}")
        return "\n".join(lines[:200]) or "Aucune parole dans ce passage."

    def definir_plans(self, a: DefinirPlans) -> str:
        from .. import pipeline
        project = storage.load_project(self.pid)
        clips: list[Clip] = []
        used = []
        for p in a.plans:
            m = self._media(project, p.media)
            lo, hi = max(0.0, min(p.debut, m.duration)), max(0.0, min(p.fin, m.duration))
            if hi - lo < 0.3:
                raise ToolError(f"Plan trop court ou hors du rush : {m.filename} {p.debut}→{p.fin} s (le rush dure {m.duration:.1f} s).")
            clips.append(Clip(media_id=m.id, src_in=round(lo, 3), src_out=round(hi, 3),
                              speed=max(0.25, min(4.0, p.vitesse or 1.0)), reason=p.raison[:200]))
            if m not in used:
                used.append(m)
        if not clips:
            raise ToolError("Donne au moins un plan.")
        for m in used:  # un rush jamais analysé n'a pas de proxy de lecture : on le prépare
            if m.has_video and not m.proxy:
                pipeline.analyze_media(project, m, pipeline._Scoped(self.ctx, 0.3, 0.6), 0.0, 1.0)
                pipeline._save_media(project)

        def apply(pr: Project) -> None:
            tl = pr.timeline
            if not tl.clips:
                tl.format, tl.captions = pr.settings.format, pr.settings.captions
            tl.clips, tl.rejected = clips, []
            tl.texts, tl.sfx, tl.impacts, tl.overlays = [], [], [], []
            tl.director = "claude"
            tl.title = a.titre[:120] or tl.title
            tl.summary = a.resume[:600] or tl.summary
            tl.review = tl.quality = tl.range = tl.reference_match = None
            tl.plan, tl.voice_focus = [], []
        storage.update_project(self.pid, apply)
        self.applied = True
        self.actions.append(f"A défini le montage : {len(clips)} plan(s)")
        return "Montage remplacé.\n" + self._state()

    def retoucher_plan(self, a: RetoucherPlan) -> str:
        project = storage.load_project(self.pid)
        n = len(project.timeline.clips)
        if not 1 <= a.numero <= n:
            raise ToolError(f"Plan {a.numero} inexistant : le montage a {n} plan(s).")
        dur = {m.id: m.duration for m in project.media}

        def apply(pr: Project) -> None:
            tl = pr.timeline
            c = tl.clips[a.numero - 1]
            if a.supprimer:
                del tl.clips[a.numero - 1]
                return
            lo = c.src_in if a.debut is None else a.debut
            hi = c.src_out if a.fin is None else a.fin
            lo, hi = max(0.0, lo), min(hi, dur.get(c.media_id, hi))
            if hi - lo < 0.3:
                raise ToolError(f"Plan trop court : {lo:.1f}→{hi:.1f} s.")
            c.src_in, c.src_out = round(lo, 3), round(hi, 3)
            if a.vitesse is not None:
                c.speed, c.speed_end = max(0.25, min(4.0, a.vitesse)), None
            if a.couleur is not None:
                def lim(v: float) -> float:
                    return max(-1.0, min(1.0, v))
                col = ColorAdjust(exposure=lim(a.couleur.exposition), contrast=lim(a.couleur.contraste),
                                  saturation=lim(a.couleur.saturation), temperature=lim(a.couleur.temperature))
                c.color = None if col.neutral else col
        storage.update_project(self.pid, apply)
        self.applied = True
        self.actions.append(f"A {'supprimé' if a.supprimer else 'retouché'} le plan {a.numero}")
        return self._state()

    def ajouter_texte(self, a: AjouterTexte) -> str:
        if not a.texte.strip():
            raise ToolError("Texte vide.")
        project = storage.load_project(self.pid)
        if not project.timeline.clips:
            raise ToolError("Pas encore de montage : définis d'abord des plans.")
        start = max(0.0, min(a.debut, max(0.0, project.timeline.duration - 0.5)))
        item = TextItem(start=round(start, 2), duration=max(0.5, min(a.duree, 20.0)), text=a.texte.strip()[:200],
                        style=a.style, position=a.position)
        storage.update_project(self.pid, lambda pr: pr.timeline.texts.append(item))
        self.applied = True
        self.actions.append(f"A posé le texte « {item.text[:40]} » à {mmss(start)}")
        return f"Texte posé à {mmss(start)} pour {item.duration:.1f} s."

    def poser_reperes(self, a: PoserReperes) -> str:
        project = storage.load_project(self.pid)
        total = project.timeline.duration
        new = []
        for r in a.reperes[:40]:
            t = round(max(0.0, min(r.t, total)), 2)
            if all(abs(t - k.t) > 0.3 for k in [*project.timeline.markers, *new]):
                new.append(Marker(t=t, label=r.label.strip()[:60]))
        if not new:
            return "Aucun nouveau repère (déjà posés à ces instants)."
        storage.update_project(self.pid, lambda pr: pr.timeline.markers.extend(new))
        self.applied = True
        self.actions.append(f"A posé {len(new)} repère(s)")
        return "Repères posés : " + ", ".join(f"{mmss(k.t)} {k.label}" for k in new)

    def regler_mixage(self, a: ReglerMixage) -> str:
        if not a.reglages:
            raise ToolError("Donne au moins un réglage.")

        def apply(pr: Project) -> None:
            for r in a.reglages:
                ch = getattr(pr.timeline.mix, _MIX_GROUPS[r.groupe])
                if r.volume is not None:
                    ch.volume = max(0.0, min(2.0, r.volume))
                if r.coupe is not None:
                    ch.mute = r.coupe
        storage.update_project(self.pid, apply)
        self.applied = True
        mix = storage.load_project(self.pid).timeline.mix
        parts = []
        for name, key in _MIX_GROUPS.items():
            ch = getattr(mix, key)
            parts.append(f"{name} coupé" if ch.mute else f"{name} {ch.volume * 100:.0f} %")
        self.actions.append("A réglé le mixage : " + ", ".join(r.groupe for r in a.reglages))
        return "Mixage : " + ", ".join(parts)

    def modifier_montage(self, a: ModifierMontage) -> str:
        from .. import pipeline
        if not a.consigne.strip():
            raise ToolError("Consigne vide.")
        if not storage.load_project(self.pid).timeline.clips:
            raise ToolError("Pas encore de montage : utilise definir_plans, ou dis au monteur de lancer « Monter automatiquement ».")
        self.ctx.step("Le monteur automatique applique la consigne du réalisateur")
        res = pipeline.ai_command(self.pid, a.consigne.strip(), pipeline._Scoped(self.ctx, 0.4, 0.9))
        self.applied = True
        self.actions.append(f"A fait modifier le montage : {a.consigne.strip()[:80]}")
        return (res.get("message") or "Consigne appliquée.") + "\n" + self._state()

    def run_tool(self, name: str, raw) -> tuple[list[dict] | str, bool]:
        """Exécute un outil. Renvoie (contenu du résultat, est-ce une erreur)."""
        model = _INPUTS.get(name)
        if model is None:
            return f"Outil inconnu : {name}", True
        try:
            args = model.model_validate(raw if isinstance(raw, dict) else {})
        except ValidationError as e:
            # Entrée tronquée ou mal formée : renvoyée au modèle, qui peut refaire son appel.
            return json.dumps({"INVALID_JSON": json.dumps(raw, ensure_ascii=False, default=str)[:2000],
                               "erreurs": [f"{'.'.join(map(str, x['loc']))} : {x['msg']}" for x in e.errors()[:5]]},
                              ensure_ascii=False), True
        try:
            return getattr(self, name)(args), False
        except ToolError as e:
            return str(e), True
        except Exception as e:  # un outil qui plante ne doit pas faire échouer la conversation
            from ..jobs import JobCancelled
            if isinstance(e, JobCancelled):
                raise
            log.warning("Outil %s en échec : %s", name, e)
            return f"L'outil a échoué : {e}", True

    # --- boucle ----------------------------------------------------------------------------------

    def converse(self, project: Project, text: str) -> str:
        import anthropic
        from .research import notes_as_instructions
        client = anthropic.Anthropic()
        messages: list[dict] = []
        for m in project.chat[-HISTORY:]:
            role = "user" if m.role == "monteur" else "assistant"
            content = m.text + (f"\n(Actions faites : {' ; '.join(m.actions)})" if role == "assistant" and m.actions else "")
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n\n" + content
            else:
                messages.append({"role": role, "content": content})
        notes = notes_as_instructions(project.research) if project.research else ""
        user = (f"DOSSIER DU PROJET :\n{project_dossier(project, tools=True)}\n\n" + (f"{notes}\n\n" if notes else "")
                + f"Message du monteur : {text}")
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += "\n\n" + user
        else:
            messages.append({"role": "user", "content": user})
        if messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "(début de la conversation)"})

        tools = [*TOOLS, web_search_tool()]
        last_text, json_retries = "", 0
        for turn in range(MAX_TURNS):
            self.ctx.check()
            shown = ""
            try:
                with client.beta.messages.stream(
                    model=config.CLAUDE_MODEL, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                    cache_control={"type": "ephemeral"},  # le dossier et l'historique sont relus à chaque tour d'outil
                    output_config={"effort": config.CLAUDE_EFFORT},
                ) as stream:
                    for event in stream:
                        if getattr(event, "type", "") == "text":
                            shown += event.text
                            self.ctx.preview(shown)
                    response = stream.get_final_message()
                json_retries = 0
                u = getattr(response, "usage", None)
                if u is not None:  # le cache se vérifie ici : « cache lu » doit monter dès le deuxième tour d'outils
                    log.info("Réalisateur, tour %d : entrée %s (cache lu %s), sortie %s", turn + 1,
                             getattr(u, "input_tokens", "?"), getattr(u, "cache_read_input_tokens", "?"),
                             getattr(u, "output_tokens", "?"))
            except ValueError:
                # Entrée d'outil que le SDK n'a pas pu lire du tout : pas de tool_use à qui répondre, on rejoue le tour.
                json_retries += 1
                if json_retries > 2:
                    raise
                continue
            text_now = "".join(b.text for b in response.content if b.type == "text").strip()
            if text_now:
                last_text = text_now
            for b in response.content:
                for c in getattr(b, "citations", None) or [] if b.type == "text" else []:
                    url = getattr(c, "url", "")
                    if url and all(s["url"] != url for s in self.sources):
                        self.sources.append({"title": getattr(c, "title", "") or url, "url": url})
                if b.type == "server_tool_use" and getattr(b, "name", "") == "web_search":
                    self.actions.append("A cherché sur le web")
            if response.stop_reason == "refusal":
                return last_text or "Le modèle a refusé de répondre à cette demande."
            if response.stop_reason == "pause_turn":  # recherche web longue : on renvoie le tour pour qu'il continue
                messages.append({"role": "assistant", "content": response.content})
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return last_text or "Je n'ai rien à ajouter."
            if response.stop_reason == "max_tokens":
                return (last_text + "\n\n" if last_text else "") + "(Réponse coupée avant la fin : reformule ou découpe ta demande.)"
            results = []
            for b in tool_uses:
                content, is_error = self.run_tool(b.name, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": content,
                                **({"is_error": True} if is_error else {})})
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
            self.ctx.step("Le réalisateur réfléchit à la suite", min(0.9, 0.3 + 0.05 * turn))
        return (last_text + "\n\n" if last_text else "") + "(Je me suis arrêté après beaucoup d'étapes : dis-moi si je dois continuer.)"


def talk(project_id: str, text: str, ctx) -> dict:
    """Tâche « chat » : enregistre le message, laisse le réalisateur regarder et agir, enregistre sa réponse."""
    from .. import pipeline
    text = text.strip()
    if not text:
        raise ValueError("Message vide.")
    mine = ChatMessage(role="monteur", text=text)
    project = storage.load_project(project_id)  # l'historique donné au modèle s'arrête avant ce message
    storage.update_project(project_id, lambda p: p.chat.append(mine))
    ctx.step("Le réalisateur relit le dossier des rushes", 0.1)
    agent = Agent(project_id, ctx)
    by, consigne = "local", None
    if config.claude_available():
        try:
            ctx.step("Le réalisateur répond (Claude)", 0.2)
            reply = agent.converse(project, text)
            by = "claude"
        except Exception as e:
            from ..jobs import JobCancelled
            if isinstance(e, JobCancelled):
                raise
            log.warning("Chat réalisateur Claude en échec : %s", e)
            reply = f"Je n'ai pas pu répondre ({e}). Réessaie, ou donne une consigne simple."
    elif project.timeline.clips:
        reply = ("Sans clé Claude (Réglages), je ne peux ni discuter ni regarder les images : j'applique ta demande "
                 "comme une consigne simple (durée, format, sous-titres, musique, zooms, couper un passage).")
        consigne = text
        try:
            res = pipeline.ai_command(project_id, text, pipeline._Scoped(ctx, 0.5, 0.95))
            agent.applied = True
            if res.get("message"):
                reply += f"\n\n{res['message']}"
        except Exception as e:
            log.warning("Consigne du chat non appliquée : %s", e)
            reply += f"\n\n(La consigne n'a pas pu être appliquée : {e})"
    else:
        reply = ("Sans clé Claude (Réglages), je ne peux pas discuter du projet. Lance d'abord « Monter "
                 "automatiquement », puis donne-moi des consignes simples : durée, format, sous-titres, musique.")
    answer = ChatMessage(role="realisateur", text=reply, consigne=consigne, applied=agent.applied, by=by,
                         actions=agent.actions[:30], sources=agent.sources[:8])
    storage.update_project(project_id, lambda p: p.chat.append(answer))
    ctx.preview("")
    ctx.step("Réponse du réalisateur", 1.0)
    return {"message": answer.model_dump(), "applied": agent.applied}
