"""Persistance des projets sur disque (un dossier par projet)."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from . import config
from .models import Project, Segment, Transcript

_lock = threading.RLock()
_plocks: dict[str, threading.Lock] = {}


def project_lock(project_id: str) -> threading.Lock:
    """Verrou d'un projet pour les lectures-modifications-écritures (API et tâches de fond)."""
    with _lock:
        return _plocks.setdefault(project_id, threading.Lock())


def update_project(project_id: str, fn) -> Project:
    """Relit le projet, applique `fn` dessus et l'enregistre, sans écraser ce qui a changé entre-temps."""
    with project_lock(project_id):
        p = load_project(project_id)
        fn(p)
        return save_project(p)


def project_dir(project_id: str) -> Path:
    if not project_id.replace("_", "").isalnum():
        raise ValueError("identifiant de projet invalide")
    return config.PROJECTS_DIR / project_id


def media_dir(project_id: str) -> Path:
    d = project_dir(project_id) / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir(project_id: str) -> Path:
    d = project_dir(project_id) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".mka"}


def safe_music_path(track: str | None) -> str | None:
    """Chemin de musique accepté seulement s'il est dans les données de l'app ou la bibliothèque.

    Empêche une timeline envoyée par un client de pointer vers n'importe quel fichier du serveur.
    """
    if not track:
        return None
    try:
        p = Path(track).resolve()
    except (OSError, ValueError):
        return None
    if p.suffix.lower() not in AUDIO_EXT:
        return None
    for root in (config.DATA_DIR, config.MUSIC_DIR):
        if p.is_relative_to(root):
            return str(p)
    return None


def assets_dir(project_id: str) -> Path:
    """Images du projet (logos, stickers, photos)."""
    d = project_dir(project_id) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir(project_id: str) -> Path:
    d = project_dir(project_id) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def save_project(project: Project) -> Project:
    with _lock:
        project.ensure_versions()
        project.updated_at = time.time()
        d = project_dir(project.id)
        d.mkdir(parents=True, exist_ok=True)
        _write_json(d / "project.json", project.model_dump_json(indent=2))
    return project


def load_project(project_id: str) -> Project:
    with _lock:
        path = project_dir(project_id) / "project.json"
        if not path.exists():
            raise FileNotFoundError(project_id)
        p = Project.model_validate_json(path.read_text(encoding="utf-8"))
    if p.active_version is None:  # projet créé avant les versions : on fige l'identifiant de la version
        save_project(p)
    return p


def list_projects() -> list[Project]:
    out = []
    for p in config.PROJECTS_DIR.glob("*/project.json"):
        try:
            out.append(Project.model_validate_json(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(out, key=lambda p: p.updated_at, reverse=True)


def delete_project(project_id: str) -> None:
    with _lock:
        shutil.rmtree(project_dir(project_id), ignore_errors=True)


# --- Caches d'analyse -------------------------------------------------------

def save_transcript(project_id: str, t: Transcript) -> None:
    _write_json(cache_dir(project_id) / f"{t.media_id}.transcript.json", t.model_dump_json())


def load_transcript(project_id: str, media_id: str) -> Transcript | None:
    p = cache_dir(project_id) / f"{media_id}.transcript.json"
    if p.exists():
        return Transcript.model_validate_json(p.read_text(encoding="utf-8"))
    return None


def save_segments(project_id: str, segments: list[Segment]) -> None:
    _write_json(
        cache_dir(project_id) / "segments.json",
        json.dumps([s.model_dump() for s in segments]),
    )


def load_segments(project_id: str) -> list[Segment]:
    p = cache_dir(project_id) / "segments.json"
    if not p.exists():
        return []
    return [Segment.model_validate(s) for s in json.loads(p.read_text(encoding="utf-8"))]


def save_signals(project_id: str, media_id: str, data: dict) -> None:
    _write_json(cache_dir(project_id) / f"{media_id}.signals.json", json.dumps(data))


def load_signals(project_id: str, media_id: str) -> dict | None:
    p = cache_dir(project_id) / f"{media_id}.signals.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None
