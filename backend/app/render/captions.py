"""Sous-titres : projection des mots sur la timeline, export SRT et ASS animé."""
from __future__ import annotations

import re

from ..models import CaptionLine, TextItem, Timeline, Transcript, Word

SENT_END = re.compile(r"[.!?…,;:]$")


def voice_words(timeline: Timeline, dubs: bool = True) -> list[Word]:
    """Mots dits par les voix IA posées sur les pistes audio (doublage, voix off IA), en temps timeline."""
    out: list[Word] = []
    for a in timeline.audio:
        if a.kind != "voix" or not a.words or (a.dub and not dubs):
            continue
        for w in a.words:
            if a.src_in <= (w.start + w.end) / 2 < a.src_out:
                s = a.start + max(0.0, w.start - a.src_in)
                e = a.start + min(a.src_out, w.end) - a.src_in
                if e > s:
                    out.append(Word(start=round(s, 3), end=round(e, 3), text=w.text, prob=w.prob))
    return out


def timeline_words(timeline: Timeline, transcripts: dict[str, Transcript]) -> list[Word]:
    """Mots prononcés dans le montage, en temps timeline : paroles des clips gardés (ou leur doublage), plus
    les voix off IA."""
    dubbed = bool(timeline.dub_language) and any(a.dub and a.words for a in timeline.audio)
    # Doublé : on suit la voix doublée (et les voix off IA) ; sinon les paroles des rushes plus les voix off IA.
    out = voice_words(timeline, dubs=dubbed)
    if dubbed:
        return sorted(out, key=lambda w: w.start)
    t0 = 0.0
    for c in timeline.clips:
        tr = transcripts.get(c.media_id)
        if tr:
            # Tous les mots audibles dans les plans gardés, « euh » compris s'ils n'ont pas été coupés.
            for w in tr.words:
                mid = (w.start + w.end) / 2
                if c.src_in <= mid < c.src_out:
                    s = t0 + c.src_to_local(max(0.0, w.start - c.src_in))
                    e = t0 + min(c.duration, c.src_to_local(min(c.source_duration, w.end - c.src_in)))
                    if e > s:
                        out.append(Word(start=round(s, 3), end=round(e, 3), text=w.text, prob=w.prob))
        t0 += c.duration
    return sorted(out, key=lambda w: w.start)


def group_lines(words: list[Word], max_words: int, max_chars: int, max_gap: float = 0.6) -> list[CaptionLine]:
    lines: list[CaptionLine] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            if gap > max_gap or len(cur) >= max_words or chars > max_chars or SENT_END.search(cur[-1].text):
                lines.append(CaptionLine(start=cur[0].start, end=cur[-1].end, words=cur))
                cur = []
        cur.append(w)
    if cur:
        lines.append(CaptionLine(start=cur[0].start, end=cur[-1].end, words=cur))
    # Chaque ligne reste affichée jusqu'à la suivante si l'écart est court.
    for a, b in zip(lines, lines[1:]):
        if b.start - a.end < 0.4:
            a.end = b.start
    return lines


def build_lines(timeline: Timeline, transcripts: dict[str, Transcript],
                translations: dict[str, str] | None = None) -> list[CaptionLine]:
    """Lignes de sous-titres. Avec `translations` (phrase d'origine -> traduction), chaque ligne est
    remplacée par sa traduction, aux mêmes instants (les mots se répartissent sur la durée de la ligne)."""
    vertical = timeline.format == "9:16"
    if timeline.captions == "karaoke":
        lines = group_lines(timeline_words(timeline, transcripts), 3 if vertical else 6, 18 if vertical else 38)
    else:
        lines = group_lines(timeline_words(timeline, transcripts), 7 if vertical else 12, 30 if vertical else 60)
    if timeline.caption_language and translations:
        lines = [translate_line(ln, translations.get(ln.text)) for ln in lines]
    return lines


def translate_line(ln: CaptionLine, text: str | None) -> CaptionLine:
    if not text or not text.strip():
        return ln
    toks = text.split()
    # Chaque mot traduit reçoit une part de la ligne proportionnelle à sa longueur (karaoké lisible).
    weights = [len(t) + 1 for t in toks]
    tot, t, words = sum(weights), ln.start, []
    for tok, w in zip(toks, weights):
        d = (ln.end - ln.start) * w / tot
        words.append(Word(start=round(t, 3), end=round(t + d, 3), text=tok))
        t += d
    return CaptionLine(start=ln.start, end=ln.end, words=words)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(lines: list[CaptionLine]) -> str:
    out = []
    for i, ln in enumerate(lines, 1):
        out.append(f"{i}\n{_srt_time(ln.start)} --> {_srt_time(ln.end)}\n{ln.text}\n")
    return "\n".join(out)


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def _text_styles(width: int, height: int) -> list[str]:
    base = min(width, height)
    f = "Arial Black"
    t = int(base * 0.11)
    g = int(base * 0.13)
    b = int(base * 0.055)
    lg = int(base * 0.04)
    e = int(base * 0.16)
    # Name, Font, Size, Primary, Secondary, Outline, Back, Bold, Italic, Underline, Strike, ScaleX, ScaleY,
    # Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
    return [
        f"Style: Titre,{f},{t},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,2,0,1,{max(3, t // 14)},{max(2, t // 25)},5,60,60,60,1",
        f"Style: GrosTexte,{f},{g},&H0000E5FF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,{max(4, g // 10)},{max(2, g // 20)},5,60,60,60,1",
        f"Style: Bandeau,Arial,{b},&H00FFFFFF,&H00FFFFFF,&H00FF5C7C,&H00FF5C7C,-1,0,0,0,100,100,1,0,3,{max(8, b // 3)},0,1,{int(width * 0.05)},60,{int(height * 0.12)},1",
        f"Style: Legende,Arial,{lg},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,-1,0,0,100,100,0,0,1,2,1,2,60,60,{int(height * 0.18)},1",
        f"Style: Emoji,DejaVu Sans,{e},&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(3, e // 16)},2,5,60,60,60,1",
    ]


