"""Style « Edit PvP » : montage gaming rythmé à partir des coups détectés dans le son.

1. Les coups sont regroupés en rafales au rythme d'un combo (0,5 s entre deux coups), hors menus. Avec une
   clé Claude, un jury regarde les images des meilleures et ne garde que celles à l'avantage du joueur.
   On garde les meilleures jusqu'à la durée cible.
2. Chaque combo devient un plan, plus un ralenti sur le coup final (velocity edit).
3. Les durées sont calées sur la grille de temps de la musique : chaque coupe tombe sur un temps.
4. Habillage : secousses 3D, étincelles, flashs, zoom choc sur chaque coup ; transitions 3D entre
   les combos ; textes 3D « COMBO xN » ; bruitages ; musique au tempo (générée si aucune n'est choisie).
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path

import numpy as np

from .. import config
from ..analysis import hits as hits_an
from ..models import Clip, EditSettings, ImpactItem, MusicSettings, SfxItem, TextItem, Timeline
from . import jury

DEFAULT_BPM = 128
TRANSITION_CYCLE = ["cube_3d", "flash", "flip_3d", "glitch", "porte_3d", "zoom_avant"]
SLOW = 0.4  # vitesse du ralenti sur le coup final
CLOSING_QUIET = 6.0  # secondes sans coup après une rafale : le combat est fini


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

def candidates(media: list, hits_by_media: dict[str, list], blocked: dict[str, list] | None = None) -> list[tuple[str, dict]]:
    """Rafales candidates de tous les rushes, hors menus. Si aucun coup ne s'enchaîne au rythme d'un combo
    (jeu plus lent, coups espacés), on se rabat sur les séries de coups rapprochés, bornées à quelques secondes."""
    clean = {m.id: hits_an.outside(hits_by_media.get(m.id) or [], (blocked or {}).get(m.id) or []) for m in media}
    out = [(mid, c) for mid, hits in clean.items() for c in hits_an.bursts(hits)]
    if not out:
        out = [(mid, c) for mid, hits in clean.items() for c in hits_an.bursts(hits, max_gap=1.6, min_hits=3)]
    for mid, c in out:
        # Dernière rafale d'un combat : presque plus de coups pendant plusieurs secondes après (un clic de menu
        # isolé ne compte pas). C'est là que se joue l'issue, kill ou mort : le jury la regarde en premier.
        c["closing"] = sum(1 for h in clean[mid] if c["end"] < h[0] <= c["end"] + CLOSING_QUIET) <= 1
    return out


def shortlist(cands: list[tuple[str, dict]], limit: int, known: set | None = None, span: float = 25.0) -> list[tuple[str, dict]]:
    """Les rafales à montrer au jury : la meilleure de chaque passage d'abord, puis les suivantes.

    Classées au seul score, les quatorze premières viendraient souvent du même duel. Ici on fait un tour par
    tranche de 25 s de rush, pour couvrir tous les combats. `known` : rafales déjà jugées, à ne pas reprendre."""
    groups: dict[tuple[str, int], list[dict]] = {}
    for mid, c in cands:
        if known and (mid, jury.key(c)) in known:
            continue
        groups.setdefault((mid, int(c["start"] // span)), []).append(c)
    for g in groups.values():
        g.sort(key=lambda c: -c["score"])
    # La moitié des places va aux fins de combat (l'issue s'y voit), les plus longues d'abord.
    closing = sorted(((k[0], c) for k, g in groups.items() for c in g if c.get("closing")), key=lambda x: -x[1]["score"])
    out: list[tuple[str, dict]] = closing[: limit // 2]
    taken = {(mid, jury.key(c)) for mid, c in out}
    rank = 0
    while len(out) < limit and any(len(g) > rank for g in groups.values()):
        row = [(k[0], g[rank]) for k, g in groups.items() if len(g) > rank and (k[0], jury.key(g[rank])) not in taken]
        row.sort(key=lambda x: -x[1]["score"])
        out += row[: limit - len(out)]
        rank += 1
    return out


def _worth(v: dict) -> float:
    """Valeur d'une rafale jugée : la note, un bonus pour un kill, un autre quand le joueur domine nettement."""
    return v["note"] + (2.0 if v.get("fin") == "kill" else 0.0) + (1.0 if v["verdict"] == "joueur_domine" else 0.0)


