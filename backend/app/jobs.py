"""File de tâches en arrière-plan avec progression consultable, annulation et surveillance des tâches bloquées."""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from pydantic import BaseModel, Field, PrivateAttr

from . import config
from .models import new_id

log = logging.getLogger(__name__)

ACTIVE = ("en_attente", "en_cours")
TIMEOUTS = {"montage": 3600, "export": 7200, "telechargement": 1800, "demo": 3600}  # secondes sans mise à jour avant de déclarer la tâche bloquée
DEFAULT_TIMEOUT = 600
MAX_STEPS = 200
WATCHDOG_INTERVAL = 5.0
KEEP_JOBS = 100  # tâches gardées dans jobs.json
DUMP_INTERVAL = 1.0  # secondes minimum entre deux écritures de jobs.json
INTERRUPTED = "Interrompue par un redémarrage de l'application"


class JobCancelled(Exception):
    """La tâche a été annulée par l'utilisateur : remonte jusqu'au runner."""


class Job(BaseModel):
    id: str = Field(default_factory=lambda: new_id("j_"))
    project_id: str
    kind: str
    status: str = "en_attente"  # en_attente | en_cours | termine | erreur | annulee
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Any = None
    created_at: float = Field(default_factory=time.time)
    cancel_requested: bool = False
    started_at: float | None = None
    updated_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    timeout_s: float = DEFAULT_TIMEOUT
    stage: str | None = None
    preview: str | None = None  # texte en cours d'écriture (réponse du réalisateur au fil de l'eau)
    steps: list[dict] = Field(default_factory=list)  # {t, message, progress, stage}
    _cancel: threading.Event = PrivateAttr(default_factory=threading.Event)


class JobContext:
    def __init__(self, job: Job, on_change: Callable[[], None] | None = None):
        self.job = job
        self._on_change = on_change
        self.cancel_event: threading.Event = job._cancel

    def check(self) -> None:
        """Lève JobCancelled si l'utilisateur a annulé la tâche."""
        if self.job.cancel_requested:
            raise JobCancelled()

    def step(self, message: str, progress: float | None = None, stage: str | None = None) -> None:
        self.check()
        j = self.job
        if progress is not None:
            j.progress = round(max(j.progress, min(1.0, progress)), 4)
        if stage:
            j.stage = stage
        if message != j.message:
            j.steps.append({"t": time.time(), "message": message, "progress": j.progress, "stage": j.stage})
            del j.steps[:-MAX_STEPS]
        j.message = message
        j.updated_at = time.time()
        if self._on_change:
            self._on_change()

    def preview(self, text: str) -> None:
        """Texte en cours (réponse diffusée) : lu par l'interface à chaque sondage, sans écriture disque."""
        self.job.preview = text[-8000:] if text else None
        self.job.updated_at = time.time()

    def sub(self, lo: float, hi: float, message: str | None = None) -> Callable[[float], None]:
        """Callback de progression qui mappe 0..1 sur [lo, hi]."""
        if message:
            self.step(message, lo)

        def cb(p: float) -> None:
            self.check()
            self.job.progress = round(max(self.job.progress, lo + (hi - lo) * min(1.0, p)), 4)
            self.job.updated_at = time.time()

        return cb


class ProjectGate:
    """Une seule tâche à la fois par projet. Contrairement à un verrou, on peut le reprendre de force
    à une tâche bloquée : son `release` tardif ne libère alors pas la place d'une autre."""

    def __init__(self):
        self._cond = threading.Condition()
        self.owner: str | None = None

    def acquire(self, job_id: str, cancel: threading.Event | None = None) -> bool:
        """Attend son tour. Renvoie False si la tâche est annulée pendant l'attente."""
        with self._cond:
            while self.owner is not None:
                if cancel is not None and cancel.is_set():
                    return False
                self._cond.wait(0.2)
            self.owner = job_id
            return True

    def release(self, job_id: str) -> None:
        with self._cond:
            if self.owner == job_id:
                self.owner = None
                self._cond.notify_all()

    def force_release(self) -> None:
        with self._cond:
            self.owner = None
            self._cond.notify_all()


