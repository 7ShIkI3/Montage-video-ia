"""Kit de publication : titres, description avec chapitres, hashtags et miniature.

Inspiré des outils « repurposing » (OpusClip, OpenShorts) : une fois le montage fini, l'IA
prépare aussi tout ce qu'il faut pour le mettre en ligne.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .. import config
from ..media import ffmpeg
from ..models import Project, Segment, TextItem, Timeline
from ..render import captions as cap

log = logging.getLogger(__name__)

STOP = set("""
le la les un une des de du et en à au aux pour par sur dans avec ce cet cette ces je tu il elle on nous vous
ils elles que qui quoi est sont a ai as ont pas ne plus mais ou donc or ni car mon ma mes ton ta tes son sa ses
leur leurs vos nos votre notre y se c ça ca d l j qu n s t m voilà alors bon bah euh hum aujourd hui montre
montrer faire fait fais voir vais va aller dire dit tout tous toute très bien vraiment comme aussi juste être
avoir peut peux veux faut cela ceci quand comment parce puis encore même rien chose choses truc trucs ici là
""".split())


def _fmt(t: float) -> str:
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def _words(text: str, n: int) -> str:
    """Début de phrase de n mots au plus, sans finir sur un mot vide (« pour », « de »…)."""
    first = re.split(r"(?<=[.!?])\s", re.sub(r"\s+", " ", text).strip())[0]
    w = first.split(" ")[:n]
    while len(w) > 2 and re.sub(r"[^\w']", "", w[-1].lower()) in STOP:
        w.pop()
    out = " ".join(w).rstrip(",;:.")
    return out[:1].upper() + out[1:] if out else ""


def chapters(tl: Timeline, segments: list[Segment], project: Project) -> list[dict]:
    """Chapitres au format YouTube : premier à 0:00, au moins 3, chacun d'au moins 10 s."""
    total = tl.duration
    if total < 40 or not tl.clips:
        return []
    by_id = {s.id: s for s in segments}
    target = max(10.0, total / 6)
    out, t, cur_start, cur_text = [], 0.0, 0.0, ""
    for i, c in enumerate(tl.clips):
        seg = by_id.get(c.segment_id or "")
        if i == 0 or (t - cur_start >= target and (i == 0 or c.media_id != tl.clips[i - 1].media_id or t - cur_start >= target * 1.5)):
            if i:
                out.append({"t": round(cur_start, 2), "title": cur_text})
            cur_start = t
            cur_text = _words(seg.text, 6) if seg and seg.text else Path(project.media_by_id(c.media_id).filename).stem
        t += c.duration
    out.append({"t": round(cur_start, 2), "title": cur_text})
    out = [x for k, x in enumerate(out) if k + 1 == len(out) or out[k + 1]["t"] - x["t"] >= 10]
    if len(out) < 3:
        return []
    out[0]["t"] = 0.0
    if out[0]["title"] and len(out) > 1:
        out[0]["title"] = "Intro"
    return out


def _keywords(text: str, n: int = 5) -> list[str]:
    freq: dict[str, int] = {}
    for w in re.findall(r"[a-zA-ZÀ-ÿ'’]+", text.lower()):
        w = re.split(r"['’]", w)[-1]
        if len(w) >= 4 and w not in STOP:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n]]


def heuristic_kit(project: Project, tl: Timeline, segments: list[Segment]) -> dict:
    by_id = {s.id: s for s in segments}
    spoken = " ".join(by_id[c.segment_id].text for c in tl.clips if c.segment_id in by_id and by_id[c.segment_id].text)
    hook = next((by_id[c.segment_id].text for c in tl.clips if c.segment_id in by_id and by_id[c.segment_id].text), "")
    base = tl.title or project.name
    kws = _keywords(spoken)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", spoken) if len(x.split()) >= 4]
    summary = " ".join(sentences[:2]) if tl.director != "claude" or not tl.summary else tl.summary
    hook_kw = _keywords(hook, 3)
    thumb = " ".join(k.capitalize() for k in hook_kw[:2]) + " !" if len(hook_kw) >= 2 else base
    titles = [base]
    if hook:
        titles += [f"« {_words(hook, 8)} »", f"{base} : {_words(hook, 6).lower()}"]
    if tl.format == "9:16":
        titles.append(f"{base} en {int(tl.duration)} secondes")
    tags = [f"#{k}" for k in kws] + (["#shorts"] if tl.format == "9:16" else [])
    return {"titles": list(dict.fromkeys(titles))[:5], "summary": summary or tl.summary,
            "hashtags": tags, "thumbnail_text": thumb}


KIT_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "thumbnail_text": {"type": "string"},
        "chapter_titles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titles", "summary", "hashtags", "thumbnail_text", "chapter_titles"],
    "additionalProperties": False,
}

