"""DLL CUDA (cuBLAS, cuDNN) installées par pip : à déclarer avant tout import de ctranslate2.

Les roues `nvidia-cublas-cu12` et `nvidia-cudnn-cu12` déposent leurs DLL dans <site-packages>/nvidia/<lib>/bin,
un dossier que Windows ne cherche pas tout seul.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_handles: list = []  # on garde les handles : fermés, les dossiers ne seraient plus cherchés
_found = {"cublas": False, "cudnn": False}


def _bin_dir(package: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    for loc in (spec.submodule_search_locations or []) if spec else []:
        d = Path(loc) / "bin"
        if d.is_dir():
            return d
    return None


def add_dll_dirs() -> dict:
    """Déclare les dossiers de DLL cuBLAS et cuDNN (Windows), une seule fois chacun. Renvoie ce qui est en place."""
    if sys.platform == "win32":
        for name in _found:
            d = None if _found[name] else _bin_dir(f"nvidia.{name}")
            if not d:
                continue
            try:
                _handles.append(os.add_dll_directory(str(d)))
                # Certaines bibliothèques chargent leurs DLL par le PATH classique.
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                _found[name] = True
            except OSError as e:
                log.warning("Dossier de DLL %s inutilisable : %s", d, e)
    return dict(_found)
