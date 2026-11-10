"""Pellicule de vignettes d'un rush pour la timeline."""
import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from app.media import ffmpeg

client = TestClient(app)


def test_filmstrip_count_bounds():
    assert ffmpeg.filmstrip_count(0) == 4 and ffmpeg.filmstrip_count(3) == 4
    assert ffmpeg.filmstrip_count(24) == 12
    assert ffmpeg.filmstrip_count(3600) == ffmpeg.STRIP_MAX


def _fail(*a, **k):
    raise AssertionError("pellicule régénérée alors qu'elle est en cache")


def test_filmstrip_route_builds_grid_once(rush, monkeypatch):
    pid = client.post("/api/projects", json={"name": "Pellicule"}).json()["id"]
    with rush.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    mid = client.get(f"/api/projects/{pid}").json()["media"][-1]["id"]
    r = client.get(f"/api/projects/{pid}/media/{mid}/filmstrip")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    n, cols = int(r.headers["x-strip-count"]), int(r.headers["x-strip-cols"])
    w, h = map(int, r.headers["x-strip-tile"].split("x"))
    assert n == 12 and cols == ffmpeg.STRIP_COLS, "rush de 24 s : une vignette toutes les 2 s"
    sheet = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert sheet.shape == (h, cols * w, 3), "12 vignettes tiennent sur une ligne de 16"
    tiles = [sheet[:, k * w:(k + 1) * w] for k in range(cols)]
    assert all(t.mean() > 20 for t in tiles[:n]), "chaque vignette du rush a une image"
    assert all(t.mean() < 8 for t in tiles[n:]), "les cases après la dernière vignette restent noires"
    # Le rush change de plan à 12 s : les vignettes 5 et 6 (11 s et 13 s) diffèrent plus que deux vignettes de la mire fixe.
    same = np.abs(tiles[7].astype(int) - tiles[8].astype(int)).mean()
    cut = np.abs(tiles[5].astype(int) - tiles[6].astype(int)).mean()
    assert cut > same + 10
    # Deuxième demande : servie depuis le cache, sans relancer FFmpeg.
    assert (storage.cache_dir(pid) / f"{mid}.strip.jpg").exists()
    monkeypatch.setattr(ffmpeg, "filmstrip", _fail)
    assert client.get(f"/api/projects/{pid}/media/{mid}/filmstrip").content == r.content


def test_filmstrip_unknown_media_is_404():
    pid = client.post("/api/projects", json={"name": "Vide"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/media/m_absent/filmstrip").status_code == 404


def test_filmstrip_survives_stuck_ffmpeg(rush, tmp_path, monkeypatch):
    """Un FFmpeg qui ne répond pas ne bloque pas la fabrication : la vignette reste noire, les autres sont là."""
    import subprocess
    real, calls = subprocess.run, []

    def run(cmd, **kw):
        calls.append(kw.get("timeout"))
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return real(cmd, **kw)

    monkeypatch.setattr(ffmpeg.subprocess, "run", run)
    out = tmp_path / "strip.jpg"
    assert ffmpeg.filmstrip(rush, out, 24.0) == 12
    assert all(t == ffmpeg.STRIP_TIMEOUT for t in calls), "chaque saut a un délai maximum"
    sheet = cv2.imdecode(np.frombuffer(out.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    dark = [k for k in range(12) if sheet[:, k * ffmpeg.STRIP_W:(k + 1) * ffmpeg.STRIP_W].mean() < 8]
    assert len(dark) == 1, "une seule vignette manque"


def test_filmstrip_missing_file_is_retryable(rush):
    pid = client.post("/api/projects", json={"name": "Rush absent"}).json()["id"]
    with rush.open("rb") as f:
        client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    p = storage.load_project(pid)
    m = p.media[-1]
    m.path, m.proxy = str(storage.cache_dir(pid) / "parti.mp4"), None
    storage.save_project(p)
    assert client.get(f"/api/projects/{pid}/media/{m.id}/filmstrip").status_code == 503
