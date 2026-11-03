"""File de tâches en arrière-plan avec progression consultable."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from pydantic import BaseModel, Field

from . import config
from .models import new_id

log = logging.getLogger(__name__)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: new_id("j_"))
    project_id: str
    kind: str
    status: str = "en_attente"  # en_attente | en_cours | termine | erreur
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: Any = None
    created_at: float = Field(default_factory=time.time)


class JobContext:
    def __init__(self, job: Job):
        self.job = job

    def step(self, message: str, progress: float | None = None) -> None:
        self.job.message = message
        if progress is not None:
            self.job.progress = round(max(self.job.progress, min(1.0, progress)), 4)

    def sub(self, lo: float, hi: float, message: str | None = None) -> Callable[[float], None]:
        """Callback de progression qui mappe 0..1 sur [lo, hi]."""
        if message:
            self.step(message, lo)

        def cb(p: float) -> None:
            self.job.progress = round(max(self.job.progress, lo + (hi - lo) * min(1.0, p)), 4)

        return cb


class JobManager:
    def __init__(self, workers: int = config.MAX_WORKERS):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.jobs: dict[str, Job] = {}
        self._project_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _plock(self, project_id: str) -> threading.Lock:
        with self._lock:
            return self._project_locks.setdefault(project_id, threading.Lock())

    def submit(self, project_id: str, kind: str, fn: Callable[[JobContext], Any]) -> Job:
        job = Job(project_id=project_id, kind=kind)
        self.jobs[job.id] = job

        def runner():
            # Une seule tâche à la fois par projet : évite les écritures concurrentes.
            with self._plock(project_id):
                job.status = "en_cours"
                try:
                    job.result = fn(JobContext(job))
                    job.progress = 1.0
                    job.status = "termine"
                    if not job.message:
                        job.message = "Terminé"
                except Exception as e:
                    log.error("Tâche %s échouée : %s", job.id, traceback.format_exc())
                    job.status = "erreur"
                    job.error = str(e) or e.__class__.__name__

        self.pool.submit(runner)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def for_project(self, project_id: str) -> list[Job]:
        return [j for j in self.jobs.values() if j.project_id == project_id]


manager = JobManager()
