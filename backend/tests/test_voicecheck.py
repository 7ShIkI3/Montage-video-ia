"""Voix IA calée au mot près et réécoutée : horodatage des mots, compréhension, sous-titres qui suivent la voix."""
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.ai import quality, voicecheck as vc
from app.analysis import transcribe as tr_mod
from app.main import app
from app.models import AudioItem, Clip, Timeline, Transcript, Word
from app.render import captions as cap

c = TestClient(app)
needs_espeak = pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng absent")


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


@pytest.fixture(scope="module")
def three_bursts(tmp_path_factory):
    """Trois « mots » sonores (0,2-0,6 s, 1,0-1,6 s, 2,0-2,3 s) séparés de silences."""
    out = tmp_path_factory.mktemp("vc") / "voix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.4*sin(2*PI*220*t)*(between(t,0.2,0.6)+between(t,1.0,1.6)+between(t,2.0,2.3))'"
                    ":s=16000:d=2.6", str(out)], check=True)
    return out


def test_wer():
    assert vc.wer("Bonjour à tous", "bonjour a tous") == 0
    assert vc.wer("le chat dort", "le chien dort") == pytest.approx(1 / 3)
    assert vc.wer("un deux", "") == 1


def test_words_timed_on_voiced_parts(three_bursts, monkeypatch):
    monkeypatch.setenv("MONTEUR_DISABLE_WHISPER", "1")
    words, rep = vc.verify(three_bursts, "un deux trois")
    assert rep["method"] == "energie" and rep["ok"] is None
    assert [w.text for w in words] == ["un", "deux", "trois"]
    # Chaque mot tombe dans un passage parlé, jamais dans un silence.
    runs = vc.voiced_runs(__import__("app.media.ffmpeg", fromlist=["x"]).decode_audio(three_bursts), 16000)
    assert len(runs) == 3
    for w in words:
        mid = (w.start + w.end) / 2
        assert any(a - 0.05 <= mid <= b + 0.05 for a, b in runs), (w, runs)
    assert words[0].start == pytest.approx(0.2, abs=0.05) and words[-1].end <= 2.35


def test_misheard_voice_is_flagged(three_bursts, monkeypatch):
    heard = Transcript(media_id="x", words=[Word(start=0.2, end=0.6, text="hum"), Word(start=1.0, end=1.6, text="bleu")])
    monkeypatch.setattr(tr_mod, "transcribe", lambda *a, **k: heard)
    words, rep = vc.verify(three_bursts, "un deux trois", "fr")
    assert rep["method"] == "whisper" and rep["ok"] is False and rep["wer"] >= 0.66
    assert [w.text for w in words] == ["un", "deux", "trois"]  # texte voulu gardé pour les sous-titres
    ok = Transcript(media_id="x", words=[Word(start=0.2, end=0.6, text="Un"), Word(start=1.0, end=1.6, text="deux"),
                                         Word(start=2.0, end=2.3, text="trois.")])
    monkeypatch.setattr(tr_mod, "transcribe", lambda *a, **k: ok)
    words, rep = vc.verify(three_bursts, "un deux trois", "fr")
    assert rep["ok"] is True and words[2].start == 2.0  # instants entendus par Whisper

    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=10)])
    tl.audio = [AudioItem(asset="v.wav", start=3, src_out=2.6, label="Voix off IA",
                          check={"ok": False, "wer": 0.7, "heard": "hum bleu", "method": "whisper"})]
    rep = quality.plan_checks(tl, {})
    assert any(i.check == "voix_ia" and i.t == 3 for i in rep.issues)


def test_captions_follow_dubbed_and_ai_voice():
    tr = {"m": Transcript(media_id="m", words=[Word(start=1, end=1.4, text="Bonjour")])}
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=10)])
    tl.audio = [AudioItem(asset="d.wav", start=1.0, src_out=1.2, dub="en", words=[Word(start=0.1, end=0.5, text="Hello")]),
                AudioItem(asset="v.wav", start=6.0, src_out=2.0, words=[Word(start=0.2, end=0.6, text="Abonnez-vous")])]
    # Sans doublage actif : paroles du rush + voix off IA.
    assert [w.text for w in cap.timeline_words(tl, tr)] == ["Bonjour", "Abonnez-vous"]
    assert cap.timeline_words(tl, tr)[1].start == pytest.approx(6.2)
    tl.dub_language = "en"
    words = cap.timeline_words(tl, tr)
    assert [w.text for w in words] == ["Hello", "Abonnez-vous"] and words[0].start == pytest.approx(1.1)


@needs_espeak
def test_ai_voiceover_job(rush):
    pid = c.post("/api/projects", json={"name": "Voix off IA"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    j = _wait(c.post(f"/api/projects/{pid}/voiceover",
                     json={"text": "Abonnez-vous pour la suite de la vidéo.", "start": 1.0, "language": "fr"}).json())
    assert j["status"] == "termine", j
    a = next(x for x in storage.load_project(pid).timeline.audio if x.label == "Voix off IA")
    assert a.text.startswith("Abonnez") and len(a.words) == 7 and a.check["method"] in ("whisper", "energie")
    assert all(w.end <= a.src_out + 0.05 for w in a.words) and a.words[0].start < 0.5
    tl = storage.load_project(pid).timeline
    assert any(w.text == "Abonnez-vous" for w in cap.timeline_words(tl, {}))
    assert c.post(f"/api/projects/{pid}/voiceover", json={"text": "  ", "language": "fr"}).status_code == 400
    assert _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())["status"] == "termine"
