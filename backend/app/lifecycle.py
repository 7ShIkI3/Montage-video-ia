"""Fermeture de l'application demandée depuis le serveur (mise à jour).

Le lanceur de bureau (desktop/launcher.py) possède la fenêtre et le serveur ; le backend, lui, ne les voit pas.
Le lanceur enregistre ici un rappel de sortie, et une route (ex. l'installation d'une mise à jour) peut ensuite
demander la fermeture sans rien connaître de la fenêtre. Sans lanceur (tests, `uvicorn` seul), aucun rappel
n'est enregistré et la demande est simplement refusée : le serveur ne s'arrête pas tout seul.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

_exit_callback: Callable[[], None] | None = None


def set_exit_callback(fn: Callable[[], None] | None) -> None:
    """Enregistre (ou retire, avec None) la fonction qui ferme la fenêtre et arrête le serveur."""
    global _exit_callback
    _exit_callback = fn


def can_exit() -> bool:
    return _exit_callback is not None


def _call() -> None:
    fn = _exit_callback
    if fn is None:
        return
    try:
        fn()
    except Exception:  # la fermeture ne doit jamais lever dans le fil du minuteur
        log.exception("Fermeture de l'application : le rappel a échoué")


def request_exit(delay: float = 0.0) -> bool:
    """Demande la fermeture de l'application, après `delay` secondes (le temps de répondre à la requête en cours).

    Renvoie False si aucun lanceur n'a enregistré de rappel."""
    if _exit_callback is None:
        return False
    if delay <= 0:
        _call()
    else:
        timer = threading.Timer(delay, _call)
        timer.daemon = True
        timer.start()
    return True
