"""Tests unitaires du cœur IA : silences, segmentation, réalisateur, sous-titres."""
import numpy as np

from app.ai.director import HeuristicDirector, heuristic_command, merge_contiguous
from app.ai.segments import build_segments
from app.analysis import audio as audio_an
from app.models import Clip, EditSettings, Media, Timeline, Transcript, Word
from app.render import captions as cap


def _speechy_audio(sr=16000):
    t = np.arange(int(sr * 10)) / sr
    a = 0.3 * np.sin(2 * np.pi * 200 * t)
    a[(t > 3) & (t < 5)] = 0.0005 * np.random.default_rng(0).standard_normal(((t > 3) & (t < 5)).sum())
    return a.astype(np.float32)


def test_silence_detection_finds_gap():
    res = audio_an.analyze(_speechy_audio(), 10.0)
    assert any(2.9 <= s <= 3.2 and 4.8 <= e <= 5.1 for s, e in res["silences"])
    assert res["active"][0][0] == 0.0


def _transcript():
    words = []
    t = 0.5
    phrases = [
        "Bonjour à tous et bienvenue.",
        "euh",
        "Aujourd'hui je vais vous montrer une astuce incroyable !",
        "Alors on commence par ouvrir le logiciel.",
        "Alors on commence par ouvrir le logiciel correctement.",
        "Et voilà le résultat final.",
    ]
    for ph in phrases:
        for w in ph.split():
            words.append(Word(start=round(t, 2), end=round(t + 0.3, 2), text=w, filler=w == "euh"))
            t += 0.35
        t += 0.9
    return Transcript(media_id="m_test", words=words)


def _media():
    return Media(id="m_test", filename="a.mp4", path="/x/a.mp4", duration=40, has_video=False)


def test_segments_split_fillers_and_retakes():
    segs = build_segments(_media(), _transcript(), {}, split_fillers=True, include_action=False)
    texts = [s.text for s in segs]
    assert not any("euh" in t.split() for t in texts)
    dup = [s for s in segs if s.duplicate_of]
    assert len(dup) == 1 and dup[0].text.startswith("Alors on commence par ouvrir le logiciel.")
    hook = next(s for s in segs if "incroyable" in s.text)
    assert hook.features["hook"] >= 0.6


def test_heuristic_director_youtube_and_short():
    segs = build_segments(_media(), _transcript(), {}, include_action=False)
    tl = HeuristicDirector().direct(segs, EditSettings(style="youtube"), ["m_test"])
    assert tl.clips and tl.director == "heuristique"
    assert all(c.segment_id for c in tl.clips)
    kept = {c.segment_id for c in tl.clips}
    assert not any(s.duplicate_of and s.id in kept for s in segs)
    assert any("prise répétée" in c.reason for c in tl.rejected)

    short = HeuristicDirector().direct(segs, EditSettings(style="short", format="9:16", target_duration=6),
                                       ["m_test"])
    assert short.duration <= 6 * 1.6
    assert short.clips[0].reason.startswith("Accroche")


def test_merge_contiguous():
    a = Clip(media_id="m", src_in=0, src_out=2)
    b = Clip(media_id="m", src_in=2.05, src_out=4)
    c = Clip(media_id="m", src_in=6, src_out=7)
    out = merge_contiguous([a, b, c], 0.1)
    assert len(out) == 2 and out[0].src_out == 4


def test_heuristic_command_duration_and_format():
    segs = build_segments(_media(), _transcript(), {}, include_action=False)
    tl0 = HeuristicDirector().direct(segs, EditSettings(), ["m_test"])
    tl, msg, s2 = heuristic_command("fais une version verticale de 5 secondes", tl0, segs, EditSettings(),
                                    ["m_test"], None)
    assert s2.format == "9:16" and s2.target_duration == 5
    assert tl.format == "9:16" and "durée" in msg


def test_captions_follow_timeline():
    tr = _transcript()
    tl = Timeline(clips=[Clip(media_id="m_test", src_in=5.0, src_out=9.0)], captions="karaoke")
    words = cap.timeline_words(tl, {"m_test": tr})
    assert words and words[0].start >= 0 and words[-1].end <= 4.0 + 1e-6
    lines = cap.build_lines(tl, {"m_test": tr})
    ass = cap.to_ass(lines, 1920, 1080, "karaoke", "16:9")
    assert "Dialogue:" in ass and "PlayResX: 1920" in ass
    srt = cap.to_srt(lines)
    assert "-->" in srt
