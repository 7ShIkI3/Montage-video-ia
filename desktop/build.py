"""Construit l'application de bureau avec PyInstaller (à lancer sur la machine cible : Windows, macOS ou Linux).

    cd frontend && npm ci && npm run build && cd ..
    pip install -r desktop/requirements.txt
    python desktop/build.py              # ajoute --ffmpeg CHEMIN pour embarquer ffmpeg et ffprobe
    python desktop/build.py --installer  # Windows : construit aussi l'installateur (Inno Setup 6 requis)

Résultat : dist/Monteur IA/ (dossier de l'application), ou dist/Monteur IA.app sur macOS ; avec --installer,
dist/installer/MonteurIA-Setup-<version>.exe, le seul fichier à donner à quelqu'un pour installer l'application.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
VERSION_FILE = ROOT / "backend" / "app" / "version.py"  # la seule source du numéro de version
ICON = ROOT / "desktop" / "monteur.ico"  # redessinée par desktop/make_icon.py


def read_version(path: Path = VERSION_FILE) -> str:
    """Numéro de version lu dans backend/app/version.py par expression régulière (sans importer l'application)."""
    m = re.search(r"""^__version__\s*=\s*["'](\d+\.\d+\.\d+)["']\s*$""", path.read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        raise SystemExit(f"{path} : __version__ = \"X.Y.Z\" introuvable")
    return m.group(1)


VERSION = read_version()  # version affichée par l'installateur, dans « Applications installées » et sur l'exe


def version_resource(version: str) -> str:
    """Contenu d'un fichier « version » PyInstaller : l'onglet Détails des propriétés de l'exe affiche la version."""
    nums = tuple(int(n) for n in version.split(".")) + (0,)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Monteur IA'),
      StringStruct('FileDescription', 'Monteur IA'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Monteur IA'),
      StringStruct('OriginalFilename', 'Monteur IA.exe'),
      StringStruct('ProductName', 'Monteur IA'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def find_iscc() -> Path | None:
    """Le compilateur d'Inno Setup : dans le PATH, ou à l'un de ses emplacements d'installation habituels."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        for sub in ("Programs/Inno Setup 6", "Inno Setup 6"):
            exe = Path(base) / sub / "ISCC.exe" if base else None
            if exe and exe.exists():
                return exe
    return None


def build_installer() -> int:
    """Emballe dist/Monteur IA dans un installateur (un seul .exe, sans droits d'administrateur)."""
    if os.name != "nt":
        print("L'installateur n'existe que pour Windows.", file=sys.stderr)
        return 1
    app = ROOT / "dist" / "Monteur IA" / "Monteur IA.exe"
    if not app.exists():
        print(f"{app} introuvable : construis d'abord l'application (python desktop/build.py).", file=sys.stderr)
        return 1
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 est introuvable (ISCC.exe). Installe-le : winget install --id JRSoftware.InnoSetup -e",
              file=sys.stderr)
        return 1
    print("Construction de l'installateur (plusieurs minutes : près de 3 Go à compresser)…")
    done = subprocess.run([str(iscc), f"/DAppVersion={VERSION}", "/Qp", str(ROOT / "desktop" / "installer.iss")])
    if done.returncode:
        return done.returncode
    out = ROOT / "dist" / "installer" / f"MonteurIA-Setup-{VERSION}.exe"
    print(f"Installateur prêt : {out} ({out.stat().st_size / 1e6:.0f} Mo)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="dossier contenant ffmpeg et ffprobe à embarquer")
    ap.add_argument("--installer", action="store_true", help="Windows : construit aussi l'installateur (Inno Setup 6)")
    ap.add_argument("--installer-only", action="store_true", help="n'emballe que dist/Monteur IA, sans le reconstruire")
    args = ap.parse_args()
    if args.installer_only:
        return build_installer()
    if args.installer and not find_iscc():
        # On le dit avant les longues minutes de PyInstaller, pas après.
        print("Inno Setup 6 est introuvable (ISCC.exe). Installe-le : winget install --id JRSoftware.InnoSetup -e",
              file=sys.stderr)
        return 1
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        print("Construis d'abord l'interface : cd frontend && npm ci && npm run build", file=sys.stderr)
        return 1
    # L'interface embarquée est celle de frontend/dist, telle quelle : si les sources ont changé depuis sa
    # construction, l'exe partirait avec une vieille interface sans que rien ne le signale.
    built = (dist / "index.html").stat().st_mtime
    newer = [f for f in (ROOT / "frontend" / "src").rglob("*") if f.is_file() and f.stat().st_mtime > built]
    if newer:
        print(f"L'interface construite est plus ancienne que ses sources ({newer[0].name}, {len(newer)} fichier(s)) : "
              "cd frontend && npm run build", file=sys.stderr)
        return 1
    demo_clip = ROOT / "build" / "demo_pvp.mp4"
    if not demo_clip.exists():
        # Clip « Essayer sans vidéo » : rendu une fois ici (quelques minutes) plutôt qu'à chaque essai chez l'utilisateur.
        print("Rendu du clip de démo (une fois)…")
        sys.path.insert(0, str(ROOT / "backend"))
        from app.media import demo
        demo.render(demo_clip)
    import PyInstaller.__main__ as pyi
    version_file = Path(tempfile.mkdtemp(prefix="monteur-version-")) / "version.txt"
    version_file.write_text(version_resource(VERSION), encoding="utf-8")
    opts = [
        *(["--version-file", str(version_file)] if os.name == "nt" else []),
        "--add-data", f"{demo_clip}{SEP}assets",
        str(ROOT / "desktop" / "launcher.py"),
        "--name", "Monteur IA", "--noconfirm", "--windowed",
        *(["--icon", str(ICON)] if ICON.exists() and os.name == "nt" else []),
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--paths", str(ROOT / "backend"),
        "--add-data", f"{dist}{SEP}frontend/dist",
        "--add-data", f"{ROOT / 'backend' / 'app'}{SEP}backend/app",
        "--collect-submodules", "app", "--collect-submodules", "uvicorn",
        "--collect-all", "faster_whisper", "--collect-all", "ctranslate2",
        "--collect-data", "cv2", "--hidden-import", "ai_edge_litert.interpreter",
        # Téléchargements (yt-dlp charge ses extracteurs à la demande) et voix Piper (modèle ONNX + données espeak).
        "--collect-all", "yt_dlp", "--collect-all", "piper", "--collect-binaries", "onnxruntime", "--collect-data", "onnxruntime",
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
    return build_installer() if args.installer else 0


if __name__ == "__main__":
    sys.exit(main())
