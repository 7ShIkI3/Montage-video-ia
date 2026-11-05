"""Tâches : annulation, surveillance des tâches bloquées, historique et étapes."""
import threading
import time

from fastapi.testclient import TestClient

from app.jobs import MAX_STEPS, manager
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
    assert set(got["steps"][0]) == {"t", "message", "progress"}


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
