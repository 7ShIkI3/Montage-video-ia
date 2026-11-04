"""Corrections de la revue : musique hors des données refusée, chapitres, hésitations, XML NTSC, PvP sans habillage."""
import re
import threading

import numpy as np
from fastapi.testclient import TestClient

from app import storage
from app.ai import pvp, segments as seg_mod
from app.ai.publish import chapters
from app.main import app
from app.models import Clip, EditSettings, Media, Project, Segment, Timeline, Transcript, Word
from app.render import captions, xml_export

c = TestClient(app)


def test_music_track_outside_data_is_refused(tmp_path):
    pid = c.post("/api/projects", json={"name": "Sécurité"}).json()["id"]
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    for bad in ["/etc/passwd", str(tmp_path / "x.mp3")]:
        tl["music"]["track"] = bad
        r = c.put(f"/api/projects/{pid}/timeline", json=tl)
        assert r.status_code == 400, bad
    # Même écrit directement sur le disque, le chemin n'est pas servi.
    p = storage.load_project(pid)
    p.timeline.music.track = "/etc/passwd"
    storage.save_project(p)
    assert c.get(f"/api/projects/{pid}/music/current").status_code == 404


def test_update_project_keeps_concurrent_changes():
    p = storage.save_project(Project(name="Course"))
    stale = storage.load_project(p.id)  # une tâche longue a lu le projet…
    storage.update_project(p.id, lambda q: setattr(q.timeline, "title", "retouche"))  # …l'utilisateur retouche…
    storage.update_project(p.id, lambda q: setattr(q, "publish_kit", {"ok": True}))  # …la tâche finit
    q = storage.load_project(p.id)
    assert q.timeline.title == "retouche" and q.publish_kit == {"ok": True}
    assert stale.timeline.title != "retouche"
    # Les écritures concurrentes ne se perdent pas.
    def bump():
        for _ in range(20):
            storage.update_project(p.id, lambda q: q.exports.append("x"))
    ts = [threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(storage.load_project(p.id).exports) == 80


def test_short_last_chapter_is_merged():
    p = Project(name="Chap", media=[Media(id="m1", filename="a.mp4", path="/x/a.mp4", duration=200)])
    clips, segs, t = [], [], 0.0
    for i, d in enumerate([15] * 6 + [5]):
        segs.append(Segment(id=f"s{i}", media_id="m1", start=t, end=t + d, text=f"Partie numéro {i} du sujet.",
                            kind="speech"))
        clips.append(Clip(media_id="m1", src_in=t, src_out=t + d, segment_id=f"s{i}"))
        t += d
    out = chapters(Timeline(clips=clips), segs, p)
    assert out and 95 - out[-1]["t"] >= 10
    assert all(b["t"] - a["t"] >= 10 for a, b in zip(out, out[1:]))


def _media():
    return Media(id="m1", filename="a.mp4", path="/x/a.mp4", duration=10)


def test_padding_does_not_bring_back_fillers():
    words = [Word(start=0.2, end=1.0, text="Bonjour"), Word(start=1.02, end=1.10, text="euh", filler=True),
             Word(start=1.2, end=2.0, text="tout le monde.")]
    segs = seg_mod.build_segments(_media(), Transcript(media_id="m1", words=words), {}, split_fillers=True,
                                  include_action=False)
    sp = [s for s in segs if s.kind == "speech"]
    assert all(not (s.start < 1.06 < s.end) for s in sp)  # le « euh » reste coupé
    # Sans coupe des hésitations, les sous-titres montrent ce qui s'entend vraiment.
    tl = Timeline(clips=[Clip(media_id="m1", src_in=0.0, src_out=2.2)])
    ws = captions.timeline_words(tl, {"m1": Transcript(media_id="m1", words=words)})
    assert [w.text for w in ws] == ["Bonjour", "euh", "tout le monde."]


def test_merge_does_not_bridge_removed_filler():
    from app.ai.director import merge_contiguous
    a = Clip(media_id="m1", src_in=0.1, src_out=1.0, segment_id="a")
    b = Clip(media_id="m1", src_in=1.12, src_out=2.0, segment_id="b")
    assert len(merge_contiguous([a, b], 0.5)) == 1
    assert len(merge_contiguous([a, b], 0.5, {"a"})) == 2


def test_xml_ntsc_frames_use_real_rate():
    m = Media(id="m1", filename="a.mp4", path="/x/a.mp4", duration=700, fps=29.97, width=1920, height=1080)
    p = Project(name="X", media=[m])
    xml = xml_export.to_xmeml(p, Timeline(clips=[Clip(media_id="m1", src_in=600, src_out=601)]))
    assert "<in>17982</in>" in xml and "<timebase>30</timebase>" in xml


def test_pvp_respects_auto_effects_and_beat_phase(tmp_path):
    hits = [[1.0 + 0.3 * k, 0.9] for k in range(5)]
    m = Media(id="m1", filename="pvp.mp4", path="/x/pvp.mp4", duration=12)
    off = pvp.build([m], {"m1": hits}, EditSettings(style="pvp", auto_effects=False), "pvp")
    assert off.clips and not off.impacts and not off.texts and not off.sfx
    assert all(not c.effects and c.transition == "aucune" for c in off.clips)
    on = pvp.build([m], {"m1": hits}, EditSettings(style="pvp"), "pvp")
    assert on.impacts and on.texts
    # Le morceau démarre sur un temps : la grille de la musique part de 0 dans le montage.
    bpm, first = pvp.music_grid(on.music.track)
    beat = 60 / bpm
    assert abs(((first - on.music.start) / beat) - round((first - on.music.start) / beat)) < 1e-2
