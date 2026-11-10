"""Persistance des projets sur disque (un dossier par projet)."""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path

from . import config
from .models import Project, Segment, Transcript

log = logging.getLogger(__name__)

BACKUP_INTERVAL = 300.0  # secondes minimum entre deux sauvegardes automatiques
BACKUP_KEEP = 10  # nombre de sauvegardes gardées
_BACKUP_NAME = re.compile(r"^project-\d{8}-\d{6}-\d{3}\.json$")

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


def audio_dir(project_id: str) -> Path:
    """Sons du projet : voix off enregistrées, sons et musiques des pistes audio."""
    d = project_dir(project_id) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _translations_path(project_id: str) -> Path:
    return project_dir(project_id) / "translations.json"


def load_translations(project_id: str, lang: str | None) -> dict[str, str]:
    """Traductions des phrases de sous-titres déjà faites pour une langue (texte d'origine -> traduction)."""
    if not lang:
        return {}
    p = _translations_path(project_id)
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(lang, {})
    except (OSError, ValueError):
        return {}


def save_translations(project_id: str, lang: str, mapping: dict[str, str]) -> None:
    with _lock:
        p = _translations_path(project_id)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault(lang, {}).update(mapping)
        _write_json(p, json.dumps(data, ensure_ascii=False, indent=1))


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


# --- Sauvegardes automatiques -------------------------------------------------

def backups_dir(project_id: str) -> Path:
    return project_dir(project_id) / "backups"


def _backup_files(project_id: str) -> list[Path]:
    """Sauvegardes du projet, de la plus récente à la plus ancienne (le nom contient la date)."""
    d = backups_dir(project_id)
    if not d.is_dir():
        return []
    return sorted((f for f in d.iterdir() if _BACKUP_NAME.match(f.name)), key=lambda f: f.name, reverse=True)


def _write_backup(project_id: str, data: str, force: bool = False) -> None:
    """Copie datée de project.json. Sans `force`, au plus une toutes les BACKUP_INTERVAL secondes ;
    les BACKUP_KEEP plus récentes sont gardées."""
    files = _backup_files(project_id)
    now = time.time()
    if not force and files and now - files[0].stat().st_mtime < BACKUP_INTERVAL:
        return
    d = backups_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    ms = int(now * 1000)
    while True:  # deux copies dans la même milliseconde : la seconde prend la milliseconde suivante
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ms / 1000)) + f"-{ms % 1000:03d}"
        if not (d / f"project-{stamp}.json").exists():
            break
        ms += 1
    _write_json(d / f"project-{stamp}.json", data)
    for old in _backup_files(project_id)[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)


def backup_path(project_id: str, name: str) -> Path:
    """Chemin d'une sauvegarde par son nom ; refuse tout nom qui ne ressemble pas à une sauvegarde
    (dont les `..` et les séparateurs de chemin)."""
    if not _BACKUP_NAME.match(name or ""):
        raise ValueError("nom de sauvegarde invalide")
    path = backups_dir(project_id) / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def list_backups(project_id: str) -> list[dict]:
    """Sauvegardes lisibles du projet, de la plus récente à la plus ancienne."""
    out = []
    for f in _backup_files(project_id):
        try:
            p = Project.model_validate_json(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"name": f.name, "at": f.stat().st_mtime, "clips": len(p.timeline.clips),
                    "duration": p.timeline.duration})
    return out


def _load_latest_backup(project_id: str) -> Project | None:
    for f in _backup_files(project_id):
        try:
            return Project.model_validate_json(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def restore_backup(project_id: str, name: str) -> Project:
    """Remet une sauvegarde en place, après avoir gardé l'état courant comme sauvegarde."""
    with _lock:
        path = backup_path(project_id, name)
        data = path.read_text(encoding="utf-8")
        restored = Project.model_validate_json(data)  # refuse une sauvegarde illisible avant de toucher à quoi que ce soit
        current = project_dir(project_id) / "project.json"
        if current.exists():
            try:
                Project.model_validate_json(current.read_text(encoding="utf-8"))
                _write_backup(project_id, current.read_text(encoding="utf-8"), force=True)
            except (OSError, ValueError):
                log.warning("Projet %s : état courant illisible, non gardé avant la restauration", project_id)
        _write_json(current, data)
    return restored


def save_project(project: Project) -> Project:
    with _lock:
        project.ensure_versions()
        project.updated_at = time.time()
        d = project_dir(project.id)
        d.mkdir(parents=True, exist_ok=True)
        data = project.model_dump_json(indent=2)
        _write_json(d / "project.json", data)
        try:
            _write_backup(project.id, data)
        except OSError as e:  # une sauvegarde ratée ne doit jamais empêcher d'enregistrer
            log.warning("Sauvegarde automatique du projet %s impossible : %s", project.id, e)
    return project


def load_project(project_id: str) -> Project:
    with _lock:
        path = project_dir(project_id) / "project.json"
        if not path.exists():
            raise FileNotFoundError(project_id)
        try:
            p = Project.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as e:  # JSON corrompu (coupure pendant l'écriture, disque…) : dernière sauvegarde
            p = _load_latest_backup(project_id)
            if p is None:
                raise
            log.warning("Projet %s : project.json illisible (%s), dernière sauvegarde automatique utilisée",
                        project_id, str(e).splitlines()[0])
            try:
                shutil.copyfile(path, path.with_name("project.corrupt.json"))  # fichier abîmé gardé pour l'examiner
            except OSError:
                pass
    if p.active_version is None:  # projet créé avant les versions : on fige l'identifiant de la version
        save_project(p)
    return p


def list_projects() -> list[Project]:
    out = []
    for p in config.PROJECTS_DIR.glob("*/project.json"):
        try:
            out.append(Project.model_validate_json(p.read_text(encoding="utf-8")))
        except Exception:
            b = _load_latest_backup(p.parent.name)  # project.json abîmé : le projet reste listé
            if b:
                out.append(b)
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
