"""Moteur 3D, détection des coups et style d'edit PvP."""
import subprocess
import time

import numpy as np
from fastapi.testclient import TestClient

from app import config
from app.ai import pvp
from app.analysis import hits
from app.main import app
from app.media import ffmpeg
from app.models import Clip, ImpactItem, TextItem, Timeline
from app.render import engine3d as e3
from app.render.scene3d import EFFECTS_3D, IMPACTS, TRANSITIONS_3D, Scene3D, needs_3d

SR = config.AUDIO_SR
COMBO_A = [2.0, 2.4, 2.8, 3.3, 3.7, 4.2]
COMBO_B = [8.0, 8.35, 8.7, 9.1, 9.5]


def _clicks(times, seconds=12.0, steps=True):
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.003, int(seconds * SR)).astype(np.float32)
    t = np.arange(int(0.2 * SR)) / SR
    hit = (np.sin(2 * np.pi * (150 - 200 * t) * t) * 0.9 + rng.normal(0, 0.4, t.size)) * np.exp(-25 * t)
    for h in times:
        i = int(h * SR)
        x[i:i + hit.size] += hit[: x.size - i]
    if steps:  # bruits de pas : attaque nette mais faible volume
        st = rng.normal(0, 0.1, int(0.08 * SR)) * np.exp(-60 * np.arange(int(0.08 * SR)) / SR)
        for k in np.arange(0.3, seconds - 0.2, 0.38):
            i = int(k * SR)
            x[i:i + st.size] += st
    return x


def test_hits_and_combos():
    found = hits.detect_hits(_clicks(COMBO_A + COMBO_B), SR)
    ts = [h[0] for h in found]
    for h in COMBO_A + COMBO_B:
        assert any(abs(h - t) < 0.04 for t in ts), (h, ts)
    assert len(ts) <= len(COMBO_A + COMBO_B) + 1, "les pas ne doivent pas compter comme des coups"
    cs = hits.combos(found)
    assert len(cs) == 2 and cs[0]["n"] == len(COMBO_A)


def test_generated_beat_has_expected_tempo():
    path = pvp.beat_track(128, bars=12)
    pcm = ffmpeg.decode_audio(path)
    bpm, _ = hits.beat_grid(pcm, SR)
    assert abs(bpm - 128) < 3 or abs(bpm - 64) < 2


def test_engine_projection_and_card():
    cam = e3.Camera(640, 360)
    full = e3.card(cam, e3.rotation())
    assert np.allclose(full, [[0, 0], [640, 0], [640, 360], [0, 360]], atol=1e-6)
    assert e3.faces_camera(full) and not e3.faces_camera(e3.card(cam, e3.rotation(0, 180, 0)))


def test_scene_renders_every_3d_item():
    W, H, fps = 320, 180, 30
    a = np.zeros((H, W, 3), np.uint8)
    a[:, :, 2] = 200
    b = np.zeros((H, W, 3), np.uint8)
    b[:, :, 0] = 200
    clips = [Clip(media_id="a", src_in=0, src_out=1.5)]
    for k, tr in enumerate(TRANSITIONS_3D):
        clips.append(Clip(media_id="b" if k % 2 == 0 else "a", src_in=0, src_out=1.2, transition=tr, transition_duration=0.6))
    for k, fx in enumerate(EFFECTS_3D):
        clips.append(Clip(media_id="a", src_in=0, src_out=1.2, effects=[fx]))
    tl = Timeline(clips=clips, texts=[TextItem(start=0.2, duration=1.0, text="COMBO x5", style="texte_3d")],
                  impacts=[ImpactItem(start=0.5, kind=k) for k in IMPACTS])
    assert needs_3d(tl)
    sc = Scene3D(tl, W, H, fps)
    changed = 0
    for n in range(int(sc.total * fps)):
        i = sc._clip_at(n / fps)
        src = a if tl.clips[i].media_id == "a" else b
        out = sc.process(n, src.copy())
        assert out.shape == (H, W, 3) and out.dtype == np.uint8
        changed += int(not np.array_equal(out, src))
    assert changed > sc.total * fps * 0.4


def _wait(c, job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_pvp_edit_and_3d_export(tmp_path):
    wav = tmp_path / "hits.wav"
    import wave
    pcm = (np.clip(_clicks(COMBO_A + COMBO_B), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    src = tmp_path / "gameplay.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=30:d=12", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Test PvP"}).json()["id"]
    with src.open("rb") as f:
        assert c.post(f"/api/projects/{pid}/media", files=[("files", ("gameplay.mp4", f, "video/mp4"))]).status_code == 200
    j = _wait(c, c.post(f"/api/projects/{pid}/auto-edit", json={"style": "pvp"}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["director"] == "pvp" and len(tl["clips"]) >= 3
    assert any(x["speed"] < 1 for x in tl["clips"]), "ralenti sur le coup final"
    assert sum(i["kind"] == "secousse" for i in tl["impacts"]) >= len(COMBO_A + COMBO_B) - 1
    assert any(t["style"] == "texte_3d" for t in tl["texts"])
    assert any(x["transition"] in TRANSITIONS_3D or x["transition"] != "aucune" for x in tl["clips"][1:])
    # chaque coupe entre combos tombe sur un temps de la musique
    beat = 60 / pvp.DEFAULT_BPM
    t, cuts = 0.0, []
    for x in tl["clips"]:
        if x["transition"] != "aucune":
            cuts.append(t)
        t += (x["src_out"] - x["src_in"]) / x["speed"]
    for cut in cuts:
        assert abs(cut / beat - round(cut / beat)) < 0.02, cut
    j = _wait(c, c.post(f"/api/projects/{pid}/export", json={"quality": "720p", "fps": 30}).json())
    assert j["status"] == "termine", j
    r = c.get(f"/api/projects/{pid}/exports/{j['result']}")
    out = tmp_path / "pvp.mp4"
    out.write_bytes(r.content)
    info = ffmpeg.probe(out)
    assert info["has_audio"] and info["height"] == 720 and abs(info["duration"] - t) < 0.3
