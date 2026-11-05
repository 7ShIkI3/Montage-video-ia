"""Lanceur de bureau : sans console, les sous-processus ne doivent pas ouvrir de fenêtre."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "desktop" / "launcher.py"


def _launcher():
    spec = importlib.util.spec_from_file_location("launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HAS_CONSOLE = "import ctypes, sys; sys.exit(0 if ctypes.windll.kernel32.GetConsoleWindow() == 0 else 7)"


@pytest.mark.skipif(os.name != "nt", reason="fenêtres de console Windows")
def test_subprocesses_get_no_console_window(monkeypatch):
    launcher = _launcher()
    # Sans le correctif, un enfant console hérite (ou crée) une fenêtre de console.
    orig_init = subprocess.Popen.__init__
    orig_flag = getattr(subprocess.Popen, "_monteur_sans_fenetre", False)
    try:
        launcher.hide_console_windows()
        # subprocess.run passe par Popen : le drapeau s'applique à tous les appels du backend.
        p = subprocess.run([sys.executable, "-c", HAS_CONSOLE], capture_output=True, timeout=30)
        assert p.returncode == 0, "l'enfant a encore une fenêtre de console"
        # Un drapeau déjà donné par l'appelant est conservé.
        p = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=30,
                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        assert p.stdout.strip() == "ok"
        launcher.hide_console_windows()  # idempotent
        assert subprocess.Popen.__init__.__name__ == "init"
    finally:
        subprocess.Popen.__init__ = orig_init
        if orig_flag:
            subprocess.Popen._monteur_sans_fenetre = True
        elif hasattr(subprocess.Popen, "_monteur_sans_fenetre"):
            del subprocess.Popen._monteur_sans_fenetre


def test_redirect_std_writes_a_dated_header(tmp_path):
    launcher = _launcher()
    out, err = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        launcher.redirect_std(tmp_path / "monteur.log")
        print("bonjour")
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr = out, err
    text = (tmp_path / "monteur.log").read_text(encoding="utf-8")
    assert "démarrage" in text and "bonjour" in text


def test_redirect_std_rotates_a_big_log(tmp_path):
    launcher = _launcher()
    log = tmp_path / "monteur.log"
    log.write_bytes(b"x" * (launcher.LOG_MAX_BYTES + 1))
    (tmp_path / "monteur.log.1").write_text("ancien", encoding="utf-8")  # écrasé
    out, err = sys.stdout, sys.stderr
    try:
        sys.stdout = sys.stderr = None
        launcher.redirect_std(log)
        sys.stdout.flush()
    finally:
        f, sys.stdout, sys.stderr = sys.stdout, out, err
        f.close()
    assert (tmp_path / "monteur.log.1").stat().st_size == launcher.LOG_MAX_BYTES + 1
    assert "démarrage" in log.read_text(encoding="utf-8") and log.stat().st_size < 200
    # Un petit journal n'est pas touché : on écrit à la suite.
    log.write_text("court\n", encoding="utf-8")
    try:
        sys.stdout = sys.stderr = None
        launcher.redirect_std(log)
        sys.stdout.flush()
    finally:
        f, sys.stdout, sys.stderr = sys.stdout, out, err
        f.close()
    assert log.read_text(encoding="utf-8").startswith("court\n") and (tmp_path / "monteur.log.1").stat().st_size > 1000
