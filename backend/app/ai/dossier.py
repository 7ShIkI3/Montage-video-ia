"""Dossier du projet : ce que l'app sait des rushes, en texte, pour le réalisateur IA et la recherche.

Sans ce dossier, l'IA ne « voit » pas les rushes et répond à côté (« je ne peux pas savoir quels combos tu as
gagnés »). Avec, elle a des horodatages : coups détectés au son, combos, écrans rouges (coups encaissés
probables), changements de plan, phrases transcrites, ce que Claude a vu dans les images (si la passe de vision
a été faite), montage actuel, repères, commentaires, notes de recherche. Le dossier dit aussi ce qui n'a pas été
mesuré, pour que l'IA réponde « probable » ou aille regarder, plutôt qu'inventer.
"""
from __future__ import annotations

from .. import storage
from ..analysis import hits as hits_an
from ..models import Project, Transcript

LIMITS_BLIND = (
    "Limites de la mesure : pour les rushes sans analyse visuelle, l'app n'a pas interprété les images (pas de "
    "lecture du chat ou des messages de kill, pas d'identification des joueurs, pas de score ni de barre de vie). "
    "Un combo = coups rapprochés entendus au son ; un écran rouge = probablement un coup encaissé par le joueur "
    "qui filme. Déductions à présenter comme probables, avec les horodatages."
)
LIMITS_TOOLS = (
    "Ce dossier vient de mesures (son, mouvement) et, quand elle a été faite, d'une analyse visuelle par "
    "échantillons. Pour tout ce qu'il ne dit pas (issue d'un combat, texte à l'écran, ce qu'on voit à un instant "
    "précis), regarde les images avec l'outil voir_images avant d'affirmer."
)


def mmss(t: float) -> str:
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def _phrases(tr: Transcript, gap: float = 0.8, max_len: int = 110) -> list[tuple[float, str]]:
    """Phrases (début, texte) reconstituées à partir des mots horodatés."""
    out: list[tuple[float, str]] = []
    cur: list[str] = []
    start = 0.0
    last_end = 0.0
    for w in tr.words:
        if cur and (w.start - last_end > gap or len(" ".join(cur)) > max_len):
            out.append((start, " ".join(cur)))
            cur = []
        if not cur:
            start = w.start
        cur.append(w.text)
        last_end = w.end
    if cur:
        out.append((start, " ".join(cur)))
    return out


