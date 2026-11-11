"""Style « Edit PvP » : montage gaming rythmé à partir des coups détectés dans le son.

1. Les coups sont regroupés en rafales au rythme d'un combo (0,5 s entre deux coups), hors menus. Le son ne dit
   pas qui frappe : deux mesures locales sur l'image (vie du joueur, roulis de la caméra, voir `analysis/duel.py`)
   écartent d'emblée les rafales que le joueur subit et classent d'abord celles qu'il donne. Avec une clé Claude,
   un jury regarde les images des meilleures restantes et ne garde que celles à l'avantage du joueur. On garde les
   meilleures jusqu'à la durée cible, sans remplir avec des combos subis : s'il manque de bons combos, le montage
   est plus court et son résumé le dit.
2. Chaque combo devient un plan, plus un ralenti sur le coup final (velocity edit).
3. Les durées sont calées sur la grille de temps de la musique : chaque coupe tombe sur un temps.
4. Habillage sobre, pour que le final ressorte : une secousse sur le coup final de chaque combo et sur son
   coup le plus fort, des coupes franches sur le temps, une seule carte « COMBO xN » (le meilleur, 1,6 s au plus, avec
   le nombre de coups montrés), un aperçu du moment fort en accroche ; musique au tempo (générée si aucune n'est choisie).
   La pile complète (ralenti, contraste, secousse, étincelles, flash, onde, zoom, boom) sert un seul moment, le kill s'il
   y en a un, sinon le meilleur combo ; l'autre ralenti reçoit une variante légère. Le pic de la musique se pose sur le
   kill, sinon sur une coupe.
5. Ce qu'on monte d'une rafale est borné : jamais un passage où la vie mesurée du joueur chute de plus d'un cœur (un kill
   gardé sans preuve que le joueur le donne ne montre que ses derniers coups, puis son ralenti), et un plan de combo,
   ralenti compris, tient dans `PLAN_MAX` (à `SPAN_MIN_RALENTI` de coups près) : un combo long est réduit à sa portion
   la plus dense, qui démarre sur un coup.
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path

import numpy as np

from .. import config
from ..analysis import duel as duel_an
from ..analysis import hits as hits_an
from ..models import Clip, EditSettings, ImpactItem, MusicSettings, SfxItem, TextItem, Timeline
from . import jury

DEFAULT_BPM = 128
CUT = "flash"  # la seule transition de l'edit, à l'entrée du meilleur combo ; ailleurs, coupes franches sur le temps
SHAKES_PER_COMBO = 2  # coup final compris
HOLD_BEST = 0.9  # secondes gardées après le coup final du moment fort : le temps de le voir
HOLD_KILL = 0.55  # ... après un kill : l'adversaire disparaît aussitôt, au-delà on ne filme plus qu'une arène vide
SLOW = 0.4  # vitesse du ralenti sur le coup final du moment fort (kill, sinon meilleur combo)
SLOW_LEGER = 0.6  # ralenti des autres temps forts : plus doux, pour que la pile complète ne serve qu'une fois
LEAD = 0.2  # secondes avant le premier coup d'un plan : un plan démarre sur un coup, pas sur un temps mort
LEAD_MAX = 0.3  # ... et même calé sur les temps, le premier coup montré arrive moins de 0,3 s après le début (si le tempo le permet)
TAIL = 0.45  # secondes gardées après le coup final d'un plan sans ralenti
PLAN_MAX = 4.5  # durée maximale d'un plan de combo, ralenti compris : au-delà, on en garde la portion la plus dense
SPAN_MAX = PLAN_MAX - LEAD - TAIL  # étendue maximale des coups d'un plan sans ralenti
SPAN_MIN_RALENTI = 1.5  # étendue des coups gardée au moins avant un ralenti, même si celui-ci mange le plafond (3,3 s à 128 BPM)
KILL_LEAD = 1.0  # secondes de combat gardées avant un kill dont rien ne prouve que le joueur le donne
PRE_TEMPS = 2  # temps de musique, au ralenti, avant le coup final : il tombe pile sur un temps
COMBO_CARD_MAX = 1.6  # durée maximale de la carte « COMBO xN »
CLOSING_QUIET = 6.0  # secondes sans coup après une rafale : le combat est fini
FIGHT_MIN_HITS = 12  # coups rapprochés à partir desquels une série est un vrai combat, avec une issue à regarder


# ---------------------------------------------------------------------------
# Musique générée (libre de droits) : rythme électro au tempo voulu
# ---------------------------------------------------------------------------

def beat_track(bpm: int = DEFAULT_BPM, bars: int = 48, sr: int = 44100) -> Path:
    out = config.DATA_DIR / "music_gen" / f"beat_pvp_{bpm}.wav"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    beat = 60 / bpm
    n = int(bars * 4 * beat * sr)
    L = np.zeros(n, np.float32)
    rng = np.random.default_rng(1)

    def put(t, sig, gain=1.0):
        i = int(t * sr)
        j = min(n, i + sig.size)
        if i < n:
            L[i:j] += sig[: j - i] * gain

    def tt(d):
        return np.arange(int(d * sr)) / sr

    t1 = tt(0.45)
    kick = np.sin(2 * np.pi * (48 + 110 * np.exp(-28 * t1)) * t1) * np.exp(-6.5 * t1)
    t2 = tt(0.22)
    clap = rng.normal(0, 1, t2.size) * np.exp(-22 * t2)
    clap = np.convolve(clap, [1, -0.6], "same")
    t3 = tt(0.05)
    hat = np.diff(np.concatenate([[0], rng.normal(0, 1, t3.size)])) * np.exp(-90 * t3)
    notes = [55.0, 55.0, 65.41, 49.0]  # La, La, Do, Sol (basse)
    for bar in range(bars):
        t0 = bar * 4 * beat
        root = notes[bar % 4]
        for k in range(4):
            put(t0 + k * beat, kick, 0.9)
            if k in (1, 3):
                put(t0 + k * beat, clap, 0.35)
        if bar % 2 == 1:
            put(t0 + 3.5 * beat, kick, 0.6)
        for k in range(8):
            put(t0 + k * beat / 2 + beat / 4, hat, 0.12 if k % 2 else 0.18)
        for k in range(8):  # basse en croches, décalée du kick
            tb = tt(beat / 2 * 0.9)
            env = np.minimum(1, tb * 80) * np.exp(-3 * tb)
            put(t0 + k * beat / 2, np.tanh(2.5 * np.sin(2 * np.pi * root * tb)) * env, 0.22)
        if bar % 4 == 0:  # nappe d'accord
            tc = tt(4 * beat)
            chord = sum(np.sin(2 * np.pi * root * 4 * r * tc) for r in (1, 1.189, 1.498))
            put(t0, chord * np.minimum(1, tc * 3) * np.exp(-0.6 * tc), 0.05)
    L = np.tanh(L * 1.1) * 0.85
    pcm = (np.stack([L, L], axis=1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return out


def music_grid(path: str | None) -> tuple[float, float]:
    """(bpm, premier temps) de la musique ; tempo par défaut si inconnue."""
    if not path:
        return float(DEFAULT_BPM), 0.0
    name = Path(path).name
    if name.startswith("beat_pvp_"):
        return float(name.split("_")[-1].split(".")[0]), 0.0
    try:
        from ..media import ffmpeg
        pcm = ffmpeg.decode_audio(path)[: config.AUDIO_SR * 60]
        return hits_an.beat_grid(pcm, config.AUDIO_SR)
    except Exception:
        return float(DEFAULT_BPM), 0.0


# ---------------------------------------------------------------------------
# Montage
# ---------------------------------------------------------------------------

def annotate(cands: list[tuple[str, dict]], duel: dict | None) -> list[tuple[str, dict]]:
    """Pose sur chaque rafale le résultat du signal local « qui porte le combo ? » (`analysis/duel.py`), clé
    `duel` : None si la rafale n'a pas été mesurée. Une rafale non mesurée n'est jamais écartée. `fermeture` :
    dernière rafale de son combat (le kill éventuel, voir `duel.a_confirmer`)."""
    for mid, c in cands:
        res = (duel or {}).get((mid, jury.key(c)))
        c["duel"] = {**res, "fermeture": bool(c.get("closing"))} if res else None
    return cands


def candidates(media: list, hits_by_media: dict[str, list], blocked: dict[str, list] | None = None,
               duel: dict | None = None) -> list[tuple[str, dict]]:
    """Rafales candidates de tous les rushes, hors menus. Si aucun coup ne s'enchaîne au rythme d'un combo
    (jeu plus lent, coups espacés), on se rabat sur les séries de coups rapprochés, bornées à quelques secondes.
    `duel` : résultats du signal local par (média, clé de rafale), posés sur chaque rafale (clé `duel`)."""
    clean = {m.id: hits_an.outside(hits_by_media.get(m.id) or [], (blocked or {}).get(m.id) or []) for m in media}
    # Le repli se décide rush par rush : un rush au rythme lent garde ses séries même si un autre a de vrais combos
    # (et ses rafales gardent les mêmes bornes d'un montage à l'autre, donc les verdicts du jury restent valables).
    out = [(mid, c) for mid, hits in clean.items()
           for c in (hits_an.bursts(hits) or hits_an.bursts(hits, max_gap=1.6, min_hits=3))]
    for mid, c in out:
        # Dernière rafale d'un combat : presque plus de coups pendant plusieurs secondes après (un clic de menu
        # isolé ne compte pas). C'est là que se joue l'issue, kill ou mort : le jury la regarde en premier.
        c["closing"] = sum(1 for h in clean[mid] if c["end"] < h[0] <= c["end"] + CLOSING_QUIET) <= 1
        c["fight"] = 0
    # Un vrai duel finit souvent dans le bruit (message de victoire, feux d'artifice, clics) : le silence ne suffit
    # pas à repérer sa fin. La dernière rafale de chaque combat nourri en est une aussi, et on retient la taille
    # du combat : les plus gros duels sont montrés au jury d'abord.
    for mid, hits in clean.items():
        mine = [c for m2, c in out if m2 == mid]
        for g in hits_an.combos(hits):
            if g["n"] < FIGHT_MIN_HITS:
                continue
            inside = [c for c in mine if g["start"] - 1e-6 <= c["start"] and c["end"] <= g["end"] + 1e-6]
            if inside:
                last = max(inside, key=lambda c: c["end"])
                last["closing"], last["fight"] = True, g["n"]
    return annotate(out, duel)


def shortlist(cands: list[tuple[str, dict]], limit: int, known: set | None = None, span: float = 25.0) -> list[tuple[str, dict]]:
    """Les rafales à montrer au jury : la meilleure de chaque passage d'abord, puis les suivantes.

    Classées au seul score, les quatorze premières viendraient souvent du même duel. Ici on fait un tour par
    tranche de 25 s de rush, pour couvrir tous les combats. `known` : rafales déjà jugées, à ne pas reprendre.
    Le jury est payant : il ne voit jamais une rafale que le signal local a écartée (combo subi, échange coûteux)
    et regarde d'abord celles que le joueur donne."""
    groups: dict[tuple[str, int], list[dict]] = {}
    for mid, c in cands:
        if known and (mid, jury.key(c)) in known:
            continue
        if not duel_an.admissible(c.get("duel")):
            continue
        groups.setdefault((mid, int(c["start"] // span)), []).append(c)
    for g in groups.values():
        g.sort(key=lambda c: (duel_an.rang(c.get("duel")), -c["score"]))
    # La plus grande part des places va aux fins de combat (c'est là que se voient les kills), gros duels d'abord.
    closing = sorted(((k[0], c) for k, g in groups.items() for c in g if c.get("closing")),
                     key=lambda x: (duel_an.rang(x[1].get("duel")), -x[1].get("fight", 0), -x[1]["score"]))
    out: list[tuple[str, dict]] = closing[: limit * 7 // 10]
    taken = {(mid, jury.key(c)) for mid, c in out}
    rank = 0
    while len(out) < limit and any(len(g) > rank for g in groups.values()):
        row = [(k[0], g[rank]) for k, g in groups.items() if len(g) > rank and (k[0], jury.key(g[rank])) not in taken]
        row.sort(key=lambda x: (duel_an.rang(x[1].get("duel")), -x[1]["score"]))
        out += row[: limit - len(out)]
        rank += 1
    return out


def match_requested(cands: list[tuple[str, dict]], wanted: list[tuple[str, float, float]]) -> tuple[set, list[tuple[str, float, float]]]:
    """Rafales désignées par (média, début, fin en secondes du rush), par exemple celles que le réalisateur du chat a
    choisies. Une demande retient chaque rafale dont elle recouvre au moins la moitié (ou une seconde) ; un passage
    qui contient plusieurs rafales les retient toutes. Renvoie (clés retenues, demandes sans aucune rafale)."""
    keys: set = set()
    lost = []
    for mid, a, b in wanted:
        hit = False
        for m2, c in cands:
            if m2 != mid:
                continue
            common = min(b, c["end"]) - max(a, c["start"])
            if common >= min(1.0, 0.5 * max(c["end"] - c["start"], 0.1)):
                keys.add((m2, jury.key(c)))
                hit = True
        if not hit:
            lost.append((mid, a, b))
    return keys, lost


def _worth(v: dict) -> float:
    """Valeur d'une rafale jugée : la note, un bonus pour un kill, un autre quand le joueur domine nettement."""
    return v["note"] + (2.0 if v.get("fin") == "kill" else 0.0) + (1.0 if v["verdict"] == "joueur_domine" else 0.0)


def rank_candidates(cands: list[tuple[str, dict]], verdicts: dict | None, target: float) -> tuple[list, dict, int]:
    """Ordre de préférence des rafales. Renvoie (classées, raisons d'écart par (média, clé), nombre de vérifiées).

    Une rafale que le signal local donne pour subie (ou pour un échange coûteux) est écartée avant tout : le jury
    ne la sauve pas. Seule exception, la dernière rafale d'un combat (le kill du duel gagné : la vie y a chuté) : elle
    n'entre que si le jury y voit le kill du joueur. Ensuite, avec des verdicts : d'abord les rafales que le joueur donne (confirmées par le signal
    local), puis les autres à son avantage, par valeur ; celles que le jury écarte ne reviennent jamais. Les
    rafales non regardées ne complètent que si les vérifiées n'atteignent pas la moitié de la durée cible (ou s'il
    n'y en a aucune), sauf celles que le signal local voit « donne » : une preuve positive suffit à les garder."""
    verdicts = verdicts or {}
    good, unseen, why = [], [], {}
    for mid, c in cands:
        d = c.get("duel")
        if not duel_an.admissible(d):
            why[(mid, jury.key(c))] = duel_an.pourquoi(d)
            continue
        v = verdicts.get((mid, jury.key(c)))
        if duel_an.a_confirmer(d):
            # Dernière rafale d'un combat que la mesure donne pour subie : elle n'entre que si le jury voit le joueur tuer
            # l'adversaire. Sans verdict, ou sans kill, c'est (très probablement) sa défaite : écartée.
            if v and v.get("fin") == "kill" and v.get("verdict") != "hors_combat":
                good.append((mid, c, v))
            else:
                why[(mid, jury.key(c))] = ("Écarté : tu subissais cette fin de combat et le jury n'y voit pas ton kill."
                                           if v else "Écarté : tu subissais probablement cette fin de combat, et "
                                           "aucun kill de ta part n'est confirmé (jury non consulté).")
            continue
        if v is None:
            unseen.append((mid, c))
        elif v["verdict"] in jury.GOOD and v.get("fin") != "mort" and v["note"] >= 3:
            good.append((mid, c, v))
        else:
            why[(mid, jury.key(c))] = f"Écarté par le jury IA : {jury.label(v)}."
    good.sort(key=lambda x: (duel_an.rang(x[1].get("duel")), -(_worth(x[2]) + 0.001 * x[1]["score"])))
    unseen.sort(key=lambda x: (duel_an.rang(x[1].get("duel")), -x[1]["score"]))
    ranked = [(mid, c) for mid, c, _ in good]
    checked = len(ranked)
    span = sum(c["end"] - c["start"] + 0.9 for _, c in ranked)
    if not ranked or span < 0.5 * target:
        ranked += unseen
    else:
        for mid, c in unseen:
            if duel_an.rang(c.get("duel")) <= 1:
                ranked.append((mid, c))
            else:
                why[(mid, jury.key(c))] = "Écarté : pas regardé par le jury, assez de combos vérifiés."
    return ranked, why, checked


def _title(title: str) -> str:
    """Titre affiché en ouverture. Un projet créé depuis un enregistrement porte son nom de fichier
    (« 2026-11-07 14-50-54 ») : ce n'est pas un titre, on en met un vrai."""
    t = re.sub(r"\.\w{2,4}$", "", (title or "").strip())
    if not t or sum(ch.isdigit() for ch in t) * 2 >= len(t):
        return "HIGHLIGHTS PVP"
    return t.upper()[:18]


def _music_peak(path: str | None) -> tuple[float, float] | None:
    """(instant du pic d'énergie du morceau, sa durée), ou None pour le rythme généré ou un morceau illisible."""
    if not path or Path(path).name.startswith("beat_pvp_"):
        return None
    try:
        from . import soundsheet
        prof = soundsheet.music_profile(path)
    except Exception:
        return None
    return (prof["peak"], prof["duration"]) if prof else None


def _plus_dense(hits: list[list[float]], span_max: float) -> list[list[float]]:
    """La suite de coups consécutifs tenant dans `span_max` secondes qui en compte le plus (puis la plus forte, puis la
    plus tardive : le coup final est là)."""
    best: list[list[float]] = hits[:1]
    best_key = None
    for i in range(len(hits)):
        j = i
        while j + 1 < len(hits) and hits[j + 1][0] - hits[i][0] <= span_max + 1e-9:
            j += 1
        sel = hits[i:j + 1]
        key = (len(sel), round(sum(h[1] for h in sel), 3), sel[-1][0])
        if best_key is None or key > best_key:
            best, best_key = sel, key
    return best


def _portion(c: dict, kill: bool, finale: bool, beat: float, span_max: float = SPAN_MAX) -> dict:
    """Ce qu'on monte d'une rafale : une vue de `c` (mêmes clés) dont `hits` ne garde que les coups montrés, plus `lo` et
    `hi`, les bornes (secondes du rush) hors desquelles on ne coupe jamais. Le dernier coup est toujours gardé.

    - La vie du joueur : le plan reste dans le passage où elle ne chute pas de plus d'un cœur (`duel.fenetre_sans_chute`).
    - Un kill dont rien ne prouve que le joueur le donne (`sauvee` : la mesure dit « subit » ou « echange », ou, faute de
      courbe de vie, elle ne dit pas « donne ») : seulement les tout derniers coups, environ la dernière seconde calée sur
      le tempo, jamais la raclée qui précède.
    - Un combo trop long pour un plan (`span_max`, étendue des coups) : sa portion la plus dense, de préférence là où la
      caméra ne voit aucun coup reçu (les bornes se resserrent alors sur cette fenêtre) ; un kill garde, lui, la fin de
      cette portion (son coup final). `finale` : le ralenti suit le dernier coup montré."""
    res = c.get("duel")
    hits = c["hits"]
    last = hits[-1][0]
    lo, hi = 0.0, math.inf
    fen = duel_an.fenetre_sans_chute(res, last)
    if fen:
        lo, hi = min(fen[0], last), max(fen[1], last + 0.05)  # le coup final reste dans les bornes
    pool = [h for h in hits if h[0] >= lo - 1e-6]
    label = (res or {}).get("label")
    sauvee = kill and (label in ("subit", "echange") or (not fen and label != "donne"))
    if sauvee:
        depuis = last - max(1, int(KILL_LEAD / beat + 1e-6)) * beat  # au plus KILL_LEAD de combat avant le coup final
        pool = [h for h in pool if h[0] >= depuis - 1e-6]
        lo = max(lo, depuis - 0.05)
    pool = pool or hits[-1:]
    if pool[-1][0] - pool[0][0] > span_max:
        cam = (res or {}).get("fenetre")
        if cam and not kill:
            calme = [h for h in pool if cam[0] - 1e-6 <= h[0] <= cam[1] + duel_an.DECALAGE_AVANT]
            if len(calme) >= duel_an.COUPS_MIN:
                pool = calme
                # Après la fenêtre vient un coup reçu : ni le plan ni son ralenti ne vont plus loin que l'impulsion qui la ferme.
                hi = min(hi, max(cam[1] + duel_an.DECALAGE_AVANT, pool[-1][0] + 0.05))
        if pool[-1][0] - pool[0][0] > span_max:
            pool = [h for h in pool if h[0] >= pool[-1][0] - span_max - 1e-9] if kill else _plus_dense(pool, span_max)
    return {**c, "hits": pool, "n": len(pool), "start": pool[0][0], "end": pool[-1][0], "lo": lo, "hi": hi,
            "rogne": len(pool) < len(hits), "sauvee": sauvee}


def _caler_fin(s0: float, out: float, lo: float, hi: float, beat: float, last: float) -> tuple[float, float]:
    """Amène [s0, out] à un nombre entier de temps (au moins deux) : on allonge ou raccourcit d'abord la fin, puis, si
    elle ne peut pas aller plus loin, le début (jamais au-delà de [lo, hi]). Le premier coup garde sa place. Sans place
    pour allonger, on raccourcit par le début (au pire les tout premiers coups sortent du plan) : la durée reste un
    nombre entier de temps, sinon les coupes suivantes sortent du tempo."""
    d = out - s0
    k = max(2, round(d / beat))
    if k * beat < d - 1e-6 and s0 + k * beat < last + 0.1:  # raccourcir couperait le coup final
        k += 1
    want = k * beat
    if want < d - 1e-6:
        return s0, s0 + want
    extra = want - d
    e = min(extra, max(0.0, hi - out))
    out, extra = out + e, extra - e
    e = min(extra, max(0.0, s0 - lo))
    s0, extra = s0 - e, extra - e
    if extra > 1e-6:  # pas la place d'allonger jusqu'au temps suivant : un temps de moins, pris sur le début
        k = max(1, int((out - s0) / beat + 1e-6))
        s0 = out - k * beat
    return s0, out


def _ralenti_temps(beat: float, vitesse: float, hold: float) -> float:
    """Durée (s dans le montage) du ralenti d'un coup final dans les cas courants : `PRE_TEMPS` temps avant, puis `hold` secondes de rush."""
    return (PRE_TEMPS + max(1, round(hold / (beat * vitesse)))) * beat


def _plan(v: dict, media_id: str, duration: float, beat: float, vitesse: float | None = None, hold: float = 0.5,
          contraste: bool = True) -> list[Clip]:
    """Plans d'un combo, `v` venant de `_portion`. Le plan démarre `LEAD` avant son premier coup et dure un nombre entier de
    temps. `vitesse` : ralenti sur le coup final (réservé au moment fort, aux kills et au meilleur combo, sinon tous
    les combos ont la même forme et plus rien ne ressort) ; le coup final tombe alors sur un temps de la musique, et le
    ralenti dure un nombre entier de temps, même quand le rush manque après le coup final (il prend alors du rush avant).
    `hold` : secondes (de rush) gardées après le coup final. Rien n'est jamais coupé hors de [v["lo"], v["hi"]]."""
    hits = v["hits"]
    first, last = hits[0][0], hits[-1][0]
    lo, hi = max(0.0, v["lo"]), min(duration, v["hi"])
    n = v["n"]
    s0 = max(first - LEAD, lo)
    if vitesse is None:
        out = min(last + TAIL, hi)
        s0, out = _caler_fin(s0, out, lo, hi, beat, last)
        return [Clip(media_id=media_id, src_in=round(s0, 3), src_out=round(out, 3), score=v["score"],
                     reason=f"Combo de {n} coups." if n >= 4 else f"Échange de {n} coups.")]
    unit = beat * vitesse  # secondes de rush par temps de la musique, au ralenti
    pre = max(0, min(PRE_TEMPS, int((last - lo) / unit + 1e-6)))
    post = max(0, min(max(1, round(hold / unit)), int((hi - last) / unit + 1e-6)))
    if post:
        slow_in, slow_out = last - pre * unit, last + post * unit
    else:
        # Presque plus de rush après le coup final (la vie du joueur chute, ou fin du fichier) : le ralenti finit là et on
        # complète par du rush avant, pour que sa durée reste un nombre entier de temps (sinon toutes les coupes suivantes glissent).
        slow_out = max(last, min(hi, last + 0.15))
        k = pre + math.ceil((slow_out - last) / unit - 1e-9)
        while k >= 1 and slow_out - k * unit < lo - 1e-6:
            k -= 1
        if k < 1 or k * unit - (slow_out - last) < 0.02:  # le coup final ne tiendrait pas dans le ralenti : plan sans ralenti
            return _plan(v, media_id, duration, beat, None, hold, contraste)
        slow_in = slow_out - k * unit
    slow = Clip(media_id=media_id, src_in=round(slow_in, 3), src_out=round(slow_out, 3), speed=vitesse, score=v["score"],
                reason="Ralenti sur le coup final.", effects=["contraste"] if contraste else [])
    if slow_in - s0 < 0.3:
        return [slow]
    # Le plan précédant le ralenti dure un nombre entier de temps et finit sur lui : seul son début peut bouger. On cherche
    # parmi les premiers coups celui qui tombe à moins de LEAD_MAX du début ; à défaut, on arrondit au temps le plus proche.
    k = None
    for h in hits[:3]:
        if h[0] >= slow_in - 0.3:
            break
        kk = math.ceil((slow_in - h[0] + 0.05) / beat - 1e-9)
        if slow_in - kk * beat >= lo - 1e-6 and h[0] - (slow_in - kk * beat) <= LEAD_MAX:
            k = kk
            break
    if k is None:
        k = max(1, round((slow_in - s0) / beat))
        if slow_in - k * beat > first - 0.05:  # le premier coup serait coupé
            k += 1
        if slow_in - k * beat < lo:
            k = int((slow_in - lo) / beat + 1e-6)
    if k < 1:
        return [slow]
    main = Clip(media_id=media_id, src_in=round(slow_in - k * beat, 3), src_out=round(slow_in, 3), score=v["score"],
                reason=f"Combo de {n} coups.")
    return [main, slow]


def _montres(clips: list[Clip], hits: list[list[float]]) -> list[list[float]]:
    """Les coups réellement visibles dans ces plans."""
    return [h for h in hits if any(c.src_in - 1e-6 <= h[0] < c.src_out for c in clips)]


def _to_timeline_time(clips: list[Clip], starts: list[float], media_id: str, t: float) -> float | None:
    for c, s in zip(clips, starts):
        if c.media_id == media_id and c.src_in <= t < c.src_out:
            return s + c.src_to_local(t - c.src_in)
    return None


def build(media: list, hits_by_media: dict[str, list], settings: EditSettings, title: str = "",
          music_path: str | None = None, verdicts: dict | None = None, blocked: dict[str, list] | None = None,
          duel: dict | None = None, only: set | None = None) -> Timeline:
    """`verdicts` : avis du jury par (média, clé de rafale), voir `jury.judge`. `blocked` : menus par média.
    `duel` : signal local « qui porte le combo ? » par (média, clé), voir `analysis/duel.py`.
    `only` : si donné, seules ces rafales (clés de `match_requested`) peuvent entrer ; le refus des rafales subies
    s'applique quand même à celles-ci."""
    target = settings.target_duration or 30.0
    verdicts = verdicts or {}
    if not music_path:
        music_path = str(beat_track())
    bpm, first_beat = music_grid(music_path)
    beat = 60 / bpm
    # On saute le début du morceau jusqu'à un temps : la grille de la musique part alors de 0 dans le montage,
    # et les durées de combo (des nombres entiers de temps) tombent pile sur ses temps.
    skip = round(first_beat % beat, 3)
    durations = {m.id: m.duration for m in media}
    order = {m.id: i for i, m in enumerate(media)}

    all_combos = candidates(media, hits_by_media, blocked, duel)
    bilan = duel_an.bilan(all_combos)
    tl = Timeline(format=settings.format, captions="aucun",
                  music=MusicSettings(track=music_path, volume=0.5, ducking=False, start=skip))
    tl.director = "pvp"
    if not all_combos:
        tl.summary = "Aucun combat détecté (pas de série de coups dans le son)."
        return tl

    pool = all_combos if only is None else [(m, c) for m, c in all_combos if (m, jury.key(c)) in only]
    ranked, why, checked = rank_candidates(pool, verdicts, target)
    if only is not None:
        for m, c in all_combos:
            why.setdefault((m, jury.key(c)), "Non choisi pour cet edit.")

    def _kill(mid: str, c: dict) -> bool:
        return (verdicts.get((mid, jury.key(c))) or {}).get("fin") == "kill"

    # Par ordre de préférence strict : on ne saute pas une bonne rafale pour en caser une plus faible. La première
    # qui dépasse la durée cible entre encore si le dépassement reste raisonnable, puis on s'arrête. Chaque rafale est
    # d'abord réduite à ce qu'on peut en montrer (`_portion`) : une durée estimée sur la portion, pas sur la rafale.
    chosen: list[tuple[str, dict]] = []
    vues: dict[tuple[str, str], tuple[dict, bool, bool, bool]] = {}  # (portion, ralenti ?, kill ?, finale ?)
    total = 0.0
    for mid, c in ranked:
        kill = _kill(mid, c)
        finale = kill or not chosen
        v = _portion(c, kill, finale, beat)
        slow = finale and (v["n"] >= 4 or v["sauvee"] or kill)  # un kill se montre toujours au ralenti, même sur un seul coup
        est = (v["end"] - v["start"]) + 0.9 + (0.8 / SLOW if slow else 0)
        if total + est > target * (1.3 if chosen else 99):
            break
        chosen.append((mid, c))
        vues[(mid, jury.key(c))] = (v, slow, kill, finale)
        total += est
        if total > target * 1.1:
            break
    if not chosen:
        # Rien de montrable : mieux vaut le dire que monter des rafales subies.
        if only is not None:
            refused = sum(1 for m, c in pool if why.get((m, jury.key(c)), "").startswith("Écarté : tu "))
            tl.summary = (f"Aucune des {len(pool)} rafales choisies n'est montrable"
                          + (f" : {refused} sont écartées parce que tu les subissais (vie qui chute, coups reçus)." if refused
                             else " : le jury IA ne les trouve pas à ton avantage.")
                          if pool else "Aucune des rafales choisies ne correspond à un combo détecté dans les rushes.")
        elif bilan["ecartees"]:
            tl.summary = (f"Aucun combo à montrer : sur {len(all_combos)} rafales de coups, {bilan['ecartees']} sont écartées "
                          "parce que tu les subissais (vie qui chute, coups reçus), les autres n'ont pas convaincu le jury IA.")
        else:
            tl.summary = (f"Le jury IA a regardé {len(all_combos)} rafales de coups et n'en a trouvé aucune à ton avantage "
                          "dans ces rushes (combos subis ou hors combat).")
        tl.rejected = [Clip(media_id=m, src_in=c["start"], src_out=c["end"], score=0.0,
                            reason=why.get((m, jury.key(c)), "Écarté par le jury IA.")) for m, c in all_combos]
        return tl

    picked = {(mid, jury.key(c)) for mid, c in chosen}
    rejected = [(m, c) for m, c in all_combos if (m, jury.key(c)) not in picked]
    best = chosen[0]  # `ranked` est par ordre de préférence : le premier gardé est le meilleur
    # Le moment fort, seul à recevoir toute la pile d'effets : le kill s'il y en a un (le meilleur d'entre eux), sinon le
    # meilleur combo. Les autres ralentis (le meilleur combo, d'autres kills) reçoivent une variante légère.
    kills = [x for x in chosen if _kill(*x)]
    climax = kills[0] if kills else best
    hold_fort = HOLD_KILL if kills else HOLD_BEST
    second = next((x for x in chosen[1:] if x is not climax), None) if len(chosen) > 2 else None
    # Ordre du montage : un combo en accroche, les autres dans l'ordre du rush, le meilleur combo, puis le moment fort
    # pour la fin.
    middle = sorted((x for x in chosen if x is not best and x is not second and x is not climax),
                    key=lambda x: (order.get(x[0], 0), x[1]["start"]))
    chosen = ([second] if second else []) + middle + ([best] if best is not climax else []) + [climax]
    # Le plafond d'un plan vaut ralenti compris : on resserre les coups gardés avant le ralenti selon sa durée (kill : 3 s de
    # ralenti à 128 BPM, il ne reste que les derniers coups ; ralenti léger : presque tout le combo).
    for mid, c in chosen:
        v, slow, kill, finale = vues[(mid, jury.key(c))]
        if slow:
            vit, hold = (SLOW, hold_fort) if (mid, c) == climax else (SLOW_LEGER, 0.5)
            span = max(SPAN_MIN_RALENTI, PLAN_MAX - _ralenti_temps(beat, vit, hold))
            if span < v["end"] - v["start"] - 1e-6:
                v = _portion(c, kill, finale, beat, span)
        vues[(mid, jury.key(c))] = (v, slow, kill, finale)

    clips: list[Clip] = []
    groups: list[tuple[str, dict, dict, list[Clip], str]] = []  # (média, rafale, portion montrée, plans, rôle)
    # Accroche : une seconde du coup final du moment fort, en aperçu, avant de dérouler l'edit.
    teaser: Clip | None = None
    if len(chosen) >= 3:
        v = vues[(climax[0], jury.key(climax[1]))][0]
        last = v["hits"][-1][0]
        dur = durations.get(climax[0], last + 1)
        lo, hi = max(0.0, v["lo"]), min(dur, v["hi"])  # `lo` tient déjà un kill sans preuve à `KILL_LEAD` avant son coup final
        if kills:  # un kill : pas plus loin que ce que le ralenti final montrera (après, il n'y a plus d'adversaire à l'image),
            hi = min(hi, max(last + HOLD_KILL, lo + 2 * beat))  # sans descendre sous les deux temps qu'il faut à un aperçu
        d = min(max(3, round(1.2 / beat)) * beat, int((hi - lo) / beat + 1e-6) * beat)  # un nombre entier de temps, dans les bornes
        if d >= 2 * beat - 1e-6:
            a = max(lo, min(last - 0.75 * d, hi - d))
            teaser = Clip(media_id=climax[0], src_in=round(a, 3), src_out=round(a + d, 3), score=climax[1]["score"],
                          reason="Aperçu du moment fort, en accroche.")
            clips.append(teaser)
    for mid, c in chosen:
        v, slow, _, _ = vues[(mid, jury.key(c))]
        role = "climax" if (mid, c) == climax else ("leger" if slow else "")
        vitesse = (SLOW if role == "climax" else SLOW_LEGER) if slow else None
        part = _plan(v, mid, durations.get(mid, c["end"] + 2), beat, vitesse, hold=hold_fort if role == "climax" else 0.5,
                     contraste=role == "climax")
        shown = _montres(part, v["hits"])
        # La carte « COMBO xN » compte tout ce qui se voit : aussi les coups de la rafale hors de la portion (queue du plan, ralenti).
        v = {**v, "hits": shown or v["hits"], "n": len(shown) or v["n"], "vus": len(_montres(part, c["hits"])) or len(shown) or v["n"]}
        r = verdicts.get((mid, jury.key(c)))
        if c.get("duel"):
            part[0].reason += f" Signal local : {duel_an.label(c['duel'])}."
        if v["sauvee"]:
            part[0].reason += " Fin de combat gardée pour son kill : seuls les derniers coups sont montrés, jamais le passage où ta vie chute."
        elif v["rogne"]:
            part[0].reason += f" Portion la plus dense du combo : {v['n']} coups sur {c['n']}."
        if r:
            part[0].reason += f" Jury IA : {jury.label(r).rstrip('.')}."
        elif verdicts:
            part[0].reason += " Pas regardé par le jury IA."
        if clips and role == "climax":  # une transition à chaque raccord annonce chaque coupe : on la garde pour le moment fort
            part[0].transition = CUT
            part[0].transition_duration = round(min(0.3, beat), 3)
        clips += part
        groups.append((mid, c, v, part, role))
    clips[0].effects = ["3d_bascule"]
    top_score = max(c.score for c in clips) or 1.0
    for c in clips:  # score affiché entre 0 et 1, comme pour les autres styles
        c.score = round(c.score / top_score, 3)
    tl.clips = clips
    starts, t = [], 0.0
    for c in clips:
        starts.append(t)
        t += c.duration
    total = t
    body, body_starts = (clips[1:], starts[1:]) if teaser else (clips, starts)

    impacts: list[ImpactItem] = []
    texts: list[TextItem] = []
    sfx: list[SfxItem] = []
    quiet: tuple[float, float] | None = None  # respiration avant le moment fort : aucun effet dans cette fenêtre
    kill_at: float | None = None  # instant du montage où tombe le coup final du kill, moment fort
    for mid, c, v, part, role in groups:
        n = v["n"]
        full, light = role == "climax", role == "leger"
        # Secousses : le coup final, plus les coups les plus forts du combo s'ils en sont loin. Le reste se regarde
        # sans effet : une secousse sur chaque coup rend l'image illisible et l'effet ne veut plus rien dire.
        keep = {n - 1}
        for i in sorted(range(n - 1), key=lambda i: -v["hits"][i][1]):
            if len(keep) >= SHAKES_PER_COMBO:
                break
            if all(abs(v["hits"][i][0] - v["hits"][j][0]) >= 1.2 for j in keep):
                keep.add(i)
        final_at = None
        for hi in sorted(keep):
            ht, strength = v["hits"][hi][0], v["hits"][hi][1]
            tt = _to_timeline_time(body, body_starts, mid, ht)
            if tt is None:
                continue
            final = hi == n - 1
            if final:
                final_at = tt
            impacts.append(ImpactItem(start=round(tt, 3), kind="secousse",
                                      intensity=(1.6 if full else 1.0) if final else round(0.4 + 0.4 * strength, 2),
                                      reason="Coup détecté dans le son."))
            if final and full:
                # La pile complète (étincelles, flash, onde, zoom, boom, avec le ralenti à ×0,4 et le contraste) sert une seule fois.
                impacts.append(ImpactItem(start=round(tt, 3), kind="etincelles", intensity=1.6, x=0.5, y=0.47))
                impacts += [ImpactItem(start=round(tt, 3), kind="flash", intensity=1.0),
                            ImpactItem(start=round(tt, 3), kind="onde", intensity=1.0, x=0.5, y=0.5),
                            ImpactItem(start=round(tt, 3), kind="zoom", intensity=1.2)]
                sfx.append(SfxItem(start=round(tt, 3), kind="boom", volume=0.7))
            elif final and light:
                impacts.append(ImpactItem(start=round(tt, 3), kind="etincelles", intensity=0.7, x=0.5, y=0.47))
        k = clips.index(part[0])
        if full:
            if len(part) > 1:  # le ralenti suit les derniers coups : rien d'autre à l'écran juste avant
                quiet = (starts[clips.index(part[-1])] - 0.8, starts[clips.index(part[-1])])
            if _kill(mid, c):
                kill_at = final_at
            if k > 0:
                sfx.append(SfxItem(start=round(max(0.0, starts[k] - 0.25), 3), kind="whoosh", volume=0.5))
        if final_at is None:
            continue
        # Une seule carte par moment : « KILL » sur la fin d'un duel gardée pour son kill, sinon le compteur du meilleur
        # combo (jamais plus de COMBO_CARD_MAX s), avec le nombre de coups réellement montrés dans son plan. Le texte ne
        # déborde jamais de son plan : le ralenti léger laisse moins de temps après le coup final que le ralenti plein.
        fin_plan = starts[clips.index(part[-1])] + part[-1].duration
        if _kill(mid, c) and (v["sauvee"] or (mid, c) != best):
            texts.append(TextItem(start=round(final_at, 3),
                                  duration=round(min(max(0.6, min(1.2, total - final_at - 0.2)), fin_plan - final_at), 3),
                                  text="KILL", style="texte_3d", position="haut"))
        elif (mid, c) == best and len(part) > 1:
            at = max(starts[k] + 0.2, final_at - 0.6)
            texts.append(TextItem(start=round(at, 3),
                                  duration=round(min(max(0.6, min(COMBO_CARD_MAX, total - at - 0.3)), fin_plan - at), 3),
                                  text=f"COMBO x{v['vus']}", style="texte_3d", position="haut"))
    if quiet:
        impacts = [i for i in impacts if not quiet[0] <= i.start < quiet[1] - 0.01]
    # Pas de zoom « au rythme » entre les coups : un effet qui ne tombe sur aucun son se remarque comme un défaut.
    impacts.sort(key=lambda i: i.start)
    # Le pic de la musique (son « drop ») tombe sur le kill du montage, s'il y en a un ; sinon sur une coupe : le début du
    # dernier combo qu'il peut atteindre.
    peak_note = ""
    pk = _music_peak(music_path)
    if pk:
        peak = first_beat + round((pk[0] - first_beat) / beat) * beat  # sur la grille de temps du montage
        if kill_at is not None:
            start = skip + round((peak - kill_at - skip) / beat) * beat
            if start >= skip - 1e-6 and pk[1] - start >= total:
                tl.music.start = round(start, 3)
                peak_note = f" Pic de la musique posé sur le kill à {int(kill_at // 60)}:{kill_at % 60:04.1f}."
        if not peak_note:
            cuts = [starts[clips.index(part[0])] for _, _, _, part, _ in groups]
            reach = [x for x in cuts if peak - x >= skip - 1e-6]
            if reach:
                start = skip + max(0, round((peak - max(reach) - skip) / beat)) * beat
                if pk[1] - start >= total:  # il reste assez de morceau pour tout l'edit
                    tl.music.start = round(start, 3)
                    at = max(reach)
                    peak_note = f" Pic de la musique posé sur la coupe à {int(at // 60)}:{at % 60:04.1f}."
    title_txt = _title(title)
    if teaser:
        # L'aperçu parle de lui-même : annoncer le compte dès l'ouverture dévoilerait le final. Seul un vrai titre
        # de projet s'y affiche, le temps du plan.
        if title_txt != "HIGHLIGHTS PVP":
            texts.insert(0, TextItem(start=0.1, duration=round(max(0.6, teaser.duration - 0.2), 3), text=title_txt,
                                     style="texte_3d", position="centre"))
    else:
        texts.insert(0, TextItem(start=0.15, duration=min(1.8, clips[0].duration), text=title_txt, style="texte_3d",
                                 position="centre"))
    tl.impacts = impacts
    tl.texts = texts
    tl.sfx = sfx
    tl.look = "vif"
    tl.title = title_txt
    tl.summary = (f"Edit PvP : {len(groups)} combos sur {len(all_combos)} rafales, {sum(v['n'] for _, _, v, _, _ in groups)} coups "
                  f"synchronisés, coupes calées sur le tempo ({bpm:.0f} BPM), durée {int(total // 60)}:{total % 60:04.1f}.{peak_note}")
    if any(v["sauvee"] for _, _, v, _, _ in groups):
        tl.summary += " Kill gardé sans la raclée : seuls ses derniers coups sont montrés, jamais le passage où ta vie chute."
    measured = any(c.get("duel") for _, c in all_combos)
    if measured:
        sauvees = sum(duel_an.a_confirmer(c.get("duel")) for _, c, _, _, _ in groups)  # fins de combat confirmées par le jury
        bilan["ecartees"] -= sauvees
        bilan["subies"] -= sauvees
        n = bilan["ecartees"]
        tl.summary += (f" Signal local (vie et caméra) : {bilan['donne']} où tu enchaînes, {bilan['echange']} échanges peu coûteux, "
                       f"{bilan['inconnu']} non tranchées" + ("." if not n else
                       f" ; {n} rafale{'s' if n > 1 else ''} écartée{'s' if n > 1 else ''} parce que tu {'les ' if n > 1 else 'la '}"
                       "subissais" + (f" ou y perdais trop de vie ({bilan['subies']} subie{'s' if bilan['subies'] > 1 else ''} nettement)."
                                      if n > bilan["subies"] else ".")))
        if total < 0.8 * target and n:
            tl.summary += (f" Montage plus court que la cible ({total:.0f} s sur {target:.0f} s) : il n'y a pas assez de combos "
                           "que tu donnes, et les combos subis ne servent pas de remplissage.")
    seen = [v for (m, k), v in verdicts.items() if any(m == mid and k == jury.key(c) for mid, c in all_combos)]
    if seen:
        mine = sum(v["verdict"] == "joueur_domine" for v in seen)
        theirs = sum(v["verdict"] == "adversaire_domine" or v.get("fin") == "mort" for v in seen)
        tl.summary += (f" Jury IA : {len(seen)} rafales regardées (à ton avantage : {mine}, subies : {theirs}, "
                       f"kills : {sum(v.get('fin') == 'kill' for v in seen)}).")
        if not checked:
            tl.summary += " Aucune rafale regardée n'est à ton avantage : montage fait avec des rafales non vérifiées."
    elif measured:
        tl.summary += " Sans jury IA : tri fait sur la vie et la caméra seulement."
    else:
        tl.summary += " Sans jury IA, les rafales sont classées au son : l'app ne sait pas qui porte les coups."
    top = max((c["score"] for _, c in all_combos), default=1.0) or 1.0
    tl.rejected = [Clip(media_id=m, src_in=c["start"], src_out=c["end"], score=round(min(1.0, c["score"] / top), 3),
                        reason=why.get((m, jury.key(c)),
                                       f"Écarté : rafale de {c['n']} coups moins forte, durée cible atteinte."))
                   for m, c in rejected]
    if not settings.auto_effects:
        # « Habillage auto » décoché : on garde les combos, le ralenti et le calage sur le tempo, sans décor.
        for c in tl.clips:
            c.effects, c.transition = [], "aucune"
        tl.impacts, tl.texts, tl.sfx, tl.look = [], [], [], "aucun"
        tl.summary += " Habillage auto désactivé : aucun effet ajouté."
    return tl