class JobManager:
    def __init__(self, workers: int = config.MAX_WORKERS):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.jobs: dict[str, Job] = {}
        self._gates: dict[str, ProjectGate] = {}
        self._lock = threading.Lock()
        self._path = config.DATA_DIR / "jobs.json"
        self._dump_lock = threading.Lock()
        self._last_dump = -DUMP_INTERVAL
        self._dump_timer: threading.Timer | None = None
        self.restore()
        threading.Thread(target=self._watchdog, name="job-watchdog", daemon=True).start()

    def restore(self) -> None:
        """Relit jobs.json. Une tâche encore active au moment de l'arrêt est passée en erreur : on ne relance jamais
        une tâche toute seule. Les autres sont gardées telles quelles (historique)."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now, changed = time.time(), False
        for item in raw if isinstance(raw, list) else []:
            try:
                job = Job.model_validate(item)
            except ValueError:
                continue
            if job.status in ACTIVE:
                job.status, job.error = "erreur", INTERRUPTED
                job.finished_at = job.updated_at = now
                changed = True
            self.jobs.setdefault(job.id, job)
        if changed:
            self._dump(force=True)

    def _dump(self, force: bool = False) -> None:
        """Écrit les dernières tâches dans jobs.json (fichier temporaire puis remplacement), au plus une fois par seconde :
        un appel trop proche programme une écriture finale, pour que le dernier état ne soit jamais perdu."""
        with self._dump_lock:
            wait = self._last_dump + DUMP_INTERVAL - time.monotonic()
            if not force and wait > 0:
                if self._dump_timer is None:
                    self._dump_timer = threading.Timer(wait, self._dump, kwargs={"force": True})
                    self._dump_timer.daemon = True
                    self._dump_timer.start()
                return
            self._dump_timer = None
            self._last_dump = time.monotonic()
            try:
                recent = sorted(list(self.jobs.values()), key=lambda j: j.created_at)[-KEEP_JOBS:]
                data = json.dumps([j.model_dump(mode="json") for j in recent], ensure_ascii=False)
                tmp = self._path.with_suffix(".json.tmp")
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(self._path)
            except Exception:  # l'historique est un confort : il ne doit jamais faire échouer une tâche
                log.warning("jobs.json non écrit : %s", traceback.format_exc())

    def flush(self) -> None:
        """Écrit jobs.json tout de suite (arrêt de l'application)."""
        self._dump(force=True)

    def _gate(self, project_id: str) -> ProjectGate:
        with self._lock:
            return self._gates.setdefault(project_id, ProjectGate())

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        """Passe une tâche encore active à son état final (sans toucher à celles déjà terminées ou expirées)."""
        with self._lock:
            if job.status not in ACTIVE:
                return
            job.status, job.error = status, error
            job.finished_at = job.updated_at = time.time()
        self._dump()

    def submit(self, project_id: str, kind: str, fn: Callable[[JobContext], Any]) -> Job:
        job = Job(project_id=project_id, kind=kind, timeout_s=TIMEOUTS.get(kind, DEFAULT_TIMEOUT))
        self.jobs[job.id] = job
        self._dump()
        gate = self._gate(project_id)

        def runner():
            # Une seule tâche à la fois par projet : évite les écritures concurrentes.
            if not gate.acquire(job.id, job._cancel):
                return
            try:
                with self._lock:
                    if job.status != "en_attente":  # annulée pendant l'attente
                        return
                    job.status = "en_cours"
                    job.started_at = job.updated_at = time.time()
                self._dump()
                result = fn(JobContext(job, self._dump))
                with self._lock:
                    if job.status == "en_cours":  # sinon la surveillance l'a déjà déclarée bloquée
                        job.result, job.progress = result, 1.0
                        job.message = job.message or "Terminé"
                        job.status = "termine"
                        job.finished_at = job.updated_at = time.time()
                self._dump()
            except JobCancelled:
                job.message = "Annulée"
                self._finish(job, "annulee")
            except Exception as e:
                log.error("Tâche %s échouée : %s", job.id, traceback.format_exc())
                self._finish(job, "erreur", str(e) or e.__class__.__name__)
            finally:
                gate.release(job.id)

        self.pool.submit(runner)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def for_project(self, project_id: str, all: bool = False) -> list[Job]:
        """Tâches actives du projet ; avec `all`, les 20 dernières tous statuts confondus."""
        mine = [j for j in list(self.jobs.values()) if j.project_id == project_id]
        if not all:
            return [j for j in mine if j.status in ACTIVE]
        return sorted(mine, key=lambda j: j.created_at, reverse=True)[:20]

    def cancel(self, job_id: str) -> Job | None:
        """Demande l'annulation. Une tâche en attente s'arrête tout de suite, une tâche en cours au prochain point de contrôle."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.status in ACTIVE:
                job.cancel_requested = True
                job._cancel.set()
                if job.status == "en_attente":
                    job.status, job.message = "annulee", "Annulée"
                    job.finished_at = job.updated_at = time.time()
        self._dump()
        return job

    def cancel_all(self) -> int:
        """Annule toutes les tâches actives. Renvoie leur nombre."""
        n = 0
        for j in list(self.jobs.values()):
            if j.status in ACTIVE:
                self.cancel(j.id)
                n += 1
        return n

    def _watchdog(self) -> None:
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            try:
                self._expire_stuck()
            except Exception:  # la surveillance ne doit jamais s'arrêter
                log.error("Surveillance des tâches : %s", traceback.format_exc())

    def _expire_stuck(self) -> None:
        """Passe en erreur les tâches sans signe de vie depuis `timeout_s` et libère leur projet."""
        now = time.time()
        for job in list(self.jobs.values()):
            with self._lock:
                if job.status != "en_cours" or now - job.updated_at < job.timeout_s:
                    continue
                minutes = max(1, round(job.timeout_s / 60))
                job.status = "erreur"
                job.error = f"Aucune réponse depuis {minutes} min (bloquée pendant « {job.message} »)"
                job.finished_at = job.updated_at = now
                job.cancel_requested = True  # si le fil finit par réagir, il s'arrête au prochain contrôle
                job._cancel.set()
            self._dump()
            log.error("Tâche %s bloquée (%s), projet libéré", job.id, job.message)
            self._gate(job.project_id).force_release()


manager = JobManager()
