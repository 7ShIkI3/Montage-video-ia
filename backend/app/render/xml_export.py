"""Export du montage en XML Final Cut Pro 7 (xmeml), lisible par Premiere Pro, DaVinci Resolve et
Final Cut Pro (via import XML). L'IA fait le gros du travail, le monteur finit dans son logiciel."""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from ..models import Project, Timeline

SEQ_FPS = 30


def _rate(fps: float) -> tuple[int, str]:
    ntsc = any(abs(fps - x) < 0.02 for x in (23.976, 29.97, 59.94))
    return int(round(fps)) or SEQ_FPS, "TRUE" if ntsc else "FALSE"


def _rate_xml(fps: float) -> str:
    tb, ntsc = _rate(fps)
    return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"


def to_xmeml(project: Project, tl: Timeline, name: str | None = None) -> str:
    W, H = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}[tl.format]
    seq_name = escape(name or tl.title or project.name)
    seen: set[str] = set()
    vitems, aitems = [], []
    t = 0  # position dans la séquence, en images
    acc = 0.0  # position en secondes (arrondie à chaque plan, sans dérive cumulée)
    for i, c in enumerate(tl.clips):
        m = project.media_by_id(c.media_id)
        fps = m.fps or SEQ_FPS
        tb, _ = _rate(fps)
        src_in, src_out = round(c.src_in * tb), round(c.src_out * tb)
        acc += c.duration
        start, end = t, max(t + 1, round(acc * SEQ_FPS))
        t = end
        fid = f"file-{m.id}"
        if fid in seen:
            file_xml = f'<file id="{fid}"/>'
        else:
            seen.add(fid)
            url = Path(m.path).resolve().as_uri()
            file_xml = (
                f'<file id="{fid}"><name>{escape(m.filename)}</name><pathurl>{escape(url)}</pathurl>'
                f"{_rate_xml(fps)}<duration>{round(m.duration * tb)}</duration>"
                f"<media><video><samplecharacteristics><width>{m.width}</width><height>{m.height}</height>"
                f"</samplecharacteristics></video>"
                + ("<audio><channelcount>2</channelcount></audio>" if m.has_audio else "")
                + "</media></file>"
            )
        speed = ""
        if abs(c.speed - 1) > 1e-3:
            speed = ("<filter><effect><name>Time Remap</name><effectid>timeremap</effectid>"
                     "<effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype>"
                     f"<parameter><parameterid>speed</parameterid><name>speed</name><value>{c.speed * 100:.1f}</value>"
                     "</parameter></effect></filter>")
        note = escape(c.reason[:200])
        common = (f"<name>{escape(m.filename)}</name><duration>{round(m.duration * tb)}</duration>{_rate_xml(fps)}"
                  f"<start>{start}</start><end>{end}</end><in>{src_in}</in><out>{src_out}</out>")
        if m.has_video:
            vitems.append(f'<clipitem id="v{i}">{common}{file_xml}{speed}'
                          f"<comments><mastercomment1>{note}</mastercomment1></comments></clipitem>")
            file_xml = f'<file id="{fid}"/>'
        if m.has_audio and "muet" not in c.audio_effects:
            gain = ""
            if abs(c.volume - 1) > 1e-3:
                gain = ("<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid>"
                        "<effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype>"
                        "<mediatype>audio</mediatype><parameter><parameterid>level</parameterid><name>Level</name>"
                        f"<value>{c.volume:.3f}</value></parameter></effect></filter>")
            aitems.append(f'<clipitem id="a{i}">{common}{file_xml}{speed}{gain}'
                          "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    # Repères : titres, bruitages et plans de coupe à refaire dans le logiciel.
    markers = []
    for x in tl.texts:
        markers.append((x.start, f"Texte ({x.style}) : {x.text}"))
    for s in tl.sfx:
        markers.append((s.start, f"Bruitage : {s.kind}"))
    for o in tl.overlays:
        markers.append((o.start, f"B-roll : {project.media_by_id(o.media_id).filename} {o.src_in:.2f}-{o.src_out:.2f} s"))
    mk = "".join(
        f"<marker><name>{escape(n[:120])}</name><comment>{escape(n)}</comment>"
        f"<in>{round(st * SEQ_FPS)}</in><out>-1</out></marker>"
        for st, n in sorted(markers)
    )
    fmt = (f"<format><samplecharacteristics>{_rate_xml(SEQ_FPS)}<width>{W}</width><height>{H}</height>"
           "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="seq-1"><name>{seq_name}</name><duration>{t}</duration>{_rate_xml(SEQ_FPS)}{mk}'
        f"<media><video>{fmt}<track>{''.join(vitems)}</track></video>"
        f"<audio><track>{''.join(aitems)}</track></audio></media></sequence></xmeml>\n"
    )
