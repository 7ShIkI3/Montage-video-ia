"""Transcription au mot près avec faster-whisper (optionnelle)."""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Callable

import numpy as np

from .. import config
from ..models import Transcript, Word

log = logging.getLogger(__name__)

FILLERS = {
    "euh", "heu", "hum", "hmm", "bah", "ben", "euhm", "eh", "uh", "um", "uhm", "erm", "mmh", "hein",
}
_model = None
_model_lock = threading.Lock()


class TranscriptionUnavailable(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^\wà-ÿ']", "", text.lower())


def is_filler(text: str) -> bool:
    return _norm(text) in FILLERS


def _load_model():
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        if os.environ.get("MONTEUR_DISABLE_WHISPER"):
            raise TranscriptionUnavailable("désactivée par MONTEUR_DISABLE_WHISPER")
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise TranscriptionUnavailable("faster-whisper n'est pas installé") from e
        device = config.WHISPER_DEVICE
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = "float16" if device == "cuda" else "int8"
        try:
            _model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
        except Exception as e:
            raise TranscriptionUnavailable(
                f"Impossible de charger le modèle Whisper « {config.WHISPER_MODEL} » : {e}"
            ) from e
        return _model


def transcribe(audio: np.ndarray, media_id: str, duration: float,
               on_progress: Callable[[float], None] | None = None) -> Transcript:
    model = _load_model()
    segments, info = model.transcribe(
        audio,
        language=config.WHISPER_LANGUAGE,
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
