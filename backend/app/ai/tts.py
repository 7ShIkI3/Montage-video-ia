"""Voix de synthèse pour le doublage : Piper (voix naturelles, hors ligne) si une voix est installée,
sinon espeak-ng (robotique mais présent partout).

Voix Piper : déposer les fichiers `<langue>*.onnx` (+ `.onnx.json`) dans DATA_DIR/voices ou dans le dossier
indiqué par MONTEUR_PIPER_VOICES, par exemple `fr_FR-siwis-medium.onnx`. Paquet : `pip install piper-tts`.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

ESPEAK_VOICES = {
    "fr": "fr-fr", "en": "en-us", "es": "es", "de": "de", "it": "it", "pt": "pt-br", "nl": "nl", "pl": "pl",
    "tr": "tr", "ru": "ru", "ar": "ar", "hi": "hi", "ja": "ja", "ko": "ko", "zh": "cmn",
}


class TTSUnavailable(RuntimeError):
    pass


def voices_dir() -> Path:
    env = os.environ.get("MONTEUR_PIPER_VOICES")
    return Path(env) if env else config.DATA_DIR / "voices"


def piper_voice(lang: str) -> Path | None:
    d = voices_dir()
    if not d.is_dir():
        return None
    found = sorted(p for p in d.glob("*.onnx") if p.name.lower().startswith(lang.lower()))
    return found[0] if found else None


def _piper_module() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _piper_cmd() -> list[str] | None:
    """Exécutable `piper` (installation séparée), sinon le module Python s'il est importable (hors app compilée,
    où `-m` n'existe pas : on passe alors par l'API Python, voir `_synth_piper`)."""
    exe = shutil.which("piper")
    if exe:
        return [exe]
    if _piper_module() and not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "piper"]
    return None


def piper_available() -> bool:
    return _piper_module() or _piper_cmd() is not None


def _synth_piper(voice: Path, text: str, out: Path, rate: float) -> bool:
    """Synthèse Piper. API Python en priorité (marche aussi dans l'app compilée), sinon l'exécutable."""
    if _piper_module():
        import wave
        from piper import PiperVoice, SynthesisConfig
        v = _loaded_voices.get(voice)
        if v is None:
            v = _loaded_voices[voice] = PiperVoice.load(voice)
        with wave.open(str(out), "wb") as f:
            v.synthesize_wav(text, f, syn_config=SynthesisConfig(length_scale=1 / max(0.5, rate)))
        return out.exists() and out.stat().st_size > 1000
    cmd = [*_piper_cmd(), "--model", str(voice), "--output_file", str(out), "--length_scale", f"{1 / max(0.5, rate):.3f}"]
    p = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=120)
    if p.returncode != 0:
        log.warning("Piper : %s", p.stderr.decode(errors="replace")[-300:])
    return p.returncode == 0 and out.exists() and out.stat().st_size > 1000


_loaded_voices: dict = {}


def _espeak() -> str | None:
    found = shutil.which("espeak-ng") or shutil.which("espeak")
    if found or os.name != "nt":
        return found
    # Sous Windows, l'installeur espeak-ng n'ajoute pas toujours son dossier au PATH.
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if base and (Path(base) / "eSpeak NG" / "espeak-ng.exe").is_file():
            return str(Path(base) / "eSpeak NG" / "espeak-ng.exe")
    return None


def engine_for(lang: str) -> str | None:
    if piper_voice(lang) and piper_available():
        return "piper"
    if _espeak() and lang in ESPEAK_VOICES:
        return "espeak"
    return None


def status() -> dict:
    """Moteur de voix disponible pour chaque langue (affiché dans l'interface)."""
    return {lang: engine_for(lang) for lang in ESPEAK_VOICES}


def synthesize(text: str, lang: str, out: Path, rate: float = 1.0) -> str:
    """Écrit `text` lu dans la langue `lang` en WAV dans `out`. Renvoie le moteur utilisé."""
    eng = engine_for(lang)
    if eng == "piper":
        try:
            ok = _synth_piper(piper_voice(lang), text, out, rate)
        except Exception as e:  # voix corrompue, onnxruntime absent…
            log.warning("Piper a échoué : %s", e)
            ok = False
        if ok:
            return "piper"
        # Une voix Piper cassée ne doit pas bloquer le doublage : on retombe sur espeak.
        if not (_espeak() and lang in ESPEAK_VOICES):
            raise TTSUnavailable("Piper a échoué et espeak-ng est absent")
    if eng is None and not (_espeak() and lang in ESPEAK_VOICES):
        raise TTSUnavailable("Aucune voix de synthèse : installe espeak-ng (ou Piper avec une voix "
                             f"dans {voices_dir()}).")
    p = subprocess.run([_espeak(), "-v", ESPEAK_VOICES[lang], "-s", str(int(165 * rate)), "-w", str(out), text],
                       capture_output=True, timeout=120)
    if p.returncode != 0 or not out.exists():
        raise TTSUnavailable(f"espeak-ng a échoué : {p.stderr.decode(errors='replace')[-300:]}")
    return "espeak"
