"""Dossier du projet : ce que l'app a mesuré dans les rushes, en texte, pour le réalisateur IA et la recherche.

Sans ce dossier, l'IA ne « voit » pas les rushes et répond à côté (« je ne peux pas savoir quels combos tu as
gagnés »). Avec, elle a des horodatages : coups détectés au son, combos, écrans rouges (coups encaissés
probables), changements de plan, phrases transcrites, montage actuel, repères, commentaires, notes de recherche.
Le dossier dit aussi ce que l'app ne mesure pas, pour que l'IA réponde « probable » plutôt qu'inventer.
"""
from __future__ import annotations

from .. import storage
from ..analysis import hits as hits_an
from ..models import Project, Transcript

LIMITS = (
    "Limites de la mesure : l'app n'interprète pas les images (pas de lecture du chat ou des messages de "
    "kill, pas d'identification des joueurs, pas de score ni de barre de vie). Un combo = coups rapprochés "
    "entendus au son ; un écran rouge = probablement un coup encaissé par le joueur qui filme. Déductions à "
    "présenter comme probables, avec les horodatages."
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


def media_section(project: Project, m, max_phrases: int = 30) -> list[str]:
    lines = [f"- {m.filename} : {mmss(m.duration)} ({m.width}x{m.height}, {m.fps:.0f} i/s)"
             + ("" if m.analyzed else " — pas encore analysé")]
    sig = storage.load_signals(project.id, m.id) or {}
    hits = sig.get("hits") or []
    if hits:
        cb = hits_an.combos(hits)
        lines.append(f"  Coups détectés au son : {len(hits)}" + (f" ; combos : {len(cb)}" if cb else ""))
        for c in cb[:12]:
            lines.append(f"  · combo {mmss(c['start'])} → {mmss(c['end'])} : {c['n']} coups, score {c['score']:.1f}")
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
    cuts = (sig.get("video") or {}).get("cuts") or []
    if cuts:
        lines.append(f"  Changements de plan dans le rush : {len(cuts)}")
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


def project_dossier(project: Project, max_chars: int = 9000) -> str:
    """Le dossier complet, en français, borné en taille (les paroles sont échantillonnées si besoin)."""
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
    out.append("\n" + LIMITS)
    tl = project.timeline
    names = {m.id: m.filename for m in project.media}
    if tl.clips:
        out.append(f"\nMontage actuel : {len(tl.clips)} plans, {mmss(tl.duration)}"
                   + (f", titre « {tl.title} »" if tl.title else "") + (f", réalisateur {tl.director}" if tl.director else ""))
        if tl.summary:
            out.append(f"Résumé du réalisateur : {tl.summary[:500]}")
        t = 0.0
        for k, c in enumerate(tl.clips[:40], 1):
            d = (c.src_out - c.src_in) / max(0.05, c.speed or 1.0)
            fx = ", ".join(x for x in [f"×{c.speed}" if c.speed != 1 else "", *c.effects] if x)
            out.append(f"  {k}. {mmss(t)} ← {names.get(c.media_id, c.media_id)} {mmss(c.src_in)}→{mmss(c.src_out)}"
                       + (f" ({fx})" if fx else ""))
            t += d
        if len(tl.clips) > 40:
            out.append(f"  … {len(tl.clips) - 40} autres plans")
        if tl.texts:
            out.append("Textes : " + " | ".join(f"{mmss(x.start)} « {x.text[:60]} »" for x in tl.texts[:12]))
        if tl.review:
            out.append(f"Note du relecteur : {tl.review.score:.1f}/10")
        if tl.quality:
            out.append(f"Contrôle qualité : {tl.quality.score}/100")
    else:
        out.append("\nMontage actuel : aucun (pas encore monté).")
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