KIT_SYSTEM = (
    "Tu prépares la mise en ligne d'une vidéo pour un créateur francophone. À partir de la transcription du montage, "
    "propose 5 titres accrocheurs mais honnêtes (pas de promesse que la vidéo ne tient pas, 70 caractères max), un "
    "résumé de 2 à 4 phrases pour la description, 3 à 8 hashtags pertinents (avec #), un texte de miniature très court "
    "(2 à 4 mots, percutant) et un titre court pour chaque chapitre fourni, dans le même ordre."
)


def claude_kit(project: Project, tl: Timeline, segments: list[Segment], chaps: list[dict]) -> dict:
    from .director import ClaudeDirector

    by_id = {s.id: s for s in segments}
    lines, t = [], 0.0
    for c in tl.clips:
        seg = by_id.get(c.segment_id or "")
        if seg and seg.text:
            lines.append(f"[{_fmt(t)}] {seg.text}")
        t += c.duration
    content = (f"Projet : {project.name}\nTitre de travail : {tl.title}\nFormat : {tl.format}, durée {tl.duration:.0f} s\n"
               f"Résumé du monteur : {tl.summary}\n\nTranscription du montage :\n" + "\n".join(lines[:300])
               + "\n\nChapitres (début) :\n" + "\n".join(f"- {_fmt(c['t'])} : {c['title']}" for c in chaps))
    return ClaudeDirector()._call(content, system=KIT_SYSTEM, schema=KIT_SCHEMA)


def best_frame_time(tl: Timeline, project_id: str) -> tuple[int, float]:
    """Plan au meilleur score, pris en son milieu."""
    i = max(range(len(tl.clips)), key=lambda k: tl.clips[k].score)
    c = tl.clips[i]
    return i, (c.src_in + c.src_out) / 2


def thumbnail(project: Project, tl: Timeline, text: str, out: Path, work: Path) -> Path:
    from ..render.render import _cover, _cover_tracked
    from ..analysis import faces as faces_an
    from .. import storage

    W, H = (1280, 720) if tl.format == "16:9" else (1080, 1920) if tl.format == "9:16" else (1080, 1080)
    i, at = best_frame_time(tl, project.id)
    c = tl.clips[i]
    m = project.media_by_id(c.media_id)
    kf = []
    if tl.reframe and m.width / max(1, m.height) > W / H + 0.01:
        kf = faces_an.keyframes((storage.load_signals(project.id, m.id) or {}).get("faces"), at, at + 0.5)
    crop = _cover_tracked(W, H, 1.05, kf[:1]) if kf else _cover(W, H, 1.05)
    ass = cap.to_ass([], W, H, "aucun", tl.format,
                     [TextItem(start=0, duration=5, text=text, style="gros_texte", position="bas")])
    (work / "thumb.ass").write_text(ass.replace(r"\fad(80,150)", "").replace(r"\fscx130\fscy130\t(0,120,\fscx100\fscy100)", ""),
                                    encoding="utf-8")
    vf = f"{crop},eq=saturation=1.25:contrast=1.08,vignette=PI/5,ass=thumb.ass"
    ffmpeg.run(["-ss", f"{at:.3f}", "-i", m.path, "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)],
               None, None, cwd=work)
    return out


def build_kit(project: Project, segments: list[Segment], out_dir: Path, work: Path) -> dict:
    tl = project.timeline
    if not tl.clips:
        raise ValueError("La timeline est vide.")
    chaps = chapters(tl, segments, project)
    kit = heuristic_kit(project, tl, segments)
    kit["by"] = "heuristique"
    if config.claude_available():
        try:
            ck = claude_kit(project, tl, segments, chaps)
            for k, ct in zip(chaps, ck.get("chapter_titles", [])):
                if ct.strip():
                    k["title"] = ct.strip()
            kit.update({k: ck[k] for k in ("titles", "summary", "hashtags", "thumbnail_text") if ck.get(k)})
            kit["by"] = "claude"
        except Exception as e:
            log.warning("Kit de publication Claude indisponible : %s", e)
    desc = kit["summary"].strip()
    if chaps:
        desc += "\n\nChapitres :\n" + "\n".join(f"{_fmt(c['t'])} {c['title']}" for c in chaps)
    if kit["hashtags"]:
        desc += "\n\n" + " ".join(kit["hashtags"])
    kit["chapters"] = chaps
    kit["description"] = desc
    thumb = out_dir / "miniature.jpg"
    thumbnail(project, tl, kit["thumbnail_text"] or tl.title or project.name, thumb, work)
    kit["thumbnail"] = thumb.name
    return kit
