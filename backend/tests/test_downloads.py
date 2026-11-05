"""Téléchargements : liens (yt-dlp), musiques libres ccMixter, modèles Whisper et voix Piper."""
import json
import shutil
import sys
import time
import types
from pathlib import Path

import pytest

from app import config, downloads
from app.jobs import JobContext, Job

CCMIXTER_ROW = {
    "upload_id": 16626, "upload_name": "I dunno", "user_name": "grapes", "user_real_name": "grapes",
    "upload_extra": {"usertags": "hip_hop,instrumental", "bpm": 90},
    "file_page_url": "https://ccmixter.org/files/grapes/16626",
    "license_url": "http://creativecommons.org/licenses/by/3.0/", "license_name": "Attribution (3.0)",
    "files": [{"file_name": "grapes_-_I_dunno.mp3", "file_format_info": {"media-type": "audio", "ps": "2:45"},
               "file_filesize": " (4.32MB)", "download_url": "https://ccmixter.org/content/grapes/grapes_-_I_dunno.mp3"}],
}


def _ctx() -> JobContext:
    return JobContext(Job(project_id="t", kind="telechargement"))


def test_ccmixter_rows_become_clean_items():
    items = downloads._parse_ccmixter([CCMIXTER_ROW, {"upload_id": 1, "files": []}])
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "I dunno" and it["artist"] == "grapes" and it["license"] == "Attribution (3.0)"
    assert it["download_url"].endswith(".mp3") and it["duration"] == "2:45" and it["bpm"] == 90
    assert it["tags"] == ["hip_hop", "instrumental"] and it["size"] == "4.32MB"


def test_search_music_builds_the_query(monkeypatch):
    seen = {}

    def fake(url, timeout=20.0):
        seen["url"] = url
        return [CCMIXTER_ROW]
    monkeypatch.setattr(downloads, "_http_json", fake)
    items = downloads.search_music("calme piano", limit=5)
    assert items[0]["title"] == "I dunno"
    assert "search=calme+piano" in seen["url"] and "tags=instrumental" in seen["url"] and "limit=5" in seen["url"]


def test_download_music_keeps_the_license_next_to_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path)

    def fake_dl(url, dst, ctx, label, lo=0.0, hi=1.0, max_bytes=None):
        dst.write_bytes(b"ID3" + b"\0" * 2000)
        return dst
    monkeypatch.setattr(downloads, "_http_download", fake_dl)
    item = downloads._parse_ccmixter([CCMIXTER_ROW])[0]
    p = downloads.download_music(item, _ctx())
    assert p.name == "grapes - I dunno.mp3" and p.exists()
    meta = json.loads((tmp_path / "grapes - I dunno.mp3.json").read_text(encoding="utf-8"))
    assert meta["license"] == "Attribution (3.0)" and meta["source"] == "ccMixter" and meta["page"].startswith("https://ccmixter.org/")
    p2 = downloads.download_music(item, _ctx())
    assert p2.name == "grapes - I dunno (1).mp3"  # pas d'écrasement
    assert downloads.library_info(p.name)["artist"] == "grapes"
    assert downloads.library_info("inconnu.mp3") is None


def test_download_music_refuses_bad_links():
    with pytest.raises(ValueError):
        downloads.download_music({"download_url": "ftp://x"}, None)


