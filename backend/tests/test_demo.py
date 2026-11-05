"""Clip de démo (« Essayer sans vidéo ») et étapes nommées des tâches."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.media import demo

client = TestClient(app)


def _wait(job, seconds=120):
    for _ in range(seconds * 5):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur", "annulee"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_render_tiny_clip(tmp_path):
    """Le rendu produit un MP4 lisible, avec progression de 0 à 1 et nettoyage du WAV temporaire."""
    seen = []
    out = demo.render(tmp_path / "mini.mp4", seconds=1.0, fps=10, size="320x180", progress=seen.append)
    assert out.exists() and out.stat().st_size > 1000
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)
    assert not (tmp_path / "mini.wav").exists()
    from app.media import ffmpeg
    info = ffmpeg.probe(out)
    assert abs(info["duration"] - 1.0) < 0.3 and info["width"] == 320


def test_render_cancel(tmp_path):
    """`check` qui lève arrête le rendu et ne laisse pas de fichier à moitié écrit."""
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stop")
    with pytest.raises(RuntimeError):
        demo.render(tmp_path / "x.mp4", seconds=3.0, fps=10, size="320x180", check=check)
    assert not (tmp_path / "x.mp4").exists() and not (tmp_path / "x.wav").exists()


def test_clip_path_override(monkeypatch, tmp_path):
    f = tmp_path / "perso.mp4"
    f.write_bytes(b"x")
    monkeypatch.setenv("MONTEUR_DEMO_CLIP", str(f))
    assert demo.clip_path() == f
    monkeypatch.delenv("MONTEUR_DEMO_CLIP")
    monkeypatch.setattr(demo, "cached_path", lambda: tmp_path / "absent.mp4")
    assert demo.clip_path() is None


def test_demo_project(rush, monkeypatch):
    """POST /api/demo crée le projet tout de suite, importe le clip et le monte ; la tâche porte des étapes nommées."""
    monkeypatch.setenv("MONTEUR_DEMO_CLIP", str(rush))
    r = client.post("/api/demo")
    assert r.status_code == 200, r.text
    body = r.json()
    pid = body["project"]["id"]
    assert body["project"]["name"] == demo.DEMO_PROJECT_NAME
    assert body["project"]["settings"]["style"] == "pvp"
    assert body["job"]["kind"] == "demo" and body["job"]["project_id"] == pid
    j = _wait(body["job"])
    assert j["status"] == "termine", j
    assert j["stage"] == "pret"
    stages = [s.get("stage") for s in j["steps"]]
    assert "import" in stages and "analyse" in stages and "montage" in stages
    assert "rendu" not in stages  # clip fourni : pas de rendu
    p = client.get(f"/api/projects/{pid}").json()
    assert len(p["media"]) == 1 and p["media"][0]["filename"] == demo.DEMO_NAME
    assert p["timeline"]["clips"]
    assert j["result"]["clips"] == len(p["timeline"]["clips"])


def test_auto_edit_stages(rush):
    """Le montage classique annonce ses étapes dans l'ordre, jusqu'à « pret »."""
    pid = client.post("/api/projects", json={"name": "Étapes"}).json()["id"]
    with rush.open("rb") as f:
        assert client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))]).status_code == 200
    j = _wait(client.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())
    assert j["status"] == "termine", j
    order = ["analyse", "reperage", "brief", "montage", "pret"]
    seen = [s.get("stage") for s in j["steps"] if s.get("stage") in order]
    firsts = [seen.index(x) for x in order if x in seen]
    assert firsts == sorted(firsts) and seen[-1] == "pret"
