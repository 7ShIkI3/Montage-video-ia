"""Jury des combos : Claude regarde les rafales de coups candidates et dit à qui elles profitent.

Le son donne l'instant de chaque coup, pas son auteur : une rafale parfaite peut être l'adversaire qui enchaîne
le joueur jusqu'à sa mort. Aucune mesure locale ne tranche (les cartes à sol rouge noient le flash de l'adversaire
touché). Ici, quelques images de chaque rafale sont montrées à Claude, qui rend un verdict : le joueur domine,
c'est un échange, l'adversaire domine, ou ce n'est pas un combat (menu, lobby). Le montage PvP ne garde que les
rafales à l'avantage du joueur.

Le montage ne montre que les combos que le joueur DONNE. Le jury ne voit donc que les rafales que la mesure
locale (`analysis/duel.py` : vie du joueur, roulis de la caméra) n'a pas déjà écartées, avec en plus de chaque
rafale un recadrage zoomé de la barre de vie, avant puis après : c'est là que se voit qui enchaîne qui.

Un seul appel par montage, borné (au plus 14 rafales, 4 images et 2 recadrages de la vie chacune). Les verdicts
sont gardés avec les signaux du rush : refaire le montage ne repaie pas.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .. import config, storage
from ..analysis import duel
from ..models import Media
from . import vision

log = logging.getLogger(__name__)

MAX_CANDIDATES = 14  # rafales jugées par montage
WIDTH = 768  # largeur des images (≈ 450 tokens chacune en 16:9)
AFTER = 0.9  # secondes après le dernier coup : l'image qui montre l'issue (kill, mort, fuite)
BEFORE_LIFE = 0.4  # secondes avant le premier coup : le recadrage de la vie qui sert de point de départ
LIFE_WIDTH = 640  # largeur du recadrage de la barre de vie
VERDICTS = ["joueur_domine", "echange", "adversaire_domine", "hors_combat"]
ENDS = ["kill", "mort", "rien"]
GOOD = ("joueur_domine", "echange")

SYSTEM = (
    "Tu es le jury d'un montage de highlights de combat, filmé à la première personne (souvent du PvP Minecraft). "
    "Le logiciel a repéré au son des rafales de coups, mais le son ne dit pas qui frappe. Pour chaque rafale "
    "candidate, tu reçois quelques images prises juste après un coup, puis une image prise peu après le dernier "
    "coup. Dis à qui profite la rafale, du point de vue du joueur qui filme.\n"
    "Le montage ne garde QUE les combos que le joueur enchaîne sur l'adversaire : un combo où il se fait enchaîner "
    "n'est jamais un bon moment, même spectaculaire. Chaque candidat comporte aussi deux recadrages zoomés de la "
    "barre de vie du joueur (cœurs, armure, faim), l'un juste avant la rafale, l'autre peu après le dernier coup : "
    "compare-les. Des cœurs qui disparaissent, un contour blanc qui clignote, une potion de soin lancée en urgence "
    "ou une vie qui finit à 1-3 cœurs disent que le joueur encaisse. Une vie qui tient pendant que l'adversaire "
    "est entouré de particules dit qu'il donne. « echange » : les deux se frappent et le joueur perd peu de vie "
    "(un cœur ou deux au plus) ; s'il en perd nettement plus qu'il n'en inflige (3 cœurs ou plus, ou une vie qui "
    "finit très basse), ce n'est pas un échange mais « adversaire_domine », sauf si la rafale se termine par la mort de l'adversaire (fin « kill ») : le kill prime sur la vie "
    "perdue, c'est un bon moment du joueur. Quand la vie ne bouge pas (mode sans "
    "dégâts comme le Boxing), regarde la caméra qui penche d'environ 15 degrés quand le joueur est touché, et le "
    "compteur « Hits » du panneau latéral s'il est lisible (lui contre l'adversaire).\n"
    "Indices que le joueur porte ses coups : l'adversaire est au centre, près du viseur, entouré d'un nuage de "
    "particules (étoiles, losanges, éclats) ou teinté de rouge, il recule ou décolle ; la barre de vie du joueur "
    "reste stable. Indices que le joueur encaisse : grosses particules tout près de la caméra et pas sur "
    "l'adversaire, caméra qui bascule, adversaire hors champ ou dans le dos, barre de vie du joueur qui baisse, "
    "inventaire ou potions ouverts en urgence. Un menu, un lobby, un écran de fin ou un inventaire ouvert pendant "
    "toute la rafale : hors combat.\n"
    "Issue : « kill » si l'adversaire meurt (message d'élimination au nom de l'adversaire, écran de victoire, "
    "adversaire disparu avec butin), « mort » si c'est le joueur qui meurt (écran de mort, message disant qu'il a "
    "été tué, perdant affiché), sinon « rien ».\n"
    "Note de 0 à 10 : l'intérêt de la rafale dans un montage à la gloire du joueur. Une longue série de coups "
    "nets du joueur qui finit par un kill vaut 9 ou 10 ; un échange peu coûteux 4 à 6 ; une rafale subie ou un échange où le joueur perd plus de vie qu'il n'en prend 0 à 2 ; "
    "un passage hors combat 0. Ne devine pas : si les images ne permettent pas de trancher, choisis « echange » "
    "avec une note moyenne et dis-le dans la raison. La raison tient en une phrase courte, en français."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "candidats": {"type": "array", "items": {"type": "object", "properties": {
            "index": {"type": "integer"}, "verdict": {"type": "string", "enum": VERDICTS},
            "fin": {"type": "string", "enum": ENDS}, "note": {"type": "integer"}, "raison": {"type": "string"}},
            "required": ["index", "verdict", "fin", "note", "raison"], "additionalProperties": False}},
    },
    "required": ["candidats"], "additionalProperties": False,
}


def life_times(c: dict, duration: float) -> list[tuple[float, str]]:
    """Instants des recadrages de la barre de vie : juste avant la rafale, puis peu après le dernier coup."""
    hits = c["hits"]
    return [(max(0.0, hits[0][0] - BEFORE_LIFE), "avant la rafale"),
            (min(max(0.0, duration - 0.05), hits[-1][0] + AFTER), f"{AFTER:.1f} s après le dernier coup".replace(".", ","))]


def key(c: dict) -> str:
    """Identifiant stable d'une rafale dans un rush : ses bornes au centième."""
    return f"{c['start']:.2f}-{c['end']:.2f}"