def test_fetch_url_uses_yt_dlp_and_names_the_file_after_the_title(monkeypatch, tmp_path):
    calls = {}

    class FakeYDL:
        def __init__(self, opts):
            calls["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls["url"] = url
            for h in calls["opts"]["progress_hooks"]:
                h({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                h({"status": "finished"})
            out = Path(calls["opts"]["outtmpl"]).parent / "brut.mp4"
            out.write_bytes(b"\0" * 10)
            return {"title": "Ma super vidéo : épisode 1 / 2"}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    ctx = _ctx()
    out = downloads.fetch_url("https://exemple.org/v", tmp_path / "dl", ctx)
    assert out.name == "Ma super vidéo  épisode 1  2.mp4" and out.exists()
    assert calls["url"] == "https://exemple.org/v" and calls["opts"]["noplaylist"] is True
    assert "height<=1080" in calls["opts"]["format"] and calls["opts"]["merge_output_format"] == "mp4"
    assert ctx.job.progress >= 0.95 and "Prêt" in ctx.job.message
    audio = downloads.fetch_url.__wrapped__ if hasattr(downloads.fetch_url, "__wrapped__") else None
    assert audio is None
    with pytest.raises(ValueError):
        downloads.fetch_url("fichier.mp4", tmp_path / "dl2", None)


def test_fetch_url_audio_mode_extracts_mp3(monkeypatch, tmp_path):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            assert self.opts["format"] == "ba/b" and self.opts["postprocessors"][0]["preferredcodec"] == "mp3"
            (Path(self.opts["outtmpl"]).parent / "son.mp3").write_bytes(b"\0" * 10)
            return {"title": "Son"}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path)
    out = downloads.music_from_url("https://exemple.org/s", _ctx())
    assert out.name == "Son.mp3"
    meta = json.loads(out.with_suffix(".mp3.json").read_text(encoding="utf-8"))
    assert meta["source"] == "lien" and "droits" in meta["license"]


def test_whisper_models_report_cache_and_current(monkeypatch):
    monkeypatch.setattr(downloads, "_whisper_cached", lambda s: s in ("small",))
    monkeypatch.setattr(config, "WHISPER_MODEL", "small")
    rows = {r["size"]: r for r in downloads.whisper_models()}
    assert rows["small"]["downloaded"] and rows["small"]["current"] and rows["small"]["approx_mb"] == 480
    assert not rows["medium"]["downloaded"] and not rows["medium"]["current"]
    with pytest.raises(ValueError):
        downloads.download_whisper("gigantesque", None)


PIPER_CATALOG = {
    "fr_FR-siwis-medium": {"key": "fr_FR-siwis-medium", "name": "siwis", "quality": "medium", "num_speakers": 1,
                           "language": {"family": "fr", "name_english": "French", "country_english": "France"},
                           "files": {"fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx": {"size_bytes": 63201294},
                                     "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json": {"size_bytes": 5000},
                                     "fr/fr_FR/siwis/medium/MODEL_CARD": {"size_bytes": 300}}},
    "fr_FR-tom-medium": {"key": "fr_FR-tom-medium", "name": "tom", "quality": "medium", "num_speakers": 1,
                         "language": {"family": "fr"}, "files": ["fr/fr_FR/tom/medium/fr_FR-tom-medium.onnx", "fr/fr_FR/tom/medium/fr_FR-tom-medium.onnx.json"]},
    "en_US-amy-low": {"key": "en_US-amy-low", "name": "amy", "quality": "low", "language": {"family": "en"},
                      "files": ["en/en_US/amy/low/en_US-amy-low.onnx", "en/en_US/amy/low/en_US-amy-low.onnx.json"]},
    "cassee": {"key": "cassee", "name": "x", "language": {"family": "fr"}, "files": ["x/MODEL_CARD"]},
}


def test_piper_catalog_filters_by_language_and_marks_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("MONTEUR_PIPER_VOICES", str(tmp_path))
    (tmp_path / "fr_FR-tom-medium.onnx").write_bytes(b"\0")
    fr = downloads.parse_piper_catalog(PIPER_CATALOG, "fr")
    assert [v["key"] for v in fr] == ["fr_FR-siwis-medium", "fr_FR-tom-medium"]
    siwis = fr[0]
    assert siwis["approx_mb"] == 63 and siwis["onnx"].endswith(".onnx") and siwis["config"].endswith(".onnx.json")
    assert not siwis["installed"] and fr[1]["installed"]
    assert len(downloads.parse_piper_catalog(PIPER_CATALOG, None)) == 3


def test_download_piper_fetches_config_then_model(monkeypatch, tmp_path):
    monkeypatch.setenv("MONTEUR_PIPER_VOICES", str(tmp_path))
    monkeypatch.setattr(downloads, "piper_catalog", lambda lang=None, refresh=False: downloads.parse_piper_catalog(PIPER_CATALOG, lang))
    got = []

    def fake_dl(url, dst, ctx, label, lo=0.0, hi=1.0, max_bytes=None):
        got.append(url)
        dst.write_bytes(b"\0")
        return dst
    monkeypatch.setattr(downloads, "_http_download", fake_dl)
    out = downloads.download_piper("fr_FR-siwis-medium", _ctx())
    assert out == tmp_path / "fr_FR-siwis-medium.onnx" and out.exists() and (tmp_path / "fr_FR-siwis-medium.onnx.json").exists()
    assert got == [downloads.PIPER_BASE + "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json",
                   downloads.PIPER_BASE + "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx"]
    with pytest.raises(ValueError):
        downloads.download_piper("xx_XX-rien-low", None)


def test_piper_runs_in_process_when_frozen(monkeypatch):
    from app.ai import tts
    assert tts.piper_available()  # piper-tts est une dépendance
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert tts._piper_cmd() is None and tts.piper_available()


def _wait(client, job):
    for _ in range(300):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.1)
    raise TimeoutError(job)


def test_media_and_reference_from_url_go_through_a_job(rush, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Lien"}).json()["id"]

    def fake_fetch(url, dst_dir, ctx=None, audio=False, max_height=1080):
        dst_dir.mkdir(parents=True, exist_ok=True)
        out = dst_dir / "clip du web.mp4"
        shutil.copy(rush, out)
        if ctx:
            ctx.step("faux téléchargement", 0.5)
        return out
    monkeypatch.setattr(downloads, "fetch_url", fake_fetch)
    j = _wait(client, client.post(f"/api/projects/{pid}/media/url", json={"url": "https://exemple.org/v"}).json())
    assert j["status"] == "termine", j
    p = client.get(f"/api/projects/{pid}").json()
    assert [m["filename"] for m in p["media"]] == ["clip du web.mp4"] and abs(p["media"][0]["duration"] - 24) < 0.5
    j = _wait(client, client.post(f"/api/projects/{pid}/reference/url", json={"url": "https://exemple.org/ref"}).json())
    assert j["status"] == "termine", j
    p = client.get(f"/api/projects/{pid}").json()
    assert p["reference"]["filename"] == "clip du web.mp4" and p["reference"]["summary"]
    client.delete(f"/api/projects/{pid}")


def test_library_and_models_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path)
    (tmp_path / "a - b.mp3").write_bytes(b"\0" * 10)
    (tmp_path / "a - b.mp3.json").write_text(json.dumps({"title": "b", "artist": "a", "license": "CC BY"}), encoding="utf-8")
    lib = client.get("/api/music/library").json()
    assert lib[0]["name"] == "a - b.mp3" and lib[0]["license"] == "CC BY" and lib[0]["path"].endswith("a - b.mp3")
    assert client.post("/api/music/download", json={"download_url": "https://ailleurs.org/x.mp3"}).status_code == 400
    monkeypatch.setattr(downloads, "_whisper_cached", lambda s: False)
    monkeypatch.setenv("MONTEUR_PIPER_VOICES", str(tmp_path / "voix"))
    m = client.get("/api/models").json()
    assert {r["size"] for r in m["whisper"]} >= {"tiny", "small", "large-v3"} and m["piper"]["runtime"] is True
    assert m["piper"]["installed"] == [] and m["piper"]["voices_dir"].endswith("voix")
    assert client.post("/api/models/whisper/enorme").status_code == 400
    assert client.post("/api/models/piper/fr_FR;x").status_code == 400
    assert client.delete("/api/music/a - b.mp3").json()["ok"] and not (tmp_path / "a - b.mp3.json").exists()
