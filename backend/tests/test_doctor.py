"""Diagnostic du poste : blocs attendus, absence de GPU sans erreur, cache de 30 s, route /api/system."""
import shutil

from fastapi.testclient import TestClient

from app import doctor
from app.main import app

c = TestClient(app)
BLOCKS = ("ffmpeg", "encoder", "gpu", "whisper", "voices", "claude", "folders", "jobs")


def _check_shape(rep):
    for name in BLOCKS:
        b = rep[name]
        assert isinstance(b["ok"], bool), name
        assert b["level"] in ("ok", "warn", "error"), name
        assert b["fix"] is None or isinstance(b["fix"], str), name
        assert (b["level"] == "ok") == b["ok"], name
    assert isinstance(rep["summary"]["ok"], bool) and isinstance(rep["summary"]["problems"], list)
    assert rep["summary"]["ok"] == (not rep["summary"]["problems"])


def test_report_has_all_blocks():
    rep = doctor.report(refresh=True)
    _check_shape(rep)
    ff = rep["ffmpeg"]
    assert ff["version"] and ff["path"] and isinstance(ff["libass"], bool)
    assert rep["encoder"]["name"]
    w = rep["whisper"]
    assert w["model"] and w["device_requested"] and isinstance(w["cuda"], bool) and isinstance(w["cublas"], bool)
    assert isinstance(w["model_cached"], bool)
    assert set(rep["voices"]) >= {"espeak", "piper", "installed", "voices_dir"}
    assert rep["claude"]["key_set"] is False and rep["claude"]["fix"]  # conftest désactive Claude
    f = rep["folders"]
    assert f["project_count"] >= 0 and f["free_gb"] > 0 and f["data"] and f["music"] and f["voices"]
    assert rep["jobs"]["active"] >= 0


def test_missing_nvidia_smi_is_not_an_error(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "nvidia-smi" else real(name, *a, **k))
    rep = doctor.report(refresh=True)
    assert rep["gpu"]["ok"] is True and rep["gpu"]["name"] is None and rep["gpu"]["memory"] is None
    _check_shape(rep)


def test_gpu_parsed_from_nvidia_smi(monkeypatch):
    class P:
        returncode, stdout = 0, "NVIDIA GeForce RTX 4070, 12282 MiB\n"

    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(doctor, "_run", lambda cmd: P())
    g = doctor._check_gpu()
    assert g["ok"] and g["name"] == "NVIDIA GeForce RTX 4070" and g["memory"] == "12282 MiB"


def test_failing_check_becomes_not_ok(monkeypatch):
    def boom():
        raise RuntimeError("panne")

    monkeypatch.setattr(doctor, "_check_voices", boom)
    rep = doctor.report(refresh=True)
    assert rep["voices"]["ok"] is False and rep["voices"]["error"] == "panne" and rep["voices"]["fix"]
    assert any(p.startswith("Voix") for p in rep["summary"]["problems"])
    _check_shape(rep)


def test_missing_ffmpeg_is_reported(monkeypatch):
    monkeypatch.setattr(doctor.ffmpeg, "FFMPEG", "ffmpeg-inexistant")
    b = doctor._check_ffmpeg()
    assert b["ok"] is False and b["level"] == "error" and "FFmpeg" in b["fix"]


def test_cache_then_refresh(monkeypatch):
    monkeypatch.setattr(doctor, "_cache", None)  # restauré en fin de test : le faux rapport ne reste pas en cache
    calls = []
    monkeypatch.setattr(doctor, "_build", lambda: calls.append(1) or {"generated_at": len(calls)})
    first = doctor.report(refresh=True)
    assert doctor.report() is first and doctor.report() is first and len(calls) == 1
    assert doctor.report(refresh=True)["generated_at"] == 2 and len(calls) == 2
    monkeypatch.setattr(doctor, "CACHE_TTL", -1)  # cache expiré
    doctor.report()
    assert len(calls) == 3


def test_system_route():
    r = c.get("/api/system")
    assert r.status_code == 200
    body = r.json()
    _check_shape(body)
    stamp = body["generated_at"]
    assert c.get("/api/system").json()["generated_at"] == stamp  # servi par le cache
    assert c.get("/api/system?refresh=1").json()["generated_at"] > stamp
