"""Lot 3 : LUT d'étalonnage, écran partagé, détourage IA sans fond vert et doublage IA."""
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import storage
from app.ai import translate, tts
from app.analysis import segment
from app.main import app
from app.media import ffmpeg
from app.models import Clip, Transcript, Word
from app.render import luts

c = TestClient(app)
FACE = Path(__file__).parent / "data" / "face.jpg"


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def _project(rush, name):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    return pid


def _export(pid, tmp_path, name="out.mp4"):
    j = _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())
    assert j["status"] == "termine", j
    out = tmp_path / name
    out.write_bytes(c.get(f"/api/projects/{pid}/exports/{j['result']}").content)
    return out


def _rgb(path, t, w=160, h=90):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3).astype(np.float32)


def _segmenter_or_skip():
    try:
        return segment.Segmenter()
    except segment.SegmenterUnavailable as e:
        pytest.skip(str(e))


def test_builtin_luts_are_valid_cubes():
    d = luts.builtin_dir()
    for key in luts.BUILTIN:
        luts.validate_cube((d / f"{key}.cube").read_text())
    with pytest.raises(ValueError):
        luts.validate_cube("TITLE x\nLUT_3D_SIZE 4\n0 0 0\n")
    assert luts.resolve("p_x", "integre:../../etc") is None


