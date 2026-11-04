"""Monter « comme cette vidéo de référence » : on copie le rythme des plans."""
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.ai import reference
from app.main import app

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


@pytest.fixture(scope="module")
def ref_video(tmp_path_factory):
    """12 plans de 1 s (couleurs franches qui changent) : un rythme très rapide."""
    out = tmp_path_factory.mktemp("ref") / "reference.mp4"
    colors = ["red", "blue", "green", "yellow", "white", "purple"] * 2
    inputs, labels = [], []
    for k, col in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color=c={col}:s=320x180:r=30:d=1"]
        labels.append(f"[{k}:v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
                    f"{''.join(labels)}concat=n={len(colors)}:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def test_analyze_reference(ref_video):
    prof = reference.analyze(ref_video)
    assert prof["shots"] == 12 and prof["mean_shot"] == pytest.approx(1.0, abs=0.1)
    assert "12 plans" in reference.describe(prof)


def test_edit_follows_reference_rhythm(rush, ref_video):
    pid = c.post("/api/projects", json={"name": "Référence"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    with ref_video.open("rb") as f:
        r = c.post(f"/api/projects/{pid}/reference", files={"file": ("reference.mp4", f, "video/mp4")})
    assert r.status_code == 200, r.text
    assert r.json()["mean_shot"] == pytest.approx(1.0, abs=0.1)
    j = _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube", "punch_zooms": False}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    m = tl["reference_match"]
    assert m and 2 <= m["rounds"] <= 5 and m["history"][0] > m["result_shot"]
    # Le rythme se rapproche nettement de la référence (le rush n'a que 2 phrases : on ne l'atteint pas forcément).
    assert m["result_shot"] < 0.6 * m["history"][0]
    assert any(cl["zoom"] > 1.05 for cl in tl["clips"])  # coupes dans la même prise rendues visibles
    assert c.delete(f"/api/projects/{pid}/reference").json()["ok"]
    assert c.get(f"/api/projects/{pid}").json()["reference"] is None
