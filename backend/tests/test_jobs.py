"""Tâches : annulation, surveillance des tâches bloquées, historique, étapes et persistance."""
import json
import threading
import time

from fastapi.testclient import TestClient

from app import config
from app import main as main_mod
from app.jobs import MAX_STEPS, Job, JobManager, manager
from app.main import app

c = TestClient(app)


def _wait(job_id, statuses, timeout):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.05)
    raise TimeoutError(f"{job_id} : {j['status']}")


def _spin(ctx):
    while True:
        ctx.check()
        time.sleep(0.05)


def test_cancel_running_job_then_next_one_starts():
    job = manager.submit("p_cancel", "test", _spin)
    _wait(job.id, ("en_cours",), 2)
    t0 = time.time()
    r = c.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["cancel_requested"] is True
    j = _wait(job.id, ("annulee",), 2)
    assert time.time() - t0 < 2 and j["finished_at"] is not None
    nxt = manager.submit("p_cancel", "test", lambda ctx: "ok")
    assert _wait(nxt.id, ("termine",), 2)["result"] == "ok"


def test_cancel_waiting_job_is_immediate():
    first = manager.submit("p_wait", "test", _spin)
    _wait(first.id, ("en_cours",), 2)
    second = manager.submit("p_wait", "test", lambda ctx: "jamais")
    assert c.post(f"/api/jobs/{second.id}/cancel").json()["status"] == "annulee"
    c.post(f"/api/jobs/{first.id}/cancel")
    _wait(first.id, ("annulee",), 2)
    time.sleep(0.5)  # la tâche annulée en attente ne doit pas se lancer ensuite
    assert c.get(f"/api/jobs/{second.id}").json()["status"] == "annulee"


def test_cancel_unknown_job():
    assert c.post("/api/jobs/j_inconnu/cancel").status_code == 404


def test_watchdog_fails_stuck_job_and_frees_project():
    release = threading.Event()

    def stuck(ctx):
        ctx.step("calcul bloqué")
        release.wait(30)
        return "trop tard"

    job = manager.submit("p_stuck", "test", stuck)
    job.timeout_s = 0.5
    try:
        j = _wait(job.id, ("erreur",), 10)
        assert "Aucune réponse" in j["error"] and "calcul bloqué" in j["error"]
        assert j["finished_at"] is not None
        nxt = manager.submit("p_stuck", "test", lambda ctx: "ok")  # le verrou du projet est libéré
        assert _wait(nxt.id, ("termine",), 3)["result"] == "ok"
    finally:
        release.set()
    time.sleep(0.3)  # le fil bloqué finit : son résultat ne doit pas écraser l'erreur
    assert c.get(f"/api/jobs/{job.id}").json()["status"] == "erreur"


def test_project_jobs_history_and_steps():
    def work(ctx):
        ctx.step("étape A", 0.2, stage="analyse")
        ctx.step("étape A", 0.3)  # même message : pas de doublon
        ctx.step("étape B", 0.6)
        for k in range(MAX_STEPS + 20):
            ctx.step(f"boucle {k}")

    a = manager.submit("p_hist", "montage", work)
    _wait(a.id, ("termine",), 5)
    b = manager.submit("p_hist", "test", lambda ctx: 1 / 0)
    _wait(b.id, ("erreur",), 5)
    assert c.get("/api/projects/p_hist/jobs").json() == []
    hist = c.get("/api/projects/p_hist/jobs?all=1").json()
    assert [j["id"] for j in hist] == [b.id, a.id]
    assert [j["status"] for j in hist] == ["erreur", "termine"]
    got = hist[1]
    assert got["stage"] == "analyse" and got["timeout_s"] == 3600 and hist[0]["timeout_s"] == 600
    assert got["started_at"] and got["finished_at"] and got["updated_at"] >= got["started_at"]
    assert len(got["steps"]) == MAX_STEPS and got["steps"][-1]["message"] == f"boucle {MAX_STEPS + 19}"
    assert set(got["steps"][0]) == {"t", "message", "progress", "stage"}
    assert got["steps"][0]["stage"] == "analyse" and got["steps"][1]["stage"] == "analyse"  # l'étape reste jusqu'à la suivante


def test_shutdown_cancels_active_jobs():
    """À l'arrêt du serveur (fin du lifespan), les tâches encore actives sont annulées."""
    with TestClient(app) as client:
        job = manager.submit("p_stop", "test", _spin)
        _wait(job.id, ("en_cours",), 2)
        waiting = manager.submit("p_stop", "test", lambda ctx: "jamais")
        assert client.get(f"/api/jobs/{waiting.id}").json()["status"] == "en_attente"
    _wait(job.id, ("annulee",), 2)
    assert c.get(f"/api/jobs/{waiting.id}").json()["status"] == "annulee"
    assert manager.cancel_all() == 0


def _wait_file(path, pred, timeout=4):
    end = time.time() + timeout
    while time.time() < end:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if pred(data):
                return data
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise TimeoutError(str(path))


def test_jobs_are_persisted_and_interrupted_ones_fail_on_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mgr = JobManager()
    done = mgr.submit("p_persist", "test", lambda ctx: "ok")
    path = tmp_path / "jobs.json"
    data = _wait_file(path, lambda d: any(j["id"] == done.id and j["status"] == "termine" for j in d))
    assert [j["id"] for j in data] == [done.id] and not path.with_suffix(".json.tmp").exists()

    release = threading.Event()

    def run(ctx):
        ctx.step("en route", 0.3)
        release.wait(10)

    live = mgr.submit("p_persist", "montage", run)
    try:
        _wait_file(path, lambda d: any(j["id"] == live.id and j["status"] == "en_cours" and j["message"] == "en route" for j in d))
        # Redémarrage : un gestionnaire neuf relit le fichier.
        fresh = JobManager()
        got = fresh.get(live.id)
        assert got.status == "erreur" and got.error == "Interrompue par un redémarrage de l'application"
        assert got.finished_at is not None and got.message == "en route"
        assert fresh.get(done.id).status == "termine" and fresh.get(done.id).result == "ok"  # l'historique reste tel quel
        assert [j.id for j in fresh.for_project("p_persist")] == []  # rien n'est relancé
        assert {j.id for j in fresh.for_project("p_persist", all=True)} == {done.id, live.id}
        # Le fichier est mis à jour tout de suite : plus aucune tâche active dedans.
        assert all(j["status"] != "en_cours" for j in json.loads(path.read_text(encoding="utf-8")))
    finally:
        release.set()


def test_persisted_history_keeps_last_100(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mgr = JobManager()
    for k in range(105):
        mgr.jobs[f"j_{k:03d}"] = Job(id=f"j_{k:03d}", project_id="p", kind="test", status="termine", created_at=1000 + k)
    mgr.flush()
    data = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert len(data) == 100 and data[0]["id"] == "j_005" and data[-1]["id"] == "j_104"
    assert len(JobManager().jobs) == 100


def test_restored_jobs_are_served_by_the_route(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    old = Job(id="j_old", project_id="p_restore", kind="montage", status="en_cours", started_at=1.0)
    (tmp_path / "jobs.json").write_text(json.dumps([old.model_dump(mode="json")]), encoding="utf-8")
    fresh = JobManager()
    monkeypatch.setattr(main_mod, "manager", fresh)
    hist = c.get("/api/projects/p_restore/jobs?all=1").json()
    assert [(j["id"], j["status"], j["error"]) for j in hist] == [("j_old", "erreur", "Interrompue par un redémarrage de l'application")]
    assert c.get("/api/projects/p_restore/jobs").json() == []
