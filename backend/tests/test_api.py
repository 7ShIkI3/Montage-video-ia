"""Test de bout en bout : import → montage IA → retouche → consigne → export."""
import time

from fastapi.testclient import TestClient

from app.main import app
from app.media import ffmpeg

client = TestClient(app)


def _wait(job):
    for _ in range(600):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_full_flow(rush):
    assert client.get("/api/health").json()["ok"]
    p = client.post("/api/projects", json={"name": "Test"}).json()
    pid = p["id"]
    with rush.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    media = r.json()[0]
    assert abs(media["duration"] - 24) < 0.5

    job = client.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json()
    j = _wait(job)
    assert j["status"] == "termine", j
    proj = client.get(f"/api/projects/{pid}").json()
    tl = proj["timeline"]
    assert tl["clips"], "le réalisateur doit produire des clips"
    assert proj["media"][0]["proxy"]
    # les silences (5-8 s, 13-16 s, 21-24 s) doivent être retirés
    total = sum(c["src_out"] - c["src_in"] for c in tl["clips"])
    assert total < 22
    sig = client.get(f"/api/projects/{pid}/media/{media['id']}/signals").json()
    assert sig["cuts"] and abs(sig["cuts"][0] - 12) < 0.5

    # retouche manuelle : on supprime le dernier clip
    tl["clips"] = tl["clips"][:-1] or tl["clips"]
    assert client.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200

    # consigne en langage naturel (mode local)
    j = _wait(client.post(f"/api/projects/{pid}/command", json={"instruction": "format vertical"}).json())
    assert j["status"] == "termine", j
    assert client.get(f"/api/projects/{pid}").json()["timeline"]["format"] == "9:16"

    j = _wait(client.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())
    assert j["status"] == "termine", j
    name = j["result"]
    r = client.get(f"/api/projects/{pid}/exports/{name}")
    assert r.status_code == 200 and len(r.content) > 10_000
    out = rush.parent / "out.mp4"
    out.write_bytes(r.content)
    info = ffmpeg.probe(out)
    assert (info["width"], info["height"]) == (720, 1280)
    assert info["has_audio"]
