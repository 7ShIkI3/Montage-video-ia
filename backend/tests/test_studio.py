"""Versions, shorts automatiques, préréglages d'export, images (logo), fond vert et modèles de style."""
import subprocess
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.media import ffmpeg

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def _project(rush, name="Studio"):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    return pid


def test_versions_and_shorts(rush):
    pid = _project(rush, "Versions")
    v = c.get(f"/api/projects/{pid}/versions").json()
    assert len(v["versions"]) == 1 and v["active"] == v["versions"][0]["id"]
    main_id = v["active"]
    n_clips = len(c.get(f"/api/projects/{pid}").json()["timeline"]["clips"])

    v = c.post(f"/api/projects/{pid}/versions", json={"name": "Essai"}).json()
    assert len(v["versions"]) == 2 and v["active"] != main_id
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    tl["clips"] = tl["clips"][:1]
    c.put(f"/api/projects/{pid}/timeline", json=tl)
    p = c.post(f"/api/projects/{pid}/versions/{main_id}/activate").json()
    assert len(p["timeline"]["clips"]) == n_clips, "la version principale n'est pas modifiée par l'essai"

    j = _wait(c.post(f"/api/projects/{pid}/shorts", json={"count": 2, "duration": 12, "format": "9:16"}).json())
    assert j["status"] == "termine", j
    shorts = j["result"]
    assert shorts and all(0 <= s["score"] <= 100 and s["note"] for s in shorts)
    v = c.get(f"/api/projects/{pid}/versions").json()
    assert len(v["versions"]) == 2 + len(shorts)
    p = c.post(f"/api/projects/{pid}/versions/{shorts[0]['id']}/activate").json()
    assert p["timeline"]["format"] == "9:16" and p["timeline"]["clips"]
    assert c.delete(f"/api/projects/{pid}/versions/{shorts[0]['id']}").status_code == 200


def test_presets_queue_and_formats(rush, tmp_path):
    pid = _project(rush, "Formats")
    presets = {p["id"] for p in c.get("/api/export-presets").json()}
    assert {"tiktok", "youtube", "web", "prores", "gif"} <= presets
    c.post(f"/api/projects/{pid}/versions", json={"name": "Copie"})
    ids = [v["id"] for v in c.get(f"/api/projects/{pid}/versions").json()["versions"]]
    j = _wait(c.post(f"/api/projects/{pid}/export-preset", json={"preset": "tiktok", "versions": ids}).json())
    assert j["status"] == "termine", j
    assert len(j["result"]) == 2
    for name in j["result"]:
        out = tmp_path / name
        out.write_bytes(c.get(f"/api/projects/{pid}/exports/{name}").content)
        info = ffmpeg.probe(out)
        assert (info["width"], info["height"]) == (1080, 1920)
    for preset, ext in (("web", ".webm"), ("gif", ".gif"), ("prores", ".mov")):
        j = _wait(c.post(f"/api/projects/{pid}/export-preset", json={"preset": preset}).json())
        assert j["status"] == "termine", (preset, j)
        name = j["result"][0]
        assert name.endswith(ext)
        out = tmp_path / name
        out.write_bytes(c.get(f"/api/projects/{pid}/exports/{name}").content)
        assert out.stat().st_size > 5000 and ffmpeg.probe(out)["has_video"]


def test_logo_greenscreen_and_template(rush, tmp_path):
    pid = _project(rush, "Logo")
    logo = tmp_path / "logo.png"
    img = np.zeros((100, 100, 4), np.uint8)
    img[20:80, 20:80] = (0, 0, 255, 255)  # carré rouge opaque sur fond transparent
    cv2.imwrite(str(logo), img)
    with logo.open("rb") as f:
        asset = c.post(f"/api/projects/{pid}/assets", files={"file": ("logo.png", f, "image/png")}).json()["asset"]
    green = tmp_path / "green.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x00FF00:s=320x180:d=3,drawbox=x=120:y=50:w=80:h=80:color=blue:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(green)], check=True)
    with green.open("rb") as f:
        gm = c.post(f"/api/projects/{pid}/media", files=[("files", ("green.mp4", f, "video/mp4"))]).json()[0]
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    tl["images"] = [{"asset": asset, "start": 0, "duration": None, "x": 0.9, "y": 0.1, "scale": 0.1,
                     "opacity": 1.0, "anim": "aucune"}]
    tl["overlays"] = [{"media_id": gm["id"], "src_in": 0, "src_out": 2.5, "start": 0.5, "mode": "incrustation",
                       "chroma": "vert", "corner": "bas_gauche"}]
    tl["captions"] = "aucun"
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    j = _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p", "fps": 30}).json())
    assert j["status"] == "termine", j
    out = tmp_path / "logo.mp4"
    out.write_bytes(c.get(f"/api/projects/{pid}/exports/{j['result']}").content)
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(out), "-frames:v", "1", str(frame)], check=True)
    f = cv2.imread(str(frame)).astype(int)
    H, W = f.shape[:2]
    b, g, r = f[int(H * 0.1), int(W * 0.9)]
    assert r > 180 and g < 90 and b < 90, "le logo rouge doit être en haut à droite"
    # Même image sans l'incrustation : hors du sujet (carré bleu), le fond vert doit avoir disparu.
    tl["overlays"] = []
    c.put(f"/api/projects/{pid}/timeline", json=tl)
    j = _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p", "fps": 30}).json())
    out2 = tmp_path / "base.mp4"
    out2.write_bytes(c.get(f"/api/projects/{pid}/exports/{j['result']}").content)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(out2), "-frames:v", "1", str(frame)], check=True)
    base = cv2.imread(str(frame)).astype(int)
    # incrustation en bas à gauche : 34 % de la largeur, marges 4 % / 5 %
    pw, ph = int(W * 0.34), int(W * 0.34 * H / W)
    x0, y1 = int(W * 0.04), H - int(H * 0.05)
    box = (slice(y1 - ph + 4, y1 - 4), slice(x0 + 4, x0 + pw - 4))
    diff = np.abs(f[box] - base[box]).sum(axis=2)
    sub = f[box]
    subject = (sub[..., 0] > 150) & (sub[..., 1] < 90) & (sub[..., 2] < 90) & (diff > 60)
    assert subject.mean() > 0.05, "le sujet de l'incrustation reste visible en bas à gauche"
    assert (diff[~subject] < 60).mean() > 0.9, "le fond vert doit être retiré (on voit la vidéo dessous)"

    t = c.post(f"/api/projects/{pid}/templates", json={"name": "Ma chaîne"}).json()
    assert any(x["id"] == t["id"] for x in c.get("/api/templates").json())
    pid2 = _project(rush, "Autre vidéo")
    p2 = c.post(f"/api/projects/{pid2}/templates/{t['id']}/apply").json()
    assert p2["timeline"]["images"] and p2["timeline"]["captions"] == "aucun"
    assert c.get(f"/api/projects/{pid2}/assets/{p2['timeline']['images'][0]['asset']}").status_code == 200
