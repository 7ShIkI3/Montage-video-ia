"""Monteur IA en application de bureau : lance le serveur en local et l'ouvre dans sa propre fenêtre.

    python desktop/launcher.py            # fenêtre native (pywebview) ou, à défaut, le navigateur
    python desktop/launcher.py --browser  # toujours dans le navigateur

Les projets sont rangés dans le dossier de l'utilisateur (Documents/Monteur IA, ou MONTEUR_DATA_DIR).
Dans l'application compilée (PyInstaller), l'interface et FFmpeg sont pris dans le paquet lui-même.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Monteur IA"


def bundle_root() -> Path:
    """Dossier des fichiers de l'appli : le paquet PyInstaller, ou la racine du dépôt."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / APP_NAME


def setup_env() -> None:
    root = bundle_root()
    os.environ.setdefault("MONTEUR_DATA_DIR", str(default_data_dir()))
    dist = root / "frontend" / "dist"
    if dist.is_dir():
        os.environ.setdefault("MONTEUR_FRONTEND_DIST", str(dist))
    exe = ".exe" if os.name == "nt" else ""
    for tool in ("ffmpeg", "ffprobe"):
        p = root / "bin" / f"{tool}{exe}"
        if p.exists():
            os.environ.setdefault(f"MONTEUR_{tool.upper()}", str(p))
    backend = root / "backend"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    Path(os.environ["MONTEUR_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    from app import cuda
    cuda.add_dll_dirs()  # DLL CUDA de pip, avant tout import de ctranslate2


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_is_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_port(wanted: int) -> int:
    """Port demandé (--port), sinon celui des Réglages (settings.json), sinon 8000 ; un port libre si occupé."""
    if not wanted:
        from app import settings
        wanted = settings.load().port
    return wanted if port_is_free(wanted) else free_port()


def serve(port: int):
    import uvicorn

    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def wait_ready(url: str, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--browser", action="store_true", help="ouvrir dans le navigateur plutôt qu'en fenêtre")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    setup_env()
    port = choose_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server, server_thread = serve(port)
    if not wait_ready(url):
        print("Le serveur ne répond pas.", file=sys.stderr)
        return 1
    print(f"{APP_NAME} : {url}  (projets dans {os.environ['MONTEUR_DATA_DIR']})")
    window = None
    if not args.browser:
        try:
            import webview  # pywebview
            window = webview
        except ImportError:
            pass
    if window:
        window.create_window(APP_NAME, url, width=1440, height=900, min_size=(1100, 700))
        window.start()  # rend la main quand la fenêtre est fermée
    else:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
    server_thread.join(timeout=5)  # laisse le serveur annuler les tâches en cours (ffmpeg compris) avant de quitter
    return 0


if __name__ == "__main__":
    sys.exit(main())
