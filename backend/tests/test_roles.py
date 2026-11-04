"""Le réalisateur en rôles nommés : brief avant le montage, storyboard, montage et publication après."""
import time

from fastapi.testclient import TestClient

from app import storage
from app.ai import roles
from app.main import app
from app.models import Clip, EditSettings, Media, Project, Segment, Timeline

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_local_brief_and_storyboard():
    p = Project(name="r", media=[Media(id="m", filename="a.mp4", path="a.mp4", duration=120)])
    segs = [Segment(id="s1", media_id="m", start=0, end=4, text="Bonjour, aujourd'hui on teste un casque", score=0.5),
            Segment(id="s2", media_id="m", start=10, end=15, text="Et là, le son est vraiment bluffant", score=0.95),
            Segment(id="s3", media_id="m", start=30, end=34, text="Merci d'avoir regardé, à bientôt", score=0.4)]
    st = EditSettings(style="youtube", target_duration=60)
    b, by = roles.brief(p, segs, st)
    assert by == "local" and "60 s" in b["objectif"] and "bluffant" in b["moment_fort"]
    assert "Brief" in roles.brief_instructions(b)
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=4, segment_id="s1", score=0.5),
                         Clip(media_id="m", src_in=10, src_out=15, segment_id="s2", score=0.95),
                         Clip(media_id="m", src_in=30, src_out=34, segment_id="s3", score=0.4)])
    sb = roles.storyboard(tl, segs)
    assert [s["kind"] for s in sb] == ["accroche", "moment fort", "fin"]
    steps = roles.before_steps(b, by) + roles.after_steps(p, tl, segs, st, b)
    assert [s.role for s in steps] == ["stratege", "creatif", "storyboard", "montage", "publication"]
    mont = steps[3]
    assert mont.ok is False and "cible de 60 s" in mont.notes[0]  # 13 s pour 60 s visés


def test_auto_edit_fills_the_plan(rush):
    pid = c.post("/api/projects", json={"name": "Rôles"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit",
                        json={"style": "youtube", "instructions": "garde l'intro"}).json())["status"] == "termine"
    p = storage.load_project(pid)
    assert [s.title for s in p.timeline.plan] == ["Stratège", "Directeur créatif", "Storyboard", "Montage", "Publication"]
    assert p.settings.instructions == "garde l'intro"  # le brief guide le réalisateur sans s'ajouter aux consignes
