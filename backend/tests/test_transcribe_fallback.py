"""Whisper : repli automatique sur le processeur quand le GPU (cuBLAS) est inutilisable."""
import time
import types

import faster_whisper
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.analysis import transcribe
from app.main import app

c = TestClient(app)
CUBLAS = "Library cublas64_12.dll is not found"
AUDIO = np.zeros(16000, np.float32)


def _fake_model(cuda_fails_at):
    """Faux WhisperModel : le GPU échoue à la création ("init"), au premier calcul ("run"), ou jamais (None)."""
    class Fake:
        def __init__(self, name, device="cpu", compute_type="default"):
            self.device = device
            if device == "cuda" and cuda_fails_at == "init":
                raise RuntimeError(CUBLAS)

        def transcribe(self, audio, **kw):
            if self.device == "cuda" and cuda_fails_at == "run":
                raise RuntimeError(CUBLAS)
            return [], types.SimpleNamespace(language="fr")
    return Fake


@pytest.fixture(autouse=True)
def whisper_on(monkeypatch):
    monkeypatch.delenv("MONTEUR_DISABLE_WHISPER", raising=False)
    monkeypatch.setattr(config, "WHISPER_DEVICE", "cuda")
    transcribe.reset_model()
    yield
    transcribe.reset_model()


@pytest.mark.parametrize("fails_at", ["init", "run"])
def test_cuda_failure_falls_back_to_cpu(monkeypatch, fails_at):
    monkeypatch.setattr(faster_whisper, "WhisperModel", _fake_model(fails_at))
    t = transcribe.transcribe(AUDIO, "m_1", 1.0)
    st = transcribe.whisper_status()
    assert t.words == [] and t.language == "fr"
    assert st["device_used"] == "cpu" and "cublas64_12.dll" in st["fallback_reason"]
    assert st["device_requested"] == "cuda" and st["model"] == config.WHISPER_MODEL
    transcribe.reset_model()
    assert transcribe.whisper_status()["device_used"] is None
    assert transcribe.whisper_status()["fallback_reason"] is None


def test_cuda_ok_is_kept(monkeypatch):
    monkeypatch.setattr(faster_whisper, "WhisperModel", _fake_model(None))
    transcribe.ensure_loaded()
    st = transcribe.whisper_status()
    assert st["device_used"] == "cuda" and st["fallback_reason"] is None


def test_disable_flag_still_raises(monkeypatch):
    monkeypatch.setenv("MONTEUR_DISABLE_WHISPER", "1")
    with pytest.raises(transcribe.TranscriptionUnavailable):
        transcribe.ensure_loaded()


def test_transcription_error_becomes_a_warning_not_a_failure(rush, monkeypatch):
    """Une erreur de transcription ne fait plus échouer le montage ; le repli GPU est signalé une seule fois."""
    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(transcribe, "ensure_loaded", lambda: None)
    monkeypatch.setattr(transcribe, "whisper_status", lambda: {
        "device_requested": "cuda", "device_used": "cpu", "fallback_reason": CUBLAS, "model": "small"})
    monkeypatch.setattr(transcribe, "transcribe", boom)
    pid = c.post("/api/projects", json={"name": "GPU en panne"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    job = c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json()
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            break
        time.sleep(0.2)
    assert j["status"] == "termine", j
    warnings = c.get(f"/api/projects/{pid}").json()["warnings"]
    assert [w for w in warnings if w.startswith("GPU indisponible")] == [
        f"GPU indisponible ({CUBLAS}) : transcription sur CPU, plus lente."]
    assert any("Transcription indisponible (boom)" in w for w in warnings)
