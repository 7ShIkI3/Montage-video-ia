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