def test_lut_upload_render_and_preview(rush, tmp_path):
    pid = _project(rush, "LUT")
    ids = [x["id"] for x in c.get(f"/api/projects/{pid}/luts").json()]
    assert "integre:teal_orange" in ids
    bad = c.post(f"/api/projects/{pid}/luts", files={"file": ("x.cube", b"LUT_3D_SIZE 8\n0 0 0\n", "text/plain")})
    assert bad.status_code == 400
    # Une LUT importée qui vide le canal bleu.
    text = luts.cube_text(lambda r, g, b: (r, g, b * 0), "Sans bleu", n=9)
    up = c.post(f"/api/projects/{pid}/luts", files={"file": ("sans bleu.cube", text.encode(), "text/plain")})
    assert up.status_code == 200, up.text
    assert up.json()["id"] in [x["id"] for x in c.get(f"/api/projects/{pid}/luts").json()]

    before = _export(pid, tmp_path, "avant.mp4")
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    tl["lut"], tl["lut_strength"] = up.json()["id"], 1.0
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    after = _export(pid, tmp_path, "apres.mp4")
    a, b = _rgb(before, 1.0), _rgb(after, 1.0)
    assert a[..., 2].mean() > 60 and b[..., 2].mean() < 25
    assert abs(a[..., 1].mean() - b[..., 1].mean()) < 12  # le vert ne bouge presque pas

    media = c.get(f"/api/projects/{pid}").json()["media"][0]["id"]
    r = c.get(f"/api/projects/{pid}/preview-frame", params={"media": media, "t": 1, "lut": up.json()["id"],
                                                          "strength": 0.5})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_split_screen(rush, tmp_path):
    pid = _project(rush, "Écran partagé")
    red = tmp_path / "rouge.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(red)], check=True)
    with red.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rouge.mp4", f, "video/mp4"))])
    p = c.get(f"/api/projects/{pid}").json()
    red_id = next(m["id"] for m in p["media"] if m["filename"] == "rouge.mp4")
    tl = p["timeline"]
    tl["overlays"] = [{"media_id": red_id, "src_in": 0, "src_out": 3, "start": 0.5, "mode": "partage"}]
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    out = _export(pid, tmp_path)
    W = ffmpeg.probe(out)["width"]
    f = _rgb(out, 2.0)
    right, left = f[:, 85:], f[:, :75]
    assert right[..., 0].mean() > 180 and right[..., 1].mean() < 60  # moitié droite : le plan rouge
    assert left.std() > 30  # moitié gauche : la mire du plan principal
    full = _rgb(out, 2.0, W, ffmpeg.probe(out)["height"])
    assert full[:, W // 2 + 1].mean() > 200  # séparateur blanc au milieu


def test_segmenter_finds_person():
    seg = _segmenter_or_skip()
    img = cv2_read(FACE)
    m = seg.mask(img)
    assert m.shape == img.shape[:2] and 0.05 < m.mean() < 0.9
    out = segment.composite(img, m, "detourage_noir")
    corners = [out[:10, :10], out[:10, -10:]]
    assert max(float(x.mean()) for x in corners) < 40  # le fond derrière la personne est devenu noir


def cv2_read(path):
    import cv2
    return cv2.imread(str(path))


def test_cutout_render_replaces_background(rush, tmp_path):
    _segmenter_or_skip()
    pid = _project(rush, "Détourage")
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    for cl in tl["clips"]:
        cl["effects"] = ["detourage_blanc"]
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    # Pas de personne dans la mire : tout le cadre devient le fond blanc.
    assert _rgb(_export(pid, tmp_path), 1.0).mean() > 200
    # Un plan de coupe en écran partagé n'est pas détouré : sa moitié reste intacte.
    red = tmp_path / "rouge.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(red)], check=True)
    with red.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rouge.mp4", f, "video/mp4"))])
    p = c.get(f"/api/projects/{pid}").json()
    red_id = next(m["id"] for m in p["media"] if m["filename"] == "rouge.mp4")
    tl = p["timeline"]
    tl["overlays"] = [{"media_id": red_id, "src_in": 0, "src_out": 3, "start": 0.5, "mode": "partage"}]
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    f = _rgb(_export(pid, tmp_path, "partage.mp4"), 2.0)
    assert f[:, :75].mean() > 200 and f[:, 85:, 1].mean() < 60
    media = c.get(f"/api/projects/{pid}").json()["media"][0]["id"]
    r = c.get(f"/api/projects/{pid}/preview-frame", params={"media": media, "t": 1, "effect": "detourage_studio"})
    assert r.status_code == 200 and len(r.content) > 1000


@pytest.mark.skipif(tts.engine_for("en") is None, reason="espeak-ng absent")
def test_dubbing(rush, monkeypatch):
    pid = _project(rush, "Doublage")
    p = storage.load_project(pid)
    m = p.media[0]
    words = [Word(start=0.3 + 0.35 * k, end=0.6 + 0.35 * k, text=w) for k, w in
             enumerate("Bonjour à tous. Aujourd'hui on teste le doublage.".split())]
    words += [Word(start=3.0 + 0.35 * k, end=3.3 + 0.35 * k, text=w) for k, w in enumerate("C'est parti !".split())]
    storage.save_transcript(pid, Transcript(media_id=m.id, language="fr", words=words))
    tl = p.timeline.model_copy(deep=True)
    tl.clips = [Clip(media_id=m.id, src_in=0.0, src_out=5.0)]
    assert c.put(f"/api/projects/{pid}/timeline", json=tl.model_dump()).status_code == 200
    monkeypatch.setattr(translate, "translate_lines", lambda texts, lang: {t: "Hello everyone, let's go" for t in texts})
    assert c.get("/api/tts").json()["en"] in ("espeak", "piper")

    j = _wait(c.post(f"/api/projects/{pid}/dub", json={"language": "en"}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    dubs = [a for a in tl["audio"] if a["dub"] == "en"]
    # Une voix par phrase, posée à l'instant où la phrase d'origine commence.
    assert len(dubs) == 3 and all(a["lane"] == 3 for a in dubs)
    assert [round(a["start"], 2) for a in dubs] == [0.3, 1.35, 3.0]
    # Chaque phrase doublée tient à peu près dans son créneau (accélérée si besoin, ×1,6 au plus).
    assert dubs[1]["start"] + dubs[1]["src_out"] <= 3.0 + 1.0
    assert all(cl["volume"] == pytest.approx(0.12) for cl in tl["clips"]) and tl["dub_language"] == "en"
    for a in dubs:
        assert ffmpeg.probe(storage.audio_dir(pid) / a["asset"])["duration"] > 0.3

    # Même langue que la vidéo : la voix relit le texte d'origine, sans traduction.
    monkeypatch.setattr(translate, "translate_lines", lambda *a: pytest.fail("ne doit pas traduire"))
    assert _wait(c.post(f"/api/projects/{pid}/dub", json={"language": "fr"}).json())["status"] == "termine"
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert {a["dub"] for a in tl["audio"]} == {"fr"}

    assert _wait(c.post(f"/api/projects/{pid}/dub", json={"language": None}).json())["status"] == "termine"
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert not tl["audio"] and tl["dub_language"] is None and tl["clips"][0]["volume"] == 1.0
