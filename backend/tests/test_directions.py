"""Trois directions créatives proposées avant le montage."""
import time

from fastapi.testclient import TestClient

from app import storage
from app.ai import directions
from app.main import app
from app.models import EditSettings

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_three_directions_then_edit(rush):
    pid = c.post("/api/projects", json={"name": "Directions"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    j = _wait(c.post(f"/api/projects/{pid}/directions", json={"platform": "tiktok", "tone": "fun"}).json())
    assert j["status"] == "termine", j
    ds = c.get(f"/api/projects/{pid}").json()["directions"]
    assert len(ds) == 3 and sum(d["recommended"] for d in ds) == 1
    assert len({d["settings"]["style"] for d in ds}) == 3  # trois directions vraiment différentes
    rec = next(d for d in ds if d["recommended"])
    assert rec["settings"]["style"] == "short"  # TikTok : le short est recommandé
    # Choisir une direction = monter avec ses réglages.
    settings = directions.apply(EditSettings(), storage.load_project(pid).directions[1])
    assert settings.style == "short" and settings.format == "9:16"
    j = _wait(c.post(f"/api/projects/{pid}/auto-edit", json=settings.model_dump()).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["format"] == "9:16" and tl["clips"]
