"""Réglages de l'utilisateur (clé Claude, modèles, Whisper, port), rangés dans DATA_DIR/settings.json.

Priorité : variable d'environnement > fichier > valeur par défaut. La clé n'est jamais renvoyée ni journalisée.
"""
from __future__ import annotations

import logging
import os
import re

from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger(__name__)

SETTINGS_PATH = config.DATA_DIR / "settings.json"
DEVICES = ("auto", "cuda", "cpu")
EFFORTS = ("low", "medium", "high", "max")
# Champ -> variable d'environnement qui l'emporte sur le fichier.
_ENV = {
    "claude_model": "MONTEUR_CLAUDE_MODEL", "claude_effort": "MONTEUR_CLAUDE_EFFORT",
    "whisper_model": "MONTEUR_WHISPER_MODEL", "whisper_device": "MONTEUR_WHISPER_DEVICE",
    "whisper_language": "MONTEUR_WHISPER_LANGUAGE",
}
_ENV_KEY = os.environ.get("ANTHROPIC_API_KEY") or None  # clé fournie par l'environnement : on ne l'écrase pas


class Settings(BaseModel):
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    whisper_model: str = "small"
    whisper_device: str = "auto"  # auto | cuda | cpu
    whisper_language: str | None = None  # None = détection auto
    port: int = 8000


_current = Settings()


def _check(s: Settings) -> None:
    key = s.anthropic_api_key
    if key and (not key.isascii() or re.search(r"\s", key)):
        raise ValueError("La clé Claude ne doit contenir ni espace ni caractère accentué.")
    if s.whisper_device not in DEVICES:
        raise ValueError(f"Appareil Whisper inconnu : choisis parmi {', '.join(DEVICES)}.")
    if s.claude_effort not in EFFORTS:
        raise ValueError(f"Effort Claude inconnu : choisis parmi {', '.join(EFFORTS)}.")
    if not s.claude_model.strip() or not s.whisper_model.strip():
        raise ValueError("Le nom du modèle ne peut pas être vide.")
    if s.whisper_language is not None and not re.fullmatch(r"[a-z]{2,3}", s.whisper_language):
        raise ValueError("Langue de transcription invalide (code à 2 ou 3 lettres, ex. fr).")
    if not 1024 <= s.port <= 65535:
        raise ValueError("Le port doit être compris entre 1024 et 65535.")


def _sync() -> None:
    """Applique les réglages courants à l'environnement et à `config` (env > fichier > défaut)."""
    s = _current
    if not _ENV_KEY:
        if s.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = s.anthropic_api_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    eff = {f: os.environ.get(var) or getattr(s, f) for f, var in _ENV.items()}
    config.CLAUDE_MODEL, config.CLAUDE_EFFORT = eff["claude_model"], eff["claude_effort"]
    config.WHISPER_MODEL, config.WHISPER_DEVICE = eff["whisper_model"], eff["whisper_device"]
    config.WHISPER_LANGUAGE = eff["whisper_language"] or None


def load() -> Settings:
    """Lit le fichier (absent ou corrompu : valeurs par défaut) et l'applique."""
    global _current
    try:
        s = Settings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
        s.whisper_language = s.whisper_language or None
        _check(s)
    except (OSError, ValueError):
        s = Settings()
    _current = s
    _sync()
    return s


def apply(patch: dict) -> Settings:
    """Met à jour les champs présents dans `patch` (les autres restent), valide, enregistre et applique."""
    global _current
    from . import storage
    from .analysis import transcribe

    old = _current
    data = old.model_dump()
    data.update({k: v for k, v in patch.items() if k in Settings.model_fields})
    if data["anthropic_api_key"] is None:
        data["anthropic_api_key"] = ""
    try:
        new = Settings.model_validate(data)
    except ValidationError as e:  # on ne répète pas la valeur reçue (ce peut être la clé)
        raise ValueError("Réglage invalide : " + ", ".join(str(err["loc"][0]) for err in e.errors())) from None
    new.anthropic_api_key = new.anthropic_api_key.strip()
    new.whisper_language = (new.whisper_language or "").strip().lower() or None
    _check(new)
    storage._write_json(SETTINGS_PATH, new.model_dump_json(indent=2))
    try:
        os.chmod(SETTINGS_PATH, 0o600)  # la clé n'est lisible que par l'utilisateur (sans effet sous Windows)
    except OSError:
        pass
    _current = new
    _sync()
    if (old.whisper_model, old.whisper_device, old.whisper_language) != (
            new.whisper_model, new.whisper_device, new.whisper_language):
        transcribe.reset_model()
    return new


def public() -> dict:
    """Réglages sans la clé : seulement si elle est présente, d'où elle vient et ses 4 derniers caractères."""
    s = _current
    key = _ENV_KEY or s.anthropic_api_key
    out = s.model_dump(exclude={"anthropic_api_key"})
    out.update(
        anthropic_api_key_set=bool(key),
        anthropic_api_key_hint=("…" + key[-4:] if len(key) >= 12 else "…") if key else None,
        key_source="env" if _ENV_KEY else ("fichier" if key else None),
        env_overrides=[f for f, var in _ENV.items() if os.environ.get(var)],
    )
    return out


load()  # appliqué dès l'import (voir la fin de config.py)
