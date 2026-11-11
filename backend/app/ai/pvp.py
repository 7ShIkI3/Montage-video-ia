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
   coup le plus fort, des coupes franches sur le temps (un seul flash, à l'entrée du meilleur combo), une seule
   carte « COMBO xN » (le meilleur), un aperçu du meilleur combo en accroche, le pic de la musique posé sur
   une coupe ; musique au tempo (générée si aucune n'est choisie).
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
HOLD_BEST = 0.9  # secondes gardées après le coup final du meilleur combo : le temps de le voir
SLOW = 0.4  # vitesse du ralenti sur le coup final
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


def _plan_combo(c: dict, media_id: str, duration: float, beat: float, slow: bool = True, hold: float = 0.5) -> list[Clip]:
    """Plans d'un combo. `slow` : ralenti sur le coup final (réservé au meilleur combo et aux kills, sinon tous
    les combos ont la même forme et plus rien ne ressort). `hold` : secondes gardées après le coup final."""
    hits = c["hits"]
    last = hits[-1][0]
    s0 = max(0.0, hits[0][0] - 0.55)
    clips: list[Clip] = []
    if c["n"] >= 4 and slow:
        slow_in, slow_out = max(s0 + 0.4, last - 0.3), min(duration, last + hold)
        main = Clip(media_id=media_id, src_in=round(s0, 3), src_out=round(slow_in, 3), score=c["score"],
                    reason=f"Combo de {c['n']} coups.")
        slow = Clip(media_id=media_id, src_in=round(slow_in, 3), src_out=round(slow_out, 3), speed=SLOW,
                    score=c["score"], reason="Ralenti sur le coup final.", effects=["contraste"])
        clips = [main, slow]
    else:
        clips = [Clip(media_id=media_id, src_in=round(s0, 3), src_out=round(min(duration, last + 0.45), 3),
                      score=c["score"], reason=f"Combo de {c['n']} coups." if c["n"] >= 4 else f"Échange de {c['n']} coups.")]
    # Calage sur le tempo : la durée totale du combo devient un nombre entier de temps.
    D = sum(x.duration for x in clips)
    want = max(2, round(D / beat)) * beat
    first = clips[0]
    first.src_in = round(max(0.0, first.src_in - (want - D)), 3)
    D2 = sum(x.duration for x in clips)
    if abs(D2 - want) > 1e-3:  # début de fichier atteint : on ajuste la fin
        tail = clips[-1]
        tail.src_out = round(tail.src_out + (want - D2) * tail.speed, 3)
    return clips


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
    if not ranked:
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

    def _kill(mid: str, c: dict) -> bool:
        return (verdicts.get((mid, jury.key(c))) or {}).get("fin") == "kill"

    # Par ordre de préférence strict : on ne saute pas une bonne rafale pour en caser une plus faible. La première
    # qui dépasse la durée cible entre encore si le dépassement reste raisonnable, puis on s'arrête.
    chosen, total = [], 0.0
    for mid, c in ranked:
        est = (c["end"] - c["start"]) + 0.9 + (0.8 / SLOW if c["n"] >= 4 and (not chosen or _kill(mid, c)) else 0)
        if total + est > target * (1.3 if chosen else 99):
            break
        chosen.append((mid, c))
        total += est
        if total > target * 1.1:
            break
    picked = {(mid, jury.key(c)) for mid, c in chosen}
    rejected = [(m, c) for m, c in all_combos if (m, jury.key(c)) not in picked]
    best = chosen[0]  # `ranked` est par ordre de préférence : le premier gardé est le meilleur
    second = chosen[1] if len(chosen) > 2 else None
    # Ordre du montage : le deuxième meilleur combo en accroche, les autres dans l'ordre du rush, le meilleur
    # pour la fin.
    middle = sorted((x for x in chosen if x is not best and x is not second), key=lambda x: (order.get(x[0], 0), x[1]["start"]))
    chosen = ([second] if second else []) + middle + [best]

    clips: list[Clip] = []
    groups: list[tuple[str, dict, list[Clip]]] = []
    # Accroche : une seconde du coup final du meilleur combo, en aperçu, avant de dérouler l'edit.
    teaser: Clip | None = None
    if len(chosen) >= 3:
        last = best[1]["hits"][-1][0]
        d = max(3, round(1.2 / beat)) * beat  # un nombre entier de temps, comme les combos
        a = max(0.0, min(last - 0.75 * d, durations.get(best[0], last + 1) - d))
        teaser = Clip(media_id=best[0], src_in=round(a, 3), src_out=round(a + d, 3), score=best[1]["score"],
                      reason="Aperçu du meilleur combo, en accroche.")
        clips.append(teaser)
    for mid, c in chosen:
        is_best = (mid, c) == best
        part = _plan_combo(c, mid, durations.get(mid, c["end"] + 2), beat, slow=is_best or _kill(mid, c),
                           hold=HOLD_BEST if is_best else 0.5)
        v = verdicts.get((mid, jury.key(c)))
        if c.get("duel"):
            part[0].reason += f" Signal local : {duel_an.label(c['duel'])}."
        if v:
            part[0].reason += f" Jury IA : {jury.label(v).rstrip('.')}."
        elif verdicts:
            part[0].reason += " Pas regardé par le jury IA."
        if clips and is_best:  # une transition à chaque raccord annonce chaque coupe : on la garde pour le final
            part[0].transition = CUT
            part[0].transition_duration = round(min(0.3, beat), 3)
        clips += part
        groups.append((mid, c, part))
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

    impacts: list[ImpactItem] = []
    texts: list[TextItem] = []
    sfx: list[SfxItem] = []
    quiet: tuple[float, float] | None = None  # respiration avant le final : aucun effet dans cette fenêtre
    for mid, c, part in groups:
        n = c["n"]
        is_best = (mid, c) == best
        # Le grand final (flash, onde, zoom, boom) est réservé au meilleur combo et aux kills.
        big = is_best or _kill(mid, c)
        # Secousses : le coup final, plus les coups les plus forts du combo s'ils en sont loin. Le reste se regarde
        # sans effet : une secousse sur chaque coup rend l'image illisible et l'effet ne veut plus rien dire.
        keep = {n - 1}
        for i in sorted(range(n - 1), key=lambda i: -c["hits"][i][1]):
            if len(keep) >= SHAKES_PER_COMBO:
                break
            if all(abs(c["hits"][i][0] - c["hits"][j][0]) >= 1.2 for j in keep):
                keep.add(i)
        for hi in sorted(keep):
            ht, strength = c["hits"][hi][0], c["hits"][hi][1]
            tt = _to_timeline_time(clips[1:] if teaser else clips, starts[1:] if teaser else starts, mid, ht)
            if tt is None:
                continue
            final = hi == n - 1
            impacts.append(ImpactItem(start=round(tt, 3), kind="secousse",
                                      intensity=(1.6 if big else 1.0) if final else round(0.4 + 0.4 * strength, 2),
                                      reason="Coup détecté dans le son."))
            if final and big:
                impacts.append(ImpactItem(start=round(tt, 3), kind="etincelles", intensity=1.6, x=0.5, y=0.47))
            if final and big:
                impacts += [ImpactItem(start=round(tt, 3), kind="flash", intensity=1.0),
                            ImpactItem(start=round(tt, 3), kind="onde", intensity=1.0, x=0.5, y=0.5),
                            ImpactItem(start=round(tt, 3), kind="zoom", intensity=1.2)]
                sfx.append(SfxItem(start=round(tt, 3), kind="boom", volume=0.7))
        k = clips.index(part[0])
        if is_best:
            if len(part) > 1:  # une seule carte dans l'edit : le compteur du meilleur combo, sur son ralenti
                k0 = clips.index(part[1])
                quiet = (starts[k0] - 0.8, starts[k0])
                # La carte arrive sur les derniers coups du combo, avant le ralenti : on voit ce qu'elle compte.
                at = max(starts[k] + 0.2, starts[k0] - 1.2)
                texts.append(TextItem(start=round(at, 3), duration=round(max(0.6, min(3.2, total - at - 0.3)), 3),
                                      text=f"COMBO x{n}", style="texte_3d", position="haut"))
            if k > 0:
                sfx.append(SfxItem(start=round(max(0.0, starts[k] - 0.25), 3), kind="whoosh", volume=0.5))
        elif _kill(mid, c):
            tt = _to_timeline_time(clips[1:] if teaser else clips, starts[1:] if teaser else starts, mid, c["hits"][-1][0])
            if tt is not None:
                texts.append(TextItem(start=round(tt, 3), duration=round(max(0.6, min(1.2, total - tt - 0.2)), 3),
                                      text="KILL", style="texte_3d", position="haut"))
    if quiet:
        impacts = [i for i in impacts if not quiet[0] <= i.start < quiet[1] - 0.01]
    # Pas de zoom « au rythme » entre les coups : un effet qui ne tombe sur aucun son se remarque comme un défaut.
    impacts.sort(key=lambda i: i.start)
    # Le pic de la musique (son « drop ») tombe sur une coupe : le début du dernier combo qu'il peut atteindre.
    peak_note = ""
    pk = _music_peak(music_path)
    if pk:
        peak = first_beat + round((pk[0] - first_beat) / beat) * beat  # sur la grille de temps du montage
        cuts = [starts[clips.index(part[0])] for _, _, part in groups]
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
    tl.summary = (f"Edit PvP : {len(groups)} combos sur {len(all_combos)} rafales, {sum(c['n'] for _, c, _ in groups)} coups "
                  f"synchronisés, coupes calées sur le tempo ({bpm:.0f} BPM), durée {int(total // 60)}:{total % 60:04.1f}.{peak_note}")
    measured = any(c.get("duel") for _, c in all_combos)
    if measured:
        sauvees = sum(duel_an.a_confirmer(c.get("duel")) for _, c, _ in groups)  # fins de combat confirmées par le jury
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
