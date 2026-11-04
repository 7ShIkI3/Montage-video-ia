"""Mettre en avant ce dont parle la voix : plans calés sur les phrases, projecteur sur la zone désignée."""
import subprocess

import numpy as np
import pytest

from app.ai import voicefocus as vf
from app.models import Clip, Media, OverlayItem, Project, TextItem, Timeline, Transcript, Word
from app.render.render import mask_filters


def _words(spec):
    """spec : liste de (début, texte) ; chaque mot dure 0,3 s."""
    return [Word(start=t, end=t + 0.3, text=x) for t, x in spec]


@pytest.fixture(scope="module")
def object_video(tmp_path_factory):
    """Fond gris uni avec un carré blanc en haut à droite (x 0,70-0,80, y 0,15-0,32) : ce qu'on montre."""
    out = tmp_path_factory.mktemp("vf") / "objet.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x606060:s=640x360:r=30:d=8",
                    "-vf", "drawbox=x=448:y=54:w=64:h=60:color=white:t=fill", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", str(out)], check=True)
    return out


def test_sentences_split_on_punctuation_and_gaps():
    w = _words([(0, "Bonjour"), (0.35, "à"), (0.7, "tous."), (1.1, "On"), (1.45, "commence"), (2.6, "Ensuite")])
    s = vf.sentences(w)
    assert [x[2] for x in s] == ["Bonjour à tous.", "On commence", "Ensuite"]


def test_fit_to_sentences_extends_cut_and_snaps_broll():
    words = _words([(1.0, "Une"), (1.35, "phrase"), (1.7, "assez"), (2.05, "longue"), (2.4, "ici."),
                    (10.0, "Autre"), (10.35, "chose"), (10.7, "à"), (11.05, "dire.")])
    tr = {"m": Transcript(media_id="m", words=words)}
    tl = Timeline(clips=[Clip(media_id="m", src_in=0.5, src_out=2.0), Clip(media_id="m", src_in=9.8, src_out=12.0)])
    tl.texts = [TextItem(start=2.0, duration=1, text="après la coupe")]
    tl.overlays = [OverlayItem(media_id="b", src_in=0, src_out=1.0, start=1.6)]  # finira au milieu de « Autre chose… »
    notes = vf.fit_to_sentences(tl, tr)
    assert tl.clips[0].src_out == pytest.approx(2.85)  # la phrase finit à 2,7 s (+0,15)
    assert tl.texts[0].start == pytest.approx(2.85)  # les éléments suivants suivent
    o = tl.overlays[0]
    assert o.start == pytest.approx(2.45)  # décalé avec le plan allongé
    assert o.start + o.duration == pytest.approx(4.0, abs=0.02)  # fin de « dire. » (3,9 s) + 0,1
    assert len(notes) == 2


def test_mentions_found_in_speech():
    words = _words([(1.0, "Alors"), (1.35, "regardez"), (1.7, "ce"), (2.05, "petit"), (2.4, "carré."),
                    (5.0, "Merci.")])
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=8)])
    found = vf.mentions(tl, {"m": Transcript(media_id="m", words=words)})
    assert len(found) == 1 and found[0]["word"].lower() == "regardez"
    assert 1.0 <= found[0]["start"] <= 1.6 and found[0]["end"] >= found[0]["start"] + 1.2


def test_local_saliency_finds_the_object(object_video):
    z = vf.local_region(str(object_video), 2.0)
    assert z is not None
    cx, cy = z["x"] + z["w"] / 2, z["y"] + z["h"] / 2
    assert 0.68 <= cx <= 0.82 and 0.12 <= cy <= 0.36


def test_spotlight_added_and_rendered(object_video, tmp_path):
    words = _words([(1.0, "Regardez"), (1.35, "ici,"), (1.7, "le"), (2.05, "carré"), (2.4, "blanc."), (6.0, "Fin.")])
    tr = {"m": Transcript(media_id="m", words=words)}
    proj = Project(name="vf", media=[Media(id="m", filename="o.mp4", path=str(object_video), duration=8,
                                           width=640, height=360)])
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=8)])

    class FakeLocator:
        def locate(self, items):
            assert items[0][2].startswith("Regardez")
            return [{"x": 0.68, "y": 0.12, "w": 0.15, "h": 0.22, "label": "le carré"}]

    notes = vf.add_spotlights(proj, tl, tr, locator=FakeLocator())
    c = tl.clips[0]
    mk = next(m for m in c.masks if m.kind == "projecteur")
    assert notes and mk.label == "le carré" and mk.push_in and c.keyframes
    assert max(k.zoom for k in c.keyframes) > 1.1

    # Rendu : pendant le projecteur, le fond s'assombrit et la zone reste claire ; avant, rien ne change.
    out = tmp_path / "spot.mp4"
    chain = ",".join(mask_filters(c.masks, "t"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(object_video), "-filter_complex",
                    f"[0:v]null,{chain}[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True)

    def frame(t):
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf",
                            "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True)
        return np.frombuffer(p.stdout, np.uint8).reshape(360, 640).astype(float)

    before, during = frame(0.5), frame(mk.start + 0.5)
    assert during[300, 100] < 0.6 * before[300, 100]  # fond assombri
    assert during[85, 480] > 200  # le carré reste net et clair
    assert abs(frame(7.5)[300, 100] - before[300, 100]) < 6  # après : retour à la normale


def test_voice_focus_job_runs_on_project(rush):
    import time

    from fastapi.testclient import TestClient

    from app import storage
    from app.main import app
    c = TestClient(app)

    def wait(job):
        for _ in range(1500):
            j = c.get(f"/api/jobs/{job['id']}").json()
            if j["status"] in ("termine", "erreur"):
                return j
            time.sleep(0.2)
        raise TimeoutError(job)

    pid = c.post("/api/projects", json={"name": "Projecteur"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    # Une transcription qui désigne quelque chose, posée sur le premier plan.
    p = storage.load_project(pid)
    c0 = p.timeline.clips[0]
    t = c0.src_in + 0.2
    storage.save_transcript(pid, Transcript(media_id=c0.media_id, words=_words(
        [(t, "Regardez"), (t + 0.35, "ce"), (t + 0.7, "motif"), (t + 1.05, "coloré.")])))
    j = wait(c.post(f"/api/projects/{pid}/voice-focus").json())
    assert j["status"] == "termine", j
    tl = storage.load_project(pid).timeline
    assert any(m.kind == "projecteur" for m in tl.clips[0].masks), tl.voice_focus
    assert wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())["status"] == "termine"
