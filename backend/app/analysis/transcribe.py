"""Transcription au mot près avec faster-whisper (optionnelle)."""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Callable

import numpy as np

from .. import config, cuda
from ..models import Transcript, Word

log = logging.getLogger(__name__)

FILLERS = {
    "euh", "heu", "hum", "hmm", "bah", "ben", "euhm", "eh", "uh", "um", "uhm", "erm", "mmh", "hein",
}
_model = None
_device_used: str | None = None
_fallback_reason: str | None = None
_model_lock = threading.Lock()


class TranscriptionUnavailable(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^\wà-ÿ']", "", text.lower())


def is_filler(text: str) -> bool:
    return _norm(text) in FILLERS


def whisper_status() -> dict:
    """Appareil demandé et appareil réellement utilisé (None tant que le modèle n'est pas chargé)."""
    return {"device_requested": config.WHISPER_DEVICE, "device_used": _device_used,
            "fallback_reason": _fallback_reason, "model": config.WHISPER_MODEL}


def reset_model() -> None:
    """Oublie le modèle chargé : le prochain appel le recharge avec les réglages courants."""
    global _model, _device_used, _fallback_reason
    with _model_lock:
        _model = _device_used = _fallback_reason = None


def _load_model():
    global _model, _device_used, _fallback_reason
    with _model_lock:
        if _model is not None:
            return _model
        if os.environ.get("MONTEUR_DISABLE_WHISPER"):
            raise TranscriptionUnavailable("désactivée par MONTEUR_DISABLE_WHISPER")
        cuda.add_dll_dirs()  # avant tout import de ctranslate2
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise TranscriptionUnavailable("faster-whisper n'est pas installé") from e
        name, device = config.WHISPER_MODEL, config.WHISPER_DEVICE
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        _fallback_reason = None
        if device == "cuda":
            try:
                model = WhisperModel(name, device="cuda", compute_type="float16")
                # Les DLL CUDA ne sont chargées qu'au premier calcul : on le force ici plutôt qu'en plein montage.
                list(model.transcribe(np.zeros(16000, np.float32), beam_size=1, vad_filter=False)[0])
                _model, _device_used = model, "cuda"
                return _model
            except Exception as e:
                _fallback_reason = str(e)[:200]
                log.warning("Whisper sur GPU impossible, repli sur le processeur : %s", e)
        try:
            _model = WhisperModel(name, device="cpu", compute_type="int8")
        except Exception as e:
            raise TranscriptionUnavailable(f"Impossible de charger le modèle Whisper « {name} » : {e}") from e
        _device_used = "cpu"
        return _model


def ensure_loaded() -> None:
    """Charge le modèle maintenant (choix de l'appareil et repli CPU compris)."""
    _load_model()


def transcribe(audio: np.ndarray, media_id: str, duration: float,
               on_progress: Callable[[float], None] | None = None, language: str | None = None) -> Transcript:
    model = _load_model()
    segments, info = model.transcribe(
        audio,
        language=language or config.WHISPER_LANGUAGE,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        # Indique à Whisper de conserver les hésitations pour qu'on puisse les couper.
        initial_prompt="Euh, bon, alors, hum, voilà.",
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append(Word(start=round(w.start, 3), end=round(w.end, 3), text=text,
                              prob=round(w.probability, 3), filler=is_filler(text)))
        if on_progress and duration:
            on_progress(min(1.0, seg.end / duration))
    return Transcript(media_id=media_id, language=info.language, words=words)
