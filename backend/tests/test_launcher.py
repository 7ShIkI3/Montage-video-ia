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


# --- Fermeture demandée par le backend (mise à jour) --------------------------------------------

class FakeTimer:
    """Remplace threading.Timer : note le délai et ne tire jamais (le vrai appellerait os._exit en plein test)."""
    created: list = []

    def __init__(self, delay, fn, args=(), kwargs=None):
        self.delay, self.fn, self.args, self.daemon = delay, fn, args, False
        FakeTimer.created.append(self)

    def start(self):
        pass


def test_exit_callback_destroys_windows_and_wakes_the_loop(monkeypatch):
    import threading
    import types
    launcher = _launcher()
    FakeTimer.created = []
    monkeypatch.setattr(launcher.threading, "Timer", FakeTimer)
    destroyed = []

    class Window:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        def destroy(self):
            if self.fail:
                raise RuntimeError("déjà fermée")
            destroyed.append(self.name)

    quit_event = threading.Event()
    webview = types.SimpleNamespace(windows=[Window("a", fail=True), Window("b")])
    launcher.make_exit(quit_event, webview)()
    assert quit_event.is_set() and destroyed == ["b"]  # une fenêtre qui refuse ne bloque pas les autres
    # Garde-fou : si le processus traîne, il est coupé après 20 s.
    (t,) = FakeTimer.created
    assert t.delay == 20.0 and t.fn is os._exit and t.args == (0,) and t.daemon is True


def test_exit_callback_without_window_and_without_safety_net(monkeypatch):
    import threading
    launcher = _launcher()
    FakeTimer.created = []
    monkeypatch.setattr(launcher.threading, "Timer", FakeTimer)
    quit_event = threading.Event()
    launcher.make_exit(quit_event, None, hard_exit_after=None)()  # mode navigateur
    assert quit_event.is_set() and FakeTimer.created == []


def test_main_registers_the_exit_callback_and_stops_the_server(monkeypatch):
    """Mode navigateur : une demande de fermeture du backend sort de la boucle et arrête le serveur."""
    import threading
    import types
    from app import lifecycle
    launcher = _launcher()
    FakeTimer.created = []
    monkeypatch.setattr(launcher.threading, "Timer", FakeTimer)
    server = types.SimpleNamespace(should_exit=False)
    joined = []
    thread = types.SimpleNamespace(join=lambda timeout=None: joined.append(timeout))
    monkeypatch.setattr(launcher, "setup_env", lambda: None)
    monkeypatch.setattr(launcher, "choose_port", lambda wanted: 8765)
    monkeypatch.setattr(launcher, "serve", lambda port: (server, thread))
    monkeypatch.setattr(launcher, "wait_ready", lambda url: True)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)
    monkeypatch.setenv("MONTEUR_DATA_DIR", os.environ["MONTEUR_DATA_DIR"])
    result = []
    t = threading.Thread(target=lambda: result.append(launcher.main(["--browser"])), daemon=True)
    t.start()
    import time
    end = time.time() + 10
    while not lifecycle.can_exit() and time.time() < end:
        time.sleep(0.02)
    assert lifecycle.can_exit(), "le lanceur n'a pas enregistré de rappel de sortie"
    assert lifecycle.request_exit() is True
    t.join(10)
    assert result == [0] and server.should_exit is True and joined == [5]
    assert lifecycle.can_exit() is False  # rappel retiré à la sortie
