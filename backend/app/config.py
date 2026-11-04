"""Configuration globale, lue depuis les variables d'environnement."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("MONTEUR_DATA_DIR", ROOT / "data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
MUSIC_DIR = Path(os.environ.get("MONTEUR_MUSIC_DIR", DATA_DIR / "music")).resolve()
FRONTEND_DIST = ROOT / "frontend" / "dist"

# Transcription (faster-whisper)
WHISPER_MODEL = os.environ.get("MONTEUR_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("MONTEUR_WHISPER_DEVICE", "auto")  # auto | cpu | cuda
WHISPER_LANGUAGE = os.environ.get("MONTEUR_WHISPER_LANGUAGE") or None  # None = détection auto

# Réalisateur IA (Claude). Sans clé, l'app utilise le réalisateur heuristique local.
CLAUDE_MODEL = os.environ.get("MONTEUR_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("MONTEUR_CLAUDE_EFFORT", "medium")

# Analyse
PROXY_HEIGHT = int(os.environ.get("MONTEUR_PROXY_HEIGHT", "540"))
ANALYSIS_FPS = 5
AUDIO_SR = 16000
ENVELOPE_HOP = 0.05  # 50 ms

MAX_WORKERS = int(os.environ.get("MONTEUR_WORKERS", "2"))


def claude_available() -> bool:
    if os.environ.get("MONTEUR_DISABLE_CLAUDE"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


for d in (PROJECTS_DIR, MUSIC_DIR):
    d.mkdir(parents=True, exist_ok=True)