def frame_times(c: dict, duration: float) -> list[tuple[float, str]]:
    """Instants à montrer : juste après le premier coup, un coup du milieu, le dernier, puis l'issue."""
    hits = c["hits"]
    picks = [(0, "premier coup"), (len(hits) // 2, "coup du milieu"), (len(hits) - 1, "dernier coup")]
    out = [(min(duration, hits[i][0] + 0.12), label) for i, label in picks]
    out.append((min(max(0.0, duration - 0.05), hits[-1][0] + AFTER), f"{AFTER:.1f} s après le dernier coup".replace(".", ",")))
    return out


def cached(project_id: str, media_id: str) -> dict:
    """Verdicts déjà rendus pour ce rush : {clé de rafale: verdict}."""
    return dict(((storage.load_signals(project_id, media_id) or {}).get("jury") or {}).get("verdicts") or {})


def skipped(project_id: str, media_id: str) -> set[str]:
    """Rafales déjà montrées au jury sans qu'il rende de verdict : on ne les renvoie pas à chaque montage."""
    return set(((storage.load_signals(project_id, media_id) or {}).get("jury") or {}).get("skipped") or [])


def judge(project_id: str, candidates: list[tuple[Media, dict]], ctx=None) -> dict[tuple[str, str], dict]:
    """Fait juger les rafales pas encore vues et renvoie tous les verdicts connus : {(média, clé): verdict}.

    Sans clé Claude, ou si l'appel échoue, renvoie seulement les verdicts déjà en cache (le montage se rabat
    sur le classement local).
    """
    known: dict[tuple[str, str], dict] = {}
    for mid in {m.id for m, _ in candidates}:
        for k, v in cached(project_id, mid).items():
            known[(mid, k)] = v
    passed = {(mid, k) for mid in {m.id for m, _ in candidates} for k in skipped(project_id, mid)}
    todo = [(m, c) for m, c in candidates if (m.id, key(c)) not in known and (m.id, key(c)) not in passed][:MAX_CANDIDATES]
    if not todo or not config.claude_available():
        return known
    import anthropic
    content: list = []
    sent: list[tuple[Media, dict]] = []
    images = 0
    for m, c in todo:
        if ctx:
            ctx.check()
        src = m.proxy if m.proxy and Path(m.proxy).exists() else m.path
        blocks: list = []
        for t, label in frame_times(c, m.duration):
            data = vision.jpeg(src, t, width=WIDTH)
            if data:
                blocks += [{"type": "text", "text": f"{t:.1f} s ({label})"}, vision.image_block(data)]
        if len(blocks) < 4:  # moins de deux images lisibles : on ne demande pas un verdict à l'aveugle
            continue
        zone = duel.zone_vie(project_id, m)
        if zone:  # recadrage zoomé de la barre de vie : de quoi voir qui enchaîne qui
            for t, label in life_times(c, m.duration):
                # Le recadrage est agrandi plusieurs fois : il se lit dans le rush d'origine, pas dans le proxy compressé
                # (à 540p, un cœur ne fait que 9 pixels).
                data = vision.jpeg(m.path, t, zone=zone, width=LIFE_WIDTH)
                if data:
                    blocks += [{"type": "text", "text": f"{t:.1f} s : barre de vie du joueur, recadrage zoomé ({label})"},
                               vision.image_block(data)]
        content.append({"type": "text", "text": (
            f"Candidat {len(sent)} — rafale de {c['n']} coups en {c['end'] - c['start']:.1f} s, "
            f"de {c['start']:.1f} s à {c['end']:.1f} s du rush « {m.filename} ».")
            + (f" Mesure locale (indicative) : {c['duel']['raison']}." if (c.get("duel") or {}).get("vie") else "")
            + (" Dernière rafale de son combat, que la mesure donne pour subie : dis surtout si le joueur TUE l'adversaire "
               "à la fin (fin « kill »), sinon c'est sa défaite." if duel.a_confirmer(c.get("duel")) else "")})
        content += blocks
        images += len(blocks) // 2
        sent.append((m, c))
    if not sent:
        return known
    content.append({"type": "text", "text": f"Rends un verdict pour chacun des {len(sent)} candidats, dans l'ordre."})
    if ctx:
        ctx.step(f"Jury IA : {len(sent)} rafales regardées ({images} images)", 0.87, stage="reperage")
    r = anthropic.Anthropic().beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=6000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if r.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Jury interrompu ({r.stop_reason}).")
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    fresh: dict[str, dict[str, dict]] = {}
    for row in data.get("candidats", []):
        i = row.get("index")
        if not isinstance(i, int) or not 0 <= i < len(sent) or row.get("verdict") not in VERDICTS:
            continue
        m, c = sent[i]
        v = {"verdict": row["verdict"], "fin": row.get("fin") if row.get("fin") in ENDS else "rien",
             "note": max(0, min(10, int(row.get("note") or 0))), "raison": (row.get("raison") or "").strip().rstrip(".")[:240],
             "n": c["n"]}
        fresh.setdefault(m.id, {})[key(c)] = v
        known[(m.id, key(c))] = v
    silent: dict[str, list[str]] = {}  # envoyées, restées sans réponse : notées pour ne pas les repayer
    for m, c in sent:
        if (m.id, key(c)) not in known:
            silent.setdefault(m.id, []).append(key(c))
    for mid in set(fresh) | set(silent):
        sig = storage.load_signals(project_id, mid) or {}  # relu : l'analyse a pu écrire entre-temps
        jury = sig.get("jury") or {}
        jury = {"model": getattr(r, "model", config.CLAUDE_MODEL), "at": time.time(),
                "verdicts": {**(jury.get("verdicts") or {}), **fresh.get(mid, {})},
                "skipped": sorted(set(jury.get("skipped") or []) | set(silent.get(mid, [])))}
        sig["jury"] = jury
        storage.save_signals(project_id, mid, sig)
    log.info("Jury : %d verdicts rendus sur %d rafales envoyées (%d images)", sum(len(v) for v in fresh.values()), len(sent), images)
    return known


def label(v: dict) -> str:
    """Le verdict en clair, pour la raison d'un plan et le dossier."""
    who = {"joueur_domine": "à ton avantage", "echange": "échange de coups", "adversaire_domine": "l'adversaire domine",
           "hors_combat": "hors combat"}[v["verdict"]]
    end = {"kill": ", kill", "mort": ", tu meurs", "rien": ""}[v.get("fin", "rien")]
    return f"{who}{end}, {v['note']}/10" + (f" — {v['raison']}" if v.get("raison") else "")
