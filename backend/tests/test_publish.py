from app.ai import publish
from app.models import Clip, Media, Project, Segment, Timeline


def _project(n: int, length: float) -> tuple[Project, list[Segment]]:
    m = Media(id="m1", filename="rush.mp4", path="/x/rush.mp4", duration=n * length, width=1920, height=1080,
              fps=30, has_audio=True, has_video=True)
    segs = [Segment(id=f"s{i}", media_id="m1", start=i * length, end=(i + 1) * length,
                    text=f"Partie {i} : on parle de cuisine et de recettes faciles numéro {i}.") for i in range(n)]
    clips = [Clip(media_id="m1", src_in=s.start, src_out=s.end, segment_id=s.id) for s in segs]
    return Project(name="Cuisine", media=[m], timeline=Timeline(clips=clips, title="Cuisine facile")), segs


def test_youtube_chapters_rules():
    p, segs = _project(12, 15.0)  # 3 minutes
    ch = publish.chapters(p.timeline, segs, p)
    assert len(ch) >= 3 and ch[0]["t"] == 0.0
    assert all(b["t"] - a["t"] >= 10 for a, b in zip(ch, ch[1:]))
    short, ssegs = _project(3, 8.0)  # 24 s : pas de chapitres
    assert publish.chapters(short.timeline, ssegs, short) == []


def test_heuristic_kit():
    p, segs = _project(12, 15.0)
    kit = publish.heuristic_kit(p, p.timeline, segs)
    assert kit["titles"][0] == "Cuisine facile"
    assert "#cuisine" in kit["hashtags"] and "#recettes" in kit["hashtags"]
    assert kit["summary"].startswith("Partie 0")