def rank_candidates(cands: list[tuple[str, dict]], verdicts: dict | None, target: float) -> tuple[list, dict, int]:
    """Ordre de préférence des rafales. Renvoie (classées, raisons d'écart par (média, clé), nombre de vérifiées).

    Avec des verdicts : d'abord les rafales à l'avantage du joueur, par valeur ; celles que le jury écarte ne
    reviennent jamais. Les rafales non regardées ne complètent que si les vérifiées n'atteignent pas la moitié
    de la durée cible (ou s'il n'y en a aucune)."""
    verdicts = verdicts or {}
    good, unseen, why = [], [], {}
    for mid, c in cands:
        v = verdicts.get((mid, jury.key(c)))
        if v is None:
            unseen.append((mid, c))
        elif v["verdict"] in jury.GOOD and v.get("fin") != "mort" and v["note"] >= 3:
            good.append((mid, c, v))
        else:
            why[(mid, jury.key(c))] = f"Écarté par le jury IA : {jury.label(v)}."
    good.sort(key=lambda x: -(_worth(x[2]) + 0.001 * x[1]["score"]))
    unseen.sort(key=lambda x: -x[1]["score"])
    ranked = [(mid, c) for mid, c, _ in good]
    checked = len(ranked)
    span = sum(c["end"] - c["start"] + 0.9 for _, c in ranked)
    if not ranked or span < 0.5 * target:
        ranked += unseen
    else:
        for mid, c in unseen:
            why[(mid, jury.key(c))] = "Écarté : pas regardé par le jury, assez de combos vérifiés."
    return ranked, why, checked


def _title(title: str) -> str:
    """Titre affiché en ouverture. Un projet créé depuis un enregistrement porte son nom de fichier
    (« 2026-11-07 14-50-54 ») : ce n'est pas un titre, on en met un vrai."""
    t = re.sub(r"\.\w{2,4}$", "", (title or "").strip())
    if not t or sum(ch.isdigit() for ch in t) * 2 >= len(t):
        return "HIGHLIGHTS PVP"
    return t.upper()[:18]


