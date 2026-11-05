"""Diagnostic du poste : FFmpeg, GPU, Whisper, voix, Claude, dossiers, tâches.

Chaque vérification renvoie `{"ok": bool, "level": "ok" | "warn" | "error", ...détails, "fix": str | None}` ;
`fix` dit en français quoi faire quand ça ne va pas. Aucune vérification ne lève : un échec devient `ok: False`.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from . import config, cuda, downloads, settings
from .analysis import transcribe
from .jobs import ACTIVE, manager
from .media import ffmpeg

log = logging.getLogger(__name__)

CACHE_TTL = 30.0  # secondes
SUBPROCESS_TIMEOUT = 10
DISK_WARN_GB = 5.0
DISK_ERROR_GB = 1.0
CUDA_COMMAND = "backend\\.venv\\Scripts\\python -m pip install -r backend/requirements-cuda.txt"

LABELS = {
    "ffmpeg": "FFmpeg", "encoder": "Encodeur", "gpu": "Carte graphique", "whisper": "Transcription",
    "voices": "Voix", "claude": "Réalisateur", "folders": "Dossiers", "jobs": "Tâches",
}

_cache: tuple[float, dict] | None = None
_cache_lock = threading.Lock()


def _block(ok: bool, fix: str | None = None, bad: str = "warn", **details) -> dict:
    """`bad` : gravité quand `ok` est faux (« warn » : une fonction en moins ; « error » : l'app ne marche pas)."""
    return {"ok": ok, "level": "ok" if ok else bad, **details, "fix": None if ok and not fix else fix}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT)


