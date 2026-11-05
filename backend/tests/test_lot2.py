"""Lot 2 : images clés, rampes de vitesse, pistes audio (voix off) et traduction des sous-titres."""
import subprocess
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config, storage
from app.ai import translate
from app.main import app
from app.media import ffmpeg
from app.models import Clip, Keyframe, Transcript, Word
from app.render import render as render_mod

c = TestClient(app)


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


def _frame(path, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-s", "160x90", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(90, 160).astype(np.float32)


def test_ramp_math():
    cl = Clip(media_id="m", src_in=10, src_out=14, speed=1.0, speed_end=0.25)
    assert cl.duration == pytest.approx(2 * 4 / 1.25)
    for t in (0, 0.7, 2.1, cl.duration):
        assert cl.src_to_local(cl.local_to_src(t)) == pytest.approx(t, abs=1e-6)
    assert cl.local_to_src(cl.duration) == pytest.approx(4.0)
    assert cl.speed_at(0) == 1.0 and cl.speed_at(cl.duration) == pytest.approx(0.25)
    assert Clip(media_id="m", src_in=0, src_out=2, speed=2.0, speed_end=2.0).ramp is None


def test_keyframe_expression_values():
    kfs = [Keyframe(t=0, zoom=1.0, ease="lineaire"), Keyframe(t=2, zoom=2.0, ease="lineaire"), Keyframe(t=3, zoom=1.5)]
    expr = render_mod.kf_expr(kfs, "zoom")
    py = (expr.replace("if(", "_if(").replace("lt(", "_lt(").replace("clip(", "_clip("))
    env = {"_if": lambda a, b, c: b if a else c, "_lt": lambda a, b: a < b,
           "_clip": lambda v, lo, hi: max(lo, min(hi, v))}
    # eval sur une expression générée par notre propre code (pas de donnée externe), pour vérifier ses valeurs.
    val = lambda t: eval(py, env, {"t": t})  # noqa: E731
    assert val(0) == pytest.approx(1.0) and val(1) == pytest.approx(1.5) and val(2) == pytest.approx(2.0)
    assert val(2.5) == pytest.approx(1.75) and val(9) == pytest.approx(1.5)


def test_keyframes_ramp_audio_tracks_render(rush, tmp_path):
    pid = _project(rush, "Lot 2")
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    first = tl["clips"][0]
    first["keyframes"] = [{"t": 0, "zoom": 1.0, "x": 0.5, "y": 0.5}, {"t": 1.5, "zoom": 2.5, "x": 0.3, "y": 0.4}]
    first["speed"], first["speed_end"] = 1.0, 0.5
    # Voix off : un bip enregistré, posé à 1 s sur la piste audio 2.
    beep = tmp_path / "voix.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=880:d=1.5", "-c:a", "libopus",
                    str(beep)], check=True)
    with beep.open("rb") as f:
        up = c.post(f"/api/projects/{pid}/audio", files={"file": ("voix.webm", f, "audio/webm")}).json()
    assert up["asset"].endswith(".wav") and up["duration"] == pytest.approx(1.5, abs=0.1)
    assert c.get(f"/api/projects/{pid}/audio/{up['asset']}").status_code == 200
    tl["audio"] = [{"asset": up["asset"], "start": 1.0, "src_in": 0, "src_out": up["duration"], "lane": 1,
                    "kind": "voix", "volume": 1.0}]
    r = c.put(f"/api/projects/{pid}/timeline", json=tl)
    assert r.status_code == 200, r.text
    saved = r.json()
    d0 = saved["clips"][0]
    expected = 2 * (d0["src_out"] - d0["src_in"]) / 1.5
    j = _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())
    assert j["status"] == "termine", j
    out = tmp_path / "out.mp4"
    out.write_bytes(c.get(f"/api/projects/{pid}/exports/{j['result']}").content)
    total = sum((x["src_out"] - x["src_in"]) / (x["speed"] if x.get("speed_end") is None else (x["speed"] + x["speed_end"]) / 2)
                for x in saved["clips"])
    assert ffmpeg.probe(out)["duration"] == pytest.approx(total, abs=0.25)
    assert expected > 0
    # Le zoom animé change l'image : la fin du zoom n'a plus le cadre du début.
    assert np.abs(_frame(out, 0.05) - _frame(out, 1.45)).mean() > 8
    # La voix off s'entend : pic à 880 Hz entre 1 et 2,5 s.
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.2", "-t", "1", "-i", str(out), "-ac", "1", "-ar", "8000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.float32)
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, 1 / 8000)
    band = spec[(freqs > 860) & (freqs < 900)].mean()
    assert band > 5 * spec[(freqs > 1000) & (freqs < 3000)].mean()


def test_caption_translation(rush, monkeypatch):
    pid = _project(rush, "Traduction")
    p = storage.load_project(pid)
    m = p.media[0]
    words = [Word(start=0.3 + 0.4 * k, end=0.6 + 0.4 * k, text=w) for k, w in
             enumerate("Bonjour à tous et bienvenue dans cette vidéo.".split())]
    storage.save_transcript(pid, Transcript(media_id=m.id, words=words))
    tl = p.timeline.model_copy(deep=True)
    tl.clips = [Clip(media_id=m.id, src_in=0.0, src_out=4.0)]
    tl.captions = "karaoke"
    assert c.put(f"/api/projects/{pid}/timeline", json=tl.model_dump()).status_code == 200
    calls = []

    def fake(texts, lang):
        calls.append(list(texts))
        return {t: "EN: " + t.upper() for t in texts}
    monkeypatch.setattr(translate, "translate_lines", fake)
    monkeypatch.setattr(config, "claude_available", lambda: True)
    assert {"code": "en", "name": "anglais"} in c.get("/api/languages").json()
    j = _wait(c.post(f"/api/projects/{pid}/translate", json={"language": "en"}).json())
    assert j["status"] == "termine", j
    caps = c.get(f"/api/projects/{pid}/captions").json()
    assert caps and all(" ".join(w["text"] for w in ln["words"]).startswith("EN: ") for ln in caps)
    srt = c.get(f"/api/projects/{pid}/captions.srt").text
    assert "EN: BONJOUR" in srt
    # Le cache évite de retraduire, et on peut revenir à la langue parlée.
    _wait(c.post(f"/api/projects/{pid}/translate", json={"language": "en"}).json())
    assert len(calls) == 1
    _wait(c.post(f"/api/projects/{pid}/translate", json={"language": None}).json())
    assert "Bonjour" in c.get(f"/api/projects/{pid}/captions.srt").text
    # Sans Claude configuré, la traduction explique ce qu'il faut faire.
    monkeypatch.undo()
    r = c.post(f"/api/projects/{pid}/translate", json={"language": "es"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "claude_required"
    with pytest.raises(ValueError, match="Réglages"):
        translate.translate_lines(["Bonjour"], "es")