def _plan_combo(c: dict, media_id: str, duration: float, beat: float, slow: bool = True) -> list[Clip]:
    """Plans d'un combo. `slow` : ralenti sur le coup final (réservé au meilleur combo et aux kills, sinon tous
    les combos ont la même forme et plus rien ne ressort)."""
    hits = c["hits"]
    last = hits[-1][0]
    s0 = max(0.0, hits[0][0] - 0.55)
    clips: list[Clip] = []
    if c["n"] >= 4 and slow:
        slow_in, slow_out = max(s0 + 0.4, last - 0.3), min(duration, last + 0.5)
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
          music_path: str | None = None, verdicts: dict | None = None, blocked: dict[str, list] | None = None) -> Timeline:
    """`verdicts` : avis du jury par (média, clé de rafale), voir `jury.judge`. `blocked` : menus par média."""
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

    all_combos = candidates(media, hits_by_media, blocked)
    tl = Timeline(format=settings.format, captions="aucun",
                  music=MusicSettings(track=music_path, volume=0.5, ducking=False, start=skip))
    tl.director = "pvp"
    if not all_combos:
        tl.summary = "Aucun combat détecté (pas de série de coups dans le son)."
        return tl

    ranked, why, checked = rank_candidates(all_combos, verdicts, target)

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
    for k, (mid, c) in enumerate(chosen):
        part = _plan_combo(c, mid, durations.get(mid, c["end"] + 2), beat, slow=(mid, c) == best or _kill(mid, c))
        v = verdicts.get((mid, jury.key(c)))
        if v:
            part[0].reason += f" Jury IA : {jury.label(v)}."
        elif verdicts:
            part[0].reason += " Pas regardé par le jury IA."
        if k > 0:
            part[0].transition = TRANSITION_CYCLE[(k - 1) % len(TRANSITION_CYCLE)]
            part[0].transition_duration = round(min(0.55, beat), 3)
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
    for gi, (mid, c, part) in enumerate(groups):
        n = c["n"]
        # Le grand final (flash, onde, zoom, boom) est réservé au meilleur combo et aux kills : posé sur chaque
        # combo, il fait de l'edit une suite de boucles identiques où plus rien ne ressort.
        big = (mid, c) == best or _kill(mid, c)
        for hi, (ht, strength) in enumerate(c["hits"]):
            tt = _to_timeline_time(clips, starts, mid, ht)
            if tt is None:
                continue
            final = hi == n - 1
            # Secousse douce sur chaque coup, franche sur le coup final seulement.
            A = round(0.35 + 0.5 * strength, 2)
            impacts.append(ImpactItem(start=round(tt, 3), kind="secousse", intensity=(1.6 if big else 1.1) if final else A,
                                      reason="Coup détecté dans le son."))
            if strength > 0.9 or final:
                impacts.append(ImpactItem(start=round(tt, 3), kind="etincelles", intensity=(1.6 if big else 1.0) if final else A,
                                          x=0.5, y=0.47))
            if final and big:
                impacts += [ImpactItem(start=round(tt, 3), kind="flash", intensity=1.0),
                            ImpactItem(start=round(tt, 3), kind="onde", intensity=1.0, x=0.5, y=0.5),
                            ImpactItem(start=round(tt, 3), kind="zoom", intensity=1.2)]
                sfx.append(SfxItem(start=round(tt, 3), kind="boom", volume=0.7))
            elif final:
                impacts.append(ImpactItem(start=round(tt, 3), kind="zoom", intensity=0.8))
            elif hi % 3 == 2:
                impacts.append(ImpactItem(start=round(tt, 3), kind="zoom", intensity=0.6))
        if len(part) > 1:
            k0 = clips.index(part[1])
            texts.append(TextItem(start=round(starts[k0], 3), duration=round(min(2.2, part[1].duration), 3),
                                  text=f"COMBO x{n}", style="texte_3d", position="haut"))
        elif n >= 6:  # combo sans ralenti : le compteur arrive sur ses derniers coups
            k0 = clips.index(part[0])
            d = min(1.4, part[0].duration * 0.5)
            texts.append(TextItem(start=round(starts[k0] + part[0].duration - d, 3), duration=round(d, 3),
                                  text=f"COMBO x{n}", style="texte_3d", position="haut"))
        if (mid, c) == best and len(part) > 1:
            k1 = clips.index(part[-1])
            texts.append(TextItem(start=round(starts[k1] + part[-1].duration * 0.55, 3),
                                  duration=round(max(0.8, part[-1].duration * 0.45), 3),
                                  text="ÉPIQUE !", style="texte_3d", position="centre"))
        k = clips.index(part[0])
        if k > 0:
            sfx.append(SfxItem(start=round(max(0.0, starts[k] - 0.25), 3), kind="whoosh", volume=0.5))
    # Pulsation au rythme : petit zoom sur chaque début de mesure loin des coups.
    hit_times = sorted(i.start for i in impacts)
    bar = 4 * beat
    b = 0.0  # la musique démarre sur un temps (voir `skip`)
    while b < total:
        j = int(np.searchsorted(hit_times, b))
        near = any(abs(b - hit_times[x]) < 0.3 for x in (j - 1, j) if 0 <= x < len(hit_times))
        if not near and b > 0.5:
            impacts.append(ImpactItem(start=round(b, 3), kind="zoom", intensity=0.35, reason="Temps fort de la musique."))
        b += bar
    impacts.sort(key=lambda i: i.start)
    title_txt = _title(title)
    texts.insert(0, TextItem(start=0.15, duration=min(1.8, clips[0].duration), text=title_txt, style="texte_3d",
                             position="centre"))
    tl.impacts = impacts
    tl.texts = texts
    tl.sfx = sfx
    tl.look = "vif"
    tl.title = title_txt
    tl.summary = (f"Edit PvP : {len(groups)} combos sur {len(all_combos)} rafales, {sum(c['n'] for _, c, _ in groups)} coups "
                  f"synchronisés, coupes calées sur le tempo ({bpm:.0f} BPM), durée {int(total // 60)}:{total % 60:04.1f}.")
    seen = [v for (m, k), v in verdicts.items() if any(m == mid and k == jury.key(c) for mid, c in all_combos)]
    if seen:
        mine = sum(v["verdict"] == "joueur_domine" for v in seen)
        theirs = sum(v["verdict"] == "adversaire_domine" or v.get("fin") == "mort" for v in seen)
        tl.summary += (f" Jury IA : {len(seen)} rafales regardées (à ton avantage : {mine}, subies : {theirs}, "
                       f"kills : {sum(v.get('fin') == 'kill' for v in seen)}).")
        if not checked:
            tl.summary += " Aucune rafale regardée n'est à ton avantage : montage fait avec des rafales non vérifiées."
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
