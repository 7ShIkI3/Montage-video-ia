"""Sous-titres : projection des mots sur la timeline, export SRT et ASS animé."""
from __future__ import annotations

import re

from ..models import CaptionLine, Timeline, Transcript, Word

SENT_END = re.compile(r"[.!?…,;:]$")


def timeline_words(timeline: Timeline, transcripts: dict[str, Transcript]) -> list[Word]:
    """Mots prononcés dans les clips gardés, en temps timeline."""
    out: list[Word] = []
    t0 = 0.0
    for c in timeline.clips:
        tr = transcripts.get(c.media_id)
        if tr:
            for w in tr.words:
                if w.filler:
                    continue
                mid = (w.start + w.end) / 2
                if c.src_in <= mid < c.src_out:
                    s = t0 + max(0.0, w.start - c.src_in)
                    e = t0 + min(c.duration, w.end - c.src_in)
                    if e > s:
                        out.append(Word(start=round(s, 3), end=round(e, 3), text=w.text, prob=w.prob))
        t0 += c.duration
    return out


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


def build_lines(timeline: Timeline, transcripts: dict[str, Transcript]) -> list[CaptionLine]:
    vertical = timeline.format == "9:16"
    if timeline.captions == "karaoke":
        return group_lines(timeline_words(timeline, transcripts), 3 if vertical else 6, 18 if vertical else 38)
    return group_lines(timeline_words(timeline, transcripts), 7 if vertical else 12, 30 if vertical else 60)


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


def to_ass(lines: list[CaptionLine], width: int, height: int, style: str, fmt: str) -> str:
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
        f"Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n{styl}\n\n"
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
    return head + "\n".join(events) + "\n"
