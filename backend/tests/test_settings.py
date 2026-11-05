"""Réglages saisis dans l'app : clé Claude jamais renvoyée, validation, messages clairs sans clé."""
import pytest
from fastapi.testclient import TestClient

from app import config, settings
from app.main import app

c = TestClient(app)
KEY = "sk-test-abcd1234"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(settings, "_ENV_KEY", None)
    for var in ("ANTHROPIC_API_KEY", *settings._ENV.values()):
        monkeypatch.delenv(var, raising=False)
    yield
    settings.apply(settings.Settings().model_dump())


def test_key_is_stored_but_never_returned():
    r = c.put("/api/settings", json={"anthropic_api_key": KEY})
    assert r.status_code == 200
    for body in (r.text, c.get("/api/settings").text):
        assert "sk-test" not in body
    s = c.get("/api/settings").json()
    assert s["anthropic_api_key_set"] is True
    assert s["anthropic_api_key_hint"].endswith("1234")
    assert s["key_source"] == "fichier"
    assert settings.SETTINGS_PATH.parent == config.DATA_DIR and settings.SETTINGS_PATH.exists()
    assert KEY in settings.SETTINGS_PATH.read_text(encoding="utf-8")
    # MONTEUR_DISABLE_CLAUDE (tests) garde le réalisateur local malgré la clé.
    assert config.claude_available() is False


def test_empty_key_clears_it():
    c.put("/api/settings", json={"anthropic_api_key": KEY})
    s = c.put("/api/settings", json={"anthropic_api_key": ""}).json()
    assert s["anthropic_api_key_set"] is False and s["anthropic_api_key_hint"] is None and s["key_source"] is None
    assert KEY not in settings.SETTINGS_PATH.read_text(encoding="utf-8")


def test_environment_key_wins(monkeypatch):
    monkeypatch.setattr(settings, "_ENV_KEY", "sk-env-0000-9999")
    s = c.put("/api/settings", json={"anthropic_api_key": KEY}).json()
    assert s["key_source"] == "env" and s["anthropic_api_key_hint"].endswith("9999")


def test_partial_update_keeps_other_fields():
    c.put("/api/settings", json={"claude_effort": "high", "port": 9000})
    s = c.put("/api/settings", json={"whisper_model": "base", "whisper_language": "fr"}).json()
    assert (s["claude_effort"], s["port"], s["whisper_model"], s["whisper_language"]) == ("high", 9000, "base", "fr")
    assert (config.CLAUDE_EFFORT, config.WHISPER_MODEL, config.WHISPER_LANGUAGE) == ("high", "base", "fr")


def test_null_language_clears_it_but_absent_keeps_it():
    c.put("/api/settings", json={"whisper_language": "fr"})
    assert c.put("/api/settings", json={"port": 9001}).json()["whisper_language"] == "fr"
    assert c.put("/api/settings", json={"whisper_language": None}).json()["whisper_language"] is None
    assert config.WHISPER_LANGUAGE is None


@pytest.mark.parametrize("patch", [
    {"whisper_device": "tpu"}, {"claude_effort": "extreme"}, {"port": 80}, {"port": 70000},
    {"anthropic_api_key": "sk avec espace"}, {"anthropic_api_key": "clé-é"}, {"whisper_language": "français"},
])
def test_invalid_values_are_rejected(patch):
    before = c.get("/api/settings").json()
    r = c.put("/api/settings", json=patch)
    assert r.status_code == 422
    assert "sk avec" not in r.text
    assert c.get("/api/settings").json() == before


def test_corrupted_file_falls_back_to_defaults():
    settings.SETTINGS_PATH.write_text("{pas du json", encoding="utf-8")
    assert settings.load() == settings.Settings()


def test_claude_features_ask_for_the_key():
    pid = c.post("/api/projects", json={"name": "Sans clé"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/translate", json={"language": "en"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "claude_required"
    assert "Réglages" in r.json()["detail"]["message"]
    assert c.post(f"/api/projects/{pid}/translate", json={"language": None}).status_code == 200
    assert c.post("/api/settings/test-claude").status_code == 409


def test_health_reports_gpu_and_key():
    h = c.get("/api/health").json()
    assert h["ok"] is True and h["claude_key_set"] is False and h["key_source"] is None
    assert isinstance(h["cuda"], bool) and isinstance(h["cublas"], bool)
    assert h["whisper_device"] is None or h["whisper_device"] in ("cuda", "cpu")
    assert "whisper_fallback" in h and isinstance(h["tts"], dict)


def test_environment_overrides_are_reported(monkeypatch):
    """Une variable MONTEUR_* l'emporte sur le fichier, et l'app le dit pour que la page Réglages l'affiche."""
    c.put("/api/settings", json={"whisper_model": "base"})
    monkeypatch.setenv("MONTEUR_WHISPER_MODEL", "tiny")
    assert c.get("/api/settings").json()["env_overrides"] == ["whisper_model"]
    s = c.put("/api/settings", json={"claude_effort": "low"}).json()
    assert s["whisper_model"] == "base" and config.WHISPER_MODEL == "tiny"
    monkeypatch.delenv("MONTEUR_WHISPER_MODEL")
    assert c.get("/api/settings").json()["env_overrides"] == []
