"""Chat réalisateur, dossier des rushes, écrans rouges, duplication et ouverture du dossier."""
import os
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app import storage
from app.ai import dossier
from app.analysis import video as video_an
from app.main import app

client = TestClient(app)


def _wait(job, seconds=120):
    for _ in range(seconds * 5):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur", "annulee"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_red_flashes_detects_sudden_red_tint():
    """Une image qui rougit d'un coup est repérée une fois (pas à chaque image rouge qui suit)."""
    n, fps = 40, 5
    frames = np.full((n, 18, 32, 3), 90, np.uint8)  # gris
    frames[20:23, ..., 0] = 160  # 3 images teintées rouge à t = 4 s
    frames[35, ..., 1] = 160  # un flash vert : ignoré
    out = video_an.red_flashes(frames, fps)
    assert out == [4.0]
    assert video_an.red_flashes(frames[:2], fps) == []


def _project_with_rush(rush, name="Chat"):
    pid = client.post("/api/projects", json={"name": name}).json()["id"]
    with rush.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    return pid


def test_dossier_and_chat_flow(rush, monkeypatch):
    pid = _project_with_rush(rush)
    # Avant analyse : le dossier dit ce qui manque, sans planter.
    d = dossier.project_dossier(storage.load_project(pid))
    assert "rush.mp4" in d and "pas encore analysé" in d and "Montage actuel : aucun" in d
    assert "Limites de la mesure" in d

    # Sans clé Claude et sans montage : le chat explique, n'applique rien, garde l'échange.
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Quels combos ai-je gagnés ?"}).json())
    assert j["status"] == "termine", j
    assert j["result"]["applied"] is False and "Monter automatiquement" in j["result"]["message"]["text"]
    chat = client.get(f"/api/projects/{pid}").json()["chat"]
    assert [m["role"] for m in chat] == ["monteur", "realisateur"] and chat[1]["by"] == "local"

    j = _wait(client.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())
    assert j["status"] == "termine", j
    p = client.get(f"/api/projects/{pid}").json()
    assert p["timeline"]["format"] == "16:9"
    d = client.get(f"/api/projects/{pid}/dossier").text
    assert "Coups détectés au son" in d or "Paroles" in d
    assert "Montage actuel :" in d and "1. 0:00.0 ← rush.mp4" in d
    sig = storage.load_signals(pid, p["media"][0]["id"])
    assert isinstance(sig["video"].get("red_flashes"), list)  # mesuré au passage

    # Avec un montage : le message devient une consigne appliquée par le réalisateur local.
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Passe en vertical"}).json())
    assert j["status"] == "termine", j
    assert j["result"]["applied"] is True
    p = client.get(f"/api/projects/{pid}").json()
    assert p["timeline"]["format"] == "9:16"
    assert len(p["chat"]) == 4 and p["chat"][-1]["consigne"] == "Passe en vertical" and p["chat"][-1]["applied"]
    assert client.post(f"/api/projects/{pid}/chat", json={"text": "  "}).status_code == 400
    assert client.delete(f"/api/projects/{pid}/chat").json()["ok"]
    assert client.get(f"/api/projects/{pid}").json()["chat"] == []

    # Duplication : nouveau projet, mêmes médias, chemins déplacés et fichiers présents.
    cp = client.post(f"/api/projects/{pid}/duplicate").json()
    assert cp["id"] != pid and cp["name"] == "Copie de Chat" and cp["exports"] == []
    assert len(cp["media"]) == 1 and cp["timeline"]["format"] == "9:16"
    m = cp["media"][0]
    assert Path(m["path"]).exists() and str(storage.project_dir(cp["id"])) in m["path"]
    assert m["proxy"] and Path(m["proxy"]).exists() and pid not in m["proxy"]
    assert [x["id"] for x in client.get("/api/projects").json()].count(cp["id"]) == 1

    # Ouverture du dossier : os.startfile appelé avec le dossier du projet (jamais exécuté ici).
    opened = []
    monkeypatch.setattr(os, "startfile", lambda path: opened.append(path), raising=False)
    r = client.post(f"/api/projects/{pid}/open-folder")
    assert r.status_code == 200 and opened == [str(storage.project_dir(pid))]
    assert r.json()["path"] == str(storage.project_dir(pid))
    r = client.post(f"/api/projects/{pid}/open-folder", json={"sub": "exports"})
    assert r.status_code == 200 and opened[-1] == str(storage.exports_dir(pid))
    # Un fichier précis : l'explorateur le sélectionne (commande capturée, jamais lancée) ; un fichier absent → 404.
    import app.main as main_mod
    launched = []
    monkeypatch.setattr(main_mod.subprocess, "Popen", lambda cmd, *a, **k: launched.append(cmd))
    assert client.post(f"/api/projects/{pid}/open-folder", json={"sub": "media", "file": "nope.mp4"}).status_code == 404
    r = client.post(f"/api/projects/{pid}/open-folder", json={"sub": "media", "file": "rush.mp4"})
    assert r.status_code == 200 and r.json()["path"].endswith("rush.mp4")
    if os.name == "nt":
        assert launched and launched[-1][:2] == ["explorer", "/select,"] and launched[-1][2].endswith("rush.mp4")


def test_phrases_from_words():
    from app.models import Transcript, Word
    tr = Transcript(media_id="m", words=[Word(start=0, end=0.3, text="Salut"), Word(start=0.4, end=0.8, text="à tous"),
                                         Word(start=3.0, end=3.4, text="Deuxième"), Word(start=3.5, end=3.9, text="phrase")])
    assert dossier._phrases(tr) == [(0.0, "Salut à tous"), (3.0, "Deuxième phrase")]
    assert dossier.mmss(83.46) == "1:23.5"