def text_events(texts: list[TextItem], width: int, height: int) -> list[str]:
    out = []
    pos_y = {"haut": int(height * 0.16), "centre": int(height * 0.5), "bas": int(height * 0.8)}
    for t in texts:
        if not t.text.strip() or t.style == "texte_3d":  # rendu par le moteur 3D
            continue
        st, en = _ass_time(t.start), _ass_time(t.start + max(0.3, t.duration))
        txt = _esc(t.text).replace("\n", "\\N")
        x = width // 2
        y = pos_y.get(t.position, pos_y["centre"])
        if t.style == "titre":
            tag = rf"{{\an5\pos({x},{y})\fad(250,300)\fscx60\fscy60\t(0,250,\fscx100\fscy100)}}"
            out.append(f"Dialogue: 2,{st},{en},Titre,,0,0,0,,{tag}{txt.upper()}")
        elif t.style == "gros_texte":
            tag = rf"{{\an5\pos({x},{y})\fad(80,150)\frz-4\fscx130\fscy130\t(0,120,\fscx100\fscy100)}}"
            out.append(f"Dialogue: 2,{st},{en},GrosTexte,,0,0,0,,{tag}{txt.upper()}")
        elif t.style == "bandeau":
            x0 = -int(width * 0.4)
            x1 = int(width * 0.05)
            yb = int(height * 0.86) if t.position != "haut" else int(height * 0.12)
            tag = rf"{{\an4\move({x0},{yb},{x1},{yb},0,300)\fad(0,250)}}"
            out.append(f"Dialogue: 2,{st},{en},Bandeau,,0,0,0,,{tag} {txt} ")
        elif t.style == "emoji":
            tag = rf"{{\an5\pos({x},{y})\fad(100,200)\fscx40\fscy40\t(0,200,\fscx110\fscy110)\t(200,320,\fscx100\fscy100)}}"
            out.append(f"Dialogue: 2,{st},{en},Emoji,,0,0,0,,{tag}{txt}")
        else:
            tag = rf"{{\an5\pos({x},{y})\fad(200,200)}}"
            out.append(f"Dialogue: 2,{st},{en},Legende,,0,0,0,,{tag}{txt}")
    return out


def to_ass(lines: list[CaptionLine], width: int, height: int, style: str, fmt: str,
           texts: list[TextItem] | None = None) -> str:
    vertical = fmt == "9:16"
    base = min(width, height)
    if style == "karaoke":
        size = int(base * (0.085 if vertical else 0.065))
        outline = max(2, size // 12)
        margin_v = int(height * (0.28 if vertical else 0.09))
        styl = (f"Style: Default,Arial Black,{size},&H00FFFFFF,&H0000E5FF,&H00000000,&H64000000,"
                f"-1,0,0,0,100,100,0,0,1,{outline},{outline // 2},2,40,40,{margin_v},1")
    else:
        size = int(base * (0.055 if vertical else 0.045))
        outline = max(1, size // 16)
        margin_v = int(height * (0.2 if vertical else 0.06))
        styl = (f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                f"-1,0,0,0,100,100,0,0,1,{outline},1,2,40,40,{margin_v},1")
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        f"Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n{styl}\n"
        + "\n".join(_text_styles(width, height)) + "\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for ln in lines:
        if style == "karaoke":
            # Un événement par mot : le mot prononcé est surligné en jaune et grossit légèrement.
            for i, w in enumerate(ln.words):
                start = w.start if i else ln.start
                end = ln.words[i + 1].start if i + 1 < len(ln.words) else ln.end
                if end <= start:
                    continue
                parts = []
                for j, x in enumerate(ln.words):
                    t = _esc(x.text.upper())
                    if j == i:
                        parts.append(r"{\c&H0000E5FF&\fscx112\fscy112}" + t + r"{\c&H00FFFFFF&\fscx100\fscy100}")
                    else:
                        parts.append(t)
                pop = r"{\fad(0,0)}" if i else r"{\fscx90\fscy90\t(0,80,\fscx100\fscy100)}"
                events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{pop}{' '.join(parts)}")
        else:
            events.append(f"Dialogue: 0,{_ass_time(ln.start)},{_ass_time(ln.end)},Default,,0,0,0,,{_esc(ln.text)}")
    events += text_events(texts or [], width, height)
    return head + "\n".join(events) + "\n"
