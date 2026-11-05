"""Zone d'entrée/sortie (I/O) : découpe de la timeline pour l'export de la zone seule."""
import pytest
from pydantic import ValidationError

from app import timeline_ops
from app.models import (AudioItem, Clip, ImageItem, Keyframe, Marker, OverlayItem, Range, SfxItem, TextItem,
                        Timeline)


def _tl() -> Timeline:
    # Trois plans de 4 s : [0-4] [4-8] [8-12]
    return Timeline(clips=[Clip(media_id="m1", src_in=0, src_out=4), Clip(media_id="m1", src_in=10, src_out=14),
                           Clip(media_id="m2", src_in=0, src_out=4)], title="Montage")


def _d(tl: Timeline) -> float:
    return round(tl.duration, 3)


def test_range_must_be_ordered():
    assert Range(start=1, end=2).end == 2
    with pytest.raises(ValidationError):
        Range(start=2, end=2.01)


def test_slice_cuts_clips_at_both_ends_and_rebases_to_zero():
    out = timeline_ops.slice_timeline(_tl(), 2.0, 10.0)
    assert _d(out) == 8.0
    assert [(c.media_id, c.src_in, c.src_out) for c in out.clips] == [("m1", 2.0, 4.0), ("m1", 10.0, 14.0), ("m2", 0.0, 2.0)]
    assert out.range is None and out.title == "Montage"


def test_slice_inside_one_clip_keeps_a_single_piece():
    out = timeline_ops.slice_timeline(_tl(), 5.0, 7.0)
    assert len(out.clips) == 1 and (out.clips[0].src_in, out.clips[0].src_out) == (11.0, 13.0)


def test_slice_respects_speed_and_ramps():
    tl = Timeline(clips=[Clip(media_id="m1", src_in=0, src_out=8, speed=2.0),           # 4 s de timeline
                         Clip(media_id="m1", src_in=0, src_out=6, speed=1.0, speed_end=2.0)])  # 4 s (vitesse moyenne 1,5)
    out = timeline_ops.slice_timeline(tl, 2.0, 6.0)
    assert _d(out) == pytest.approx(4.0, abs=1e-3)
    a, b = out.clips
    assert (a.src_in, a.src_out, a.speed) == (4.0, 8.0, 2.0)
    assert b.src_in == 0 and b.speed == 1.0 and b.speed_end == pytest.approx(1.5, abs=1e-3)  # rampe coupée à mi-course


def test_slice_keeps_keyframes_continuous():
    tl = Timeline(clips=[Clip(media_id="m1", src_in=0, src_out=10,
                              keyframes=[Keyframe(t=0, zoom=1.0, ease="lineaire"), Keyframe(t=10, zoom=2.0, ease="lineaire")])])
    out = timeline_ops.slice_timeline(tl, 2.5, 7.5)
    ks = out.clips[0].keyframes
    assert ks[0].t == 0 and ks[0].zoom == pytest.approx(1.25)
    assert ks[-1].t == pytest.approx(5.0) and ks[-1].zoom == pytest.approx(1.75)


def test_slice_shifts_items_and_drops_those_outside():
    tl = _tl()
    tl.texts = [TextItem(start=1.0, duration=2.0, text="avant"), TextItem(start=3.0, duration=4.0, text="à cheval"),
                TextItem(start=5.0, duration=1.0, text="dedans"), TextItem(start=11.0, duration=1.0, text="après")]
    tl.sfx = [SfxItem(start=3.0), SfxItem(start=4.5), SfxItem(start=10.0)]
    tl.images = [ImageItem(asset="logo.png", start=0.0, duration=None), ImageItem(asset="photo.png", start=6.0, duration=10.0)]
    tl.audio = [AudioItem(asset="voix.wav", start=2.0, src_in=0.0, src_out=6.0, kind="voix")]
    tl.overlays = [OverlayItem(media_id="m2", src_in=1.0, src_out=5.0, start=9.0)]
    tl.markers = [Marker(t=3.9, label="hors"), Marker(t=4.0, label="début"), Marker(t=9.9, label="fin"), Marker(t=10.0, label="hors2")]
    out = timeline_ops.slice_timeline(tl, 4.0, 10.0)
    assert [(t.text, t.start, t.duration) for t in out.texts] == [("à cheval", 0.0, 3.0), ("dedans", 1.0, 1.0)]
    assert [s.start for s in out.sfx] == [0.5]
    assert [(g.asset, g.start, g.duration) for g in out.images] == [("logo.png", 0.0, None), ("photo.png", 2.0, 4.0)]
    a = out.audio[0]
    assert (a.start, a.src_in, a.src_out) == (0.0, 2.0, 6.0)  # les 2 premières secondes de la voix sont avant la zone
    o = out.overlays[0]
    assert (o.start, o.src_in, o.src_out) == (5.0, 1.0, 2.0)  # coupé par la fin de la zone
    assert [(k.label, k.t) for k in out.markers] == [("début", 0.0), ("fin", 5.9)]


def test_slice_keeps_music_in_sync():
    tl = _tl()
    tl.music.track = "musique.mp3"
    tl.music.start = 1.5
    out = timeline_ops.slice_timeline(tl, 4.0, 10.0)
    assert out.music.start == 5.5


def test_slice_is_clamped_to_the_timeline():
    out = timeline_ops.slice_timeline(_tl(), 10.0, 50.0)
    assert _d(out) == 2.0


def test_timeline_accepts_a_range_and_export_requires_one(rush, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Zone"}).json()["id"]
    with rush.open("rb") as f:
        m = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))]).json()[0]
    tl = {"clips": [{"media_id": m["id"], "src_in": 0, "src_out": 6}], "range": {"start": 1.0, "end": 3.0}}
    r = client.put(f"/api/projects/{pid}/timeline", json=tl)
    assert r.status_code == 200 and r.json()["range"] == {"start": 1.0, "end": 3.0}
    tl["range"] = {"start": 3.0, "end": 3.0}
    assert client.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 422
    tl["range"] = None
    client.put(f"/api/projects/{pid}/timeline", json=tl)
    r = client.post(f"/api/projects/{pid}/export", json={"quality": "720p", "range_only": True})
    assert r.status_code == 400 and "zone" in r.json()["detail"].lower()
    client.delete(f"/api/projects/{pid}")


def test_export_of_the_range_renders_only_that_part(rush, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import pipeline
    from app.main import app
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Zone"}).json()["id"]
    with rush.open("rb") as f:
        m = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))]).json()[0]
    tl = {"clips": [{"media_id": m["id"], "src_in": 0, "src_out": 6}, {"media_id": m["id"], "src_in": 12, "src_out": 18}],
          "title": "Mon film", "range": {"start": 4.0, "end": 8.0}}
    assert client.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    seen = {}
    real = pipeline.export

    def spy(project_id, quality, ctx, *a, **kw):
        seen["tl"] = kw.get("tl")
        seen["label"] = kw.get("label")
        ctx.step("faux rendu", 1.0)
        return "zone.mp4"
    monkeypatch.setattr(pipeline, "export", spy)
    job = client.post(f"/api/projects/{pid}/export", json={"quality": "720p", "range_only": True}).json()
    import time
    for _ in range(100):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            break
        time.sleep(0.1)
    assert j["status"] == "termine", j
    assert seen["label"] == "Mon film (zone 0m04-0m08)"
    sl = seen["tl"]
    assert [(c.src_in, c.src_out) for c in sl.clips] == [(4.0, 6.0), (12.0, 14.0)]
    assert real is not spy
    client.delete(f"/api/projects/{pid}")
