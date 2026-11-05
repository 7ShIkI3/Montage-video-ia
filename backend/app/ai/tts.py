"""Voix de synthèse pour le doublage : Piper (voix naturelles, hors ligne) si une voix est installée,
sinon espeak-ng (robotique mais présent partout).

Voix Piper : déposer les fichiers `<langue>*.onnx` (+ `.onnx.json`) dans DATA_DIR/voices ou dans le dossier
indiqué par MONTEUR_PIPER_VOICES, par exemple `fr_FR-siwis-medium.onnx`. Paquet : `pip install piper-tts`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

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


def _piper_cmd() -> list[str] | None:
    exe = shutil.which("piper")
    if exe:
        return [exe]
    try:
        import piper  # noqa: F401
        return [sys.executable, "-m", "piper"]
    except ImportError:
        return None


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
    if piper_voice(lang) and _piper_cmd():
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
        cmd = [*_piper_cmd(), "--model", str(piper_voice(lang)), "--output_file", str(out),
               "--length_scale", f"{1 / max(0.5, rate):.3f}"]
        p = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=120)
        if p.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            return "piper"
        # Une voix Piper cassée ne doit pas bloquer le doublage : on retombe sur espeak.
        if not (_espeak() and lang in ESPEAK_VOICES):
            raise TTSUnavailable(f"Piper a échoué : {p.stderr.decode(errors='replace')[-300:]}")
    if eng is None and not (_espeak() and lang in ESPEAK_VOICES):
        raise TTSUnavailable("Aucune voix de synthèse : installe espeak-ng (ou Piper avec une voix "
                             f"dans {voices_dir()}).")
    p = subprocess.run([_espeak(), "-v", ESPEAK_VOICES[lang], "-s", str(int(165 * rate)), "-w", str(out), text],
                       capture_output=True, timeout=120)
    if p.returncode != 0 or not out.exists():
        raise TTSUnavailable(f"espeak-ng a échoué : {p.stderr.decode(errors='replace')[-300:]}")
    return "espeak"