def _check_ffmpeg() -> dict:
    out = {"path": ffmpeg.FFMPEG, "ffprobe": ffmpeg.FFPROBE, "version": None, "libass": False}
    try:
        p = _run([ffmpeg.FFMPEG, "-version"])
    except (OSError, subprocess.SubprocessError) as e:
        return _block(False, "FFmpeg est introuvable : installe-le (winget install Gyan.FFmpeg) ou indique son "
                             "chemin avec MONTEUR_FFMPEG, puis relance l'application.", "error", **out, error=str(e)[:200])
    first = (p.stdout or "").splitlines()
    out["version"] = first[0].strip() if first else None
    out["libass"] = "--enable-libass" in (p.stdout or "")
    if p.returncode != 0 or not out["version"]:
        return _block(False, "FFmpeg ne répond pas correctement : réinstalle-le.", "error", **out)
    try:
        probe = _run([ffmpeg.FFPROBE, "-version"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        probe = False
    out["ffprobe_ok"] = probe
    if not probe:
        return _block(False, "FFprobe est introuvable : il est livré avec FFmpeg, réinstalle-le.", "error", **out)
    if not out["libass"]:
        return _block(False, "Cette version de FFmpeg n'a pas libass : les sous-titres ne pourront pas être incrustés. "
                             "Installe une version « full » (winget install Gyan.FFmpeg).", "warn", **out)
    return _block(True, **out)


def _check_gpu() -> dict:
    out = {"name": None, "memory": None}
    exe = shutil.which("nvidia-smi")
    if not exe:
        return _block(True, "Aucune carte NVIDIA détectée : la transcription et l'export utilisent le processeur "
                            "(plus lent, mais tout fonctionne).", **out)
    try:
        p = _run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader"])
    except (OSError, subprocess.SubprocessError) as e:
        return _block(False, "nvidia-smi ne répond pas : mets à jour le pilote NVIDIA.", **out, error=str(e)[:200])
    line = next(iter((p.stdout or "").strip().splitlines()), "")
    if p.returncode != 0 or not line:
        return _block(False, "Le pilote NVIDIA ne voit pas de carte : mets-le à jour, puis redémarre.", **out)
    name, _, mem = line.partition(",")
    out["name"], out["memory"] = name.strip(), mem.strip() or None
    return _block(True, **out)


def _check_encoder(gpu: dict) -> dict:
    enc, _ = ffmpeg.best_h264_encoder()
    hardware = enc != "libx264"
    if not hardware and gpu.get("name"):
        return _block(False, "Une carte NVIDIA est présente mais son encodeur (NVENC) n'est pas utilisable : mets à jour "
                             "le pilote. En attendant, l'export utilise le processeur.", name=enc, hardware=False)
    return _block(True, "Aucun encodeur matériel : l'export utilise le processeur (plus lent)." if not hardware else None,
                  name=enc, hardware=hardware)


def _cuda_state() -> tuple[bool, bool]:
    dll = cuda.add_dll_dirs()  # avant tout import de ctranslate2
    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False
    return has_cuda, bool(dll.get("cublas"))


def _check_whisper() -> dict:
    st = transcribe.whisper_status()
    has_cuda, has_cublas = _cuda_state()
    try:
        import faster_whisper  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    cached = installed and downloads._whisper_cached(st["model"])
    out = {"model": st["model"], "device_requested": st["device_requested"], "cuda": has_cuda, "cublas": has_cublas,
           "device_used": st["device_used"], "fallback_reason": st["fallback_reason"], "model_cached": cached}
    if os.environ.get("MONTEUR_DISABLE_WHISPER"):
        return _block(False, "La transcription est désactivée (variable MONTEUR_DISABLE_WHISPER) : retire-la.", **out)
    if not installed:
        return _block(False, "faster-whisper n'est pas installé : la transcription est indisponible. "
                             "Réinstalle les dépendances (pip install -r backend/requirements.txt).", **out)
    if st["fallback_reason"]:
        return _block(False, f"Le GPU n'a pas pu être utilisé ({st['fallback_reason']}) : la transcription tourne sur le processeur.", **out)
    if has_cuda and not has_cublas and st["device_requested"] != "cpu":
        return _block(False, f"Carte NVIDIA détectée, mais cuBLAS manque : {CUDA_COMMAND}", **out)
    if st["device_requested"] == "cuda" and not has_cuda:
        return _block(False, "Le GPU est demandé dans Réglages mais aucun GPU CUDA n'est disponible : choisis « Automatique » "
                             "ou « Processeur ».", **out)
    if not cached:
        return _block(False, f"Le modèle « {st['model']} » n'est pas encore sur le disque : il sera téléchargé à la première "
                             "transcription (Réglages > Transcription > Modèles sur le disque pour le faire à l'avance).", **out)
    return _block(True, **out)


def _check_voices() -> dict:
    from .ai import tts
    esp = tts._espeak()
    piper = tts.piper_available()
    vdir = tts.voices_dir()
    try:
        installed = sorted(p.name for p in vdir.glob("*.onnx")) if vdir.is_dir() else []
    except OSError:
        installed = []
    out = {"espeak": esp, "piper": piper, "voices_dir": str(vdir), "installed": installed}
    if not esp and not (piper and installed):
        return _block(False, "Aucune voix de synthèse : installe espeak-ng, ou une voix Piper "
                             "(Réglages > Voix), pour le doublage et les voix off.", **out)
    if not (piper and installed):
        return _block(True, "Seule la voix robotique espeak-ng est disponible : installe une voix Piper dans Réglages > Voix "
                            "pour un rendu naturel.", **out)
    return _block(True, **out)


def _check_claude() -> dict:
    pub = settings.public()
    available = config.claude_available()
    out = {"key_set": available, "key_source": pub["key_source"] if available else None, "model": config.CLAUDE_MODEL}
    if available:
        return _block(True, **out)
    return _block(False, "Pas de clé Claude : le montage utilise le réalisateur local (moins fin). "
                         "Ajoute ta clé dans Réglages > Claude.", **out)


def _check_folders() -> dict:
    from .ai import tts
    out = {"data": str(config.DATA_DIR), "projects": str(config.PROJECTS_DIR), "music": str(config.MUSIC_DIR),
           "voices": str(tts.voices_dir()), "project_count": 0, "free_gb": None, "writable": False}
    try:
        out["project_count"] = sum(1 for p in config.PROJECTS_DIR.iterdir() if p.is_dir())
    except OSError:
        pass
    out["writable"] = os.access(config.DATA_DIR, os.W_OK)
    try:
        out["free_gb"] = round(shutil.disk_usage(config.DATA_DIR).free / 1024 ** 3, 1)
    except OSError as e:
        return _block(False, "Le dossier de données est illisible : vérifie qu'il existe et que le disque est branché.",
                      "error", **out, error=str(e)[:200])
    if not out["writable"]:
        return _block(False, f"Impossible d'écrire dans {config.DATA_DIR} : change ses droits ou choisis un autre dossier "
                             "(MONTEUR_DATA_DIR).", "error", **out)
    if out["free_gb"] < DISK_WARN_GB:
        return _block(False, f"Il reste {out['free_gb']} Go sur le disque : libère de la place (projets inutiles, exports) "
                             "avant de monter de longues vidéos.", "error" if out["free_gb"] < DISK_ERROR_GB else "warn", **out)
    return _block(True, **out)


def _check_jobs() -> dict:
    return _block(True, active=sum(1 for j in list(manager.jobs.values()) if j.status in ACTIVE))


def _safe(name: str, fn: Callable[[], dict]) -> dict:
    """Une vérification qui plante ne doit jamais faire échouer le rapport."""
    try:
        return fn()
    except Exception as e:
        log.warning("Diagnostic « %s » en échec : %s", name, e)
        return _block(False, f"La vérification a échoué ({e.__class__.__name__}) : relance le diagnostic.", "warn",
                      error=(str(e) or e.__class__.__name__)[:200])


def _build() -> dict:
    rep = {"ffmpeg": _safe("ffmpeg", _check_ffmpeg), "gpu": _safe("gpu", _check_gpu)}
    rep["encoder"] = _safe("encoder", lambda: _check_encoder(rep["gpu"]))
    for name, fn in (("whisper", _check_whisper), ("voices", _check_voices), ("claude", _check_claude),
                     ("folders", _check_folders), ("jobs", _check_jobs)):
        rep[name] = _safe(name, fn)
    problems = [f"{LABELS[k]} : {b['fix'] or b.get('error') or 'à vérifier'}" for k, b in rep.items() if not b["ok"]]
    return {"generated_at": time.time(), "summary": {"ok": not problems, "problems": problems}, **rep,
            "system": {"platform": platform.platform(), "python": sys.version.split()[0]}}


def report(refresh: bool = False) -> dict:
    """Rapport complet, gardé 30 s en cache (`refresh=True` le recalcule). Ne pas modifier le dictionnaire renvoyé."""
    global _cache
    with _cache_lock:
        if refresh or _cache is None or time.monotonic() - _cache[0] > CACHE_TTL:
            _cache = (time.monotonic(), _build())
        return _cache[1]
