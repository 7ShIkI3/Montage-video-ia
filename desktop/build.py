"""Construit l'application de bureau avec PyInstaller (à lancer sur la machine cible : Windows, macOS ou Linux).

    cd frontend && npm ci && npm run build && cd ..
    pip install -r desktop/requirements.txt
    python desktop/build.py              # ajoute --ffmpeg CHEMIN pour embarquer ffmpeg et ffprobe

Résultat : dist/Monteur IA/ (dossier à zipper), ou dist/Monteur IA.app sur macOS.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="dossier contenant ffmpeg et ffprobe à embarquer")
    args = ap.parse_args()
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        print("Construis d'abord l'interface : cd frontend && npm ci && npm run build", file=sys.stderr)
        return 1
    import PyInstaller.__main__ as pyi
    opts = [
        str(ROOT / "desktop" / "launcher.py"),
        "--name", "Monteur IA", "--noconfirm", "--windowed",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--paths", str(ROOT / "backend"),
        "--add-data", f"{dist}{SEP}frontend/dist",
        "--add-data", f"{ROOT / 'backend' / 'app'}{SEP}backend/app",
        "--collect-submodules", "app", "--collect-submodules", "uvicorn",
        "--collect-all", "faster_whisper", "--collect-all", "ctranslate2",
        "--collect-data", "cv2", "--hidden-import", "ai_edge_litert.interpreter",
    ]
    # DLL CUDA installées par pip (requirements-cuda.txt) : embarquées si présentes, pour que la
    # transcription Whisper garde le GPU dans l'application installée (voir backend/app/cuda.py).
    import importlib.util
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            if importlib.util.find_spec(pkg):
                opts += ["--collect-binaries", pkg]
        except (ImportError, ValueError):
            pass
    if args.ffmpeg:
        exe = ".exe" if os.name == "nt" else ""
        for tool in ("ffmpeg", "ffprobe"):
            src = Path(args.ffmpeg) / f"{tool}{exe}"
            if not src.exists():
                print(f"{src} introuvable", file=sys.stderr)
                return 1
            opts += ["--add-binary", f"{src}{SEP}bin"]
    elif not shutil.which("ffmpeg"):
        print("Attention : ffmpeg n'est ni embarqué (--ffmpeg) ni installé sur cette machine.", file=sys.stderr)
    pyi.run(opts)
    print(f"Application prête dans {ROOT / 'dist'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
