"""Repères posés par l'utilisateur : chapitres YouTube du kit de publication et repères de l'export XML."""
import pytest
from pydantic import ValidationError

from app.ai import publish
from app.models import Clip, Marker, Media, Project, Segment, Timeline
from app.render import xml_export


def _project(n: int, length: float) -> tuple[Project, list[Segment]]:
    m = Media(id="m1", filename="rush.mp4", path="/x/rush.mp4", duration=n * length, width=1920, height=1080,
              fps=30, has_audio=True, has_video=True)
    segs = [Segment(id=f"s{i}", media_id="m1", start=i * length, end=(i + 1) * length,
                    text=f"Partie {i} : on parle de cuisine et de recettes faciles numéro {i}.") for i in range(n)]
    clips = [Clip(media_id="m1", src_in=s.start, src_out=s.end, segment_id=s.id) for s in segs]
    return Project(name="Cuisine", media=[m], timeline=Timeline(clips=clips, title="Cuisine facile")), segs


def test_markers_become_chapters_with_youtube_rules():
    p, segs = _project(12, 15.0)  # 3 minutes
    p.timeline.markers = [
        Marker(t=95.0, label="Conclusion"),
        Marker(t=30.0, label="Le marché"),
        Marker(t=100.0, label="Trop proche"),      # à moins de 10 s du précédent : ignoré
        Marker(t=179.5, label="Fin"),              # à la toute fin : ignoré
        Marker(t=60.0, label="Pas un chapitre", chapter=False),
    ]
    ch = publish.chapters(p.timeline, segs, p)
    assert ch == [{"t": 0.0, "title": "Intro", "fixed": False},
                  {"t": 30.0, "title": "Le marché", "fixed": True},
                  {"t": 95.0, "title": "Conclusion", "fixed": True}]


def test_too_few_markers_fall_back_to_automatic_chapters():
    p, segs = _project(12, 15.0)
    auto = publish.chapters(p.timeline, segs, p)
    p.timeline.markers = [Marker(t=30.0, label="Seul")]
    assert publish.chapters(p.timeline, segs, p) == auto


def test_marker_without_label_gets_a_numbered_title():
    p, segs = _project(12, 15.0)
    p.timeline.markers = [Marker(t=40.0), Marker(t=120.0)]
    ch = publish.chapters(p.timeline, segs, p)
    assert [c["title"] for c in ch] == ["Intro", "Chapitre 2", "Chapitre 3"]


def test_markers_are_exported_to_xml():
    p, _ = _project(4, 15.0)
    p.timeline.markers = [Marker(t=30.0, label="Le marché", color="vert")]
    xml = xml_export.to_xmeml(p, p.timeline)
    assert "<name>Repère : Le marché</name>" in xml
    assert f"<in>{round(30.0 * xml_export.SEQ_FPS)}</in>" in xml


def test_marker_validation():
    with pytest.raises(ValidationError):
        Marker(t=-1.0)
    with pytest.raises(ValidationError):
        Marker(t=1.0, color="rose")
    assert Marker(t=2.0).id.startswith("k_") and Marker(t=2.0).chapter is True