def media_section(project: Project, m, max_phrases: int = 30, max_seen: int = 40) -> list[str]:
    lines = [f"- {m.filename} : {mmss(m.duration)} ({m.width}x{m.height}, {m.fps:.0f} i/s)"
             + ("" if m.analyzed else " — pas encore analysé")]
    sig = storage.load_signals(project.id, m.id) or {}
    hits = sig.get("hits") or []
    if hits:
        cb = hits_an.combos(hits)
        lines.append(f"  Coups détectés au son : {len(hits)}" + (f" ; combats (coups rapprochés) : {len(cb)}" if cb else ""))
        for c in sorted(cb, key=lambda c: -c["score"])[:8]:
            lines.append(f"  · combat {mmss(c['start'])} → {mmss(c['end'])} : {c['n']} coups")
        # Les rafales sont les vrais combos (coups à 0,5 s d'écart). Le son ne dit pas qui frappe : seul le jury
        # (images regardées par Claude lors d'un montage PvP) le sait.
        from ..analysis import video as video_an
        from . import jury, pvp
        seen = (sig.get("jury") or {}).get("verdicts") or {}
        v0 = sig.get("video") or {}
        menus = video_an.overlays(v0.get("brightness") or [], v0.get("fps") or 5)
        # Mêmes rafales que le montage PvP (hors menus) : leurs clés sont celles des verdicts du jury.
        bursts = sorted((c for _, c in pvp.candidates([m], {m.id: hits}, {m.id: menus})),
                        key=lambda c: (jury.key(c) not in seen, -c["score"]))
        if bursts:
            lines.append(f"  Rafales au rythme d'un combo : {len(bursts)}"
                         + (f", dont {len(seen)} jugées sur images" if seen else " (auteur des coups inconnu sans images)"))
        for c in bursts[:14]:
            v = seen.get(jury.key(c))
            lines.append(f"  · combo {mmss(c['start'])} → {mmss(c['end'])} : {c['n']} coups"
                         + (f" — jury : {jury.label(v)}" if v else ""))
    video = sig.get("video") or {}
    if sig and m.has_video and "red_flashes" not in video:
        # Projet analysé avant l'ajout de cette mesure : on la calcule ici (quelques secondes) et on la garde.
        try:
            from ..analysis import video as video_an
            from ..media import ffmpeg
            video["red_flashes"] = video_an.red_flashes(ffmpeg.decode_rgb_frames(m.proxy or m.path))
            sig["video"] = video
            storage.save_signals(project.id, m.id, sig)
        except Exception:  # le dossier reste utile sans cette ligne
            video["red_flashes"] = []
    reds = video.get("red_flashes") or []
    if reds:
        lines.append("  Écrans rouges (coups encaissés probables) : " + ", ".join(mmss(t) for t in reds[:30])
                     + (f" … ({len(reds)} au total)" if len(reds) > 30 else ""))
    cuts = video.get("cuts") or []
    if cuts:
        lines.append(f"  Changements de plan dans le rush : {len(cuts)}")
    vision = sig.get("vision") or {}
    if vision.get("observations"):
        lines.append(f"  Vu dans les images (analyse visuelle de {vision.get('images', len(vision['observations']))} images) :"
                     + (f" {vision['resume']}" if vision.get("resume") else ""))
        for o in vision["observations"][:max_seen]:
            lines.append(f"  · {mmss(o['t'])} [{o['type']}] {o['vu']}" + (f" — texte : « {o['texte']} »" if o.get("texte") else ""))
    elif m.has_video:
        lines.append("  Images : pas encore regardées (aucune analyse visuelle de ce rush)")
    tr = storage.load_transcript(project.id, m.id)
    if tr and tr.words:
        ph = _phrases(tr)
        lines.append(f"  Paroles ({len(ph)} phrases{', ' + tr.language if tr.language else ''}) :")
        step = max(1, len(ph) // max_phrases)
        for t, text in ph[::step][:max_phrases]:
            lines.append(f"  · {mmss(t)} « {text} »")
    elif m.has_audio:
        lines.append("  Paroles : pas de transcription (pas de voix, ou Whisper indisponible)")
    return lines


def montage_section(project: Project, max_clips: int = 40) -> list[str]:
    """Le montage actuel, plan par plan (position sur la timeline ← source)."""
    tl = project.timeline
    names = {m.id: m.filename for m in project.media}
    if not tl.clips:
        return ["Montage actuel : aucun (pas encore monté)."]
    out = [f"Montage actuel : {len(tl.clips)} plans, {mmss(tl.duration)}"
           + (f", titre « {tl.title} »" if tl.title else "") + (f", réalisateur {tl.director}" if tl.director else "")]
    if tl.summary:
        out.append(f"Résumé du réalisateur : {tl.summary[:500]}")
    t = 0.0
    for k, c in enumerate(tl.clips[:max_clips], 1):
        fx = ", ".join(x for x in [f"×{c.speed}" if c.speed != 1 else "", *c.effects,
                                   "couleurs corrigées" if c.color and not c.color.neutral else ""] if x)
        out.append(f"  {k}. {mmss(t)} ← {names.get(c.media_id, c.media_id)} {mmss(c.src_in)}→{mmss(c.src_out)}"
                   + (f" ({fx})" if fx else ""))
        t += c.duration
    if len(tl.clips) > max_clips:
        out.append(f"  … {len(tl.clips) - max_clips} autres plans")
    if tl.texts:
        out.append("Textes : " + " | ".join(f"{mmss(x.start)} « {x.text[:60]} »" for x in tl.texts[:12]))
    mix = {"plans": tl.mix.rushes, "musique": tl.mix.music, "bruitages": tl.mix.sfx, "pistes": tl.mix.tracks}
    if any(ch.mute or abs(ch.volume - 1) > 1e-3 for ch in mix.values()):
        out.append("Mixage : " + ", ".join(f"{k} coupé" if ch.mute else f"{k} {ch.volume * 100:.0f} %" for k, ch in mix.items()))
    if tl.review:
        out.append(f"Note du relecteur : {tl.review.score:.1f}/10")
    if tl.quality:
        out.append(f"Contrôle qualité : {tl.quality.score}/100")
    return out


def project_dossier(project: Project, max_chars: int = 12000, tools: bool = False) -> str:
    """Le dossier complet, en français, borné en taille. `tools` : le lecteur peut aller regarder les images."""
    s = project.settings
    out = [f"Projet « {project.name} » — style {s.style}, format {s.format}, durée cible "
           f"{int(s.target_duration) if s.target_duration else 'auto'} s, sous-titres {s.captions}."]
    if s.instructions.strip():
        out.append(f"Consignes du monteur : {s.instructions.strip()[:400]}")
    out.append(f"\nRushes ({len(project.media)}) :")
    if not project.media:
        out.append("- aucun pour l'instant")
    for m in project.media:
        out.extend(media_section(project, m))
    out.append("\n" + (LIMITS_TOOLS if tools else LIMITS_BLIND))
    tl = project.timeline
    names = {m.id: m.filename for m in project.media}
    out.append("")
    out.extend(montage_section(project))
    if tl.markers:
        out.append("Repères du monteur : " + " | ".join(f"{mmss(k.t)} {k.label or '(sans nom)'}" for k in tl.markers[:20]))
    open_c = [c for c in tl.comments if c.status == "ouvert"]
    if open_c:
        out.append("Commentaires en attente : " + " | ".join(
            f"{mmss(c.src_t)} sur {names.get(c.media_id, 'tout le montage') if c.media_id else 'tout le montage'} : « {c.text[:80]} »"
            for c in open_c[:10]))
    if project.research:
        out.append("Notes de recherche (onglet Recherche) : " + " | ".join(n.question[:80] for n in project.research[-6:]))
    if project.warnings:
        out.append("Avertissements : " + " | ".join(w[:120] for w in project.warnings[:5]))
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars - 40].rsplit("\n", 1)[0] + "\n… (dossier tronqué)"
    return text
