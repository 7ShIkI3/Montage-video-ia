"""Réalisateur à outils et vision des rushes, avec un faux client Claude (aucun appel réseau)."""
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from app import config, storage
from app.ai import chat, dossier, vision
from app.main import app
from app.models import Media

client = TestClient(app)


def _wait(job, seconds=120):
    for _ in range(seconds * 5):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur", "annulee"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def _block(**kw):
    return types.SimpleNamespace(**kw)


class FakeStream:
    """Imite `client.beta.messages.stream(...)` : événements de texte, puis message final."""

    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.message.content:
            if b.type == "text":
                for part in (b.text[: len(b.text) // 2], b.text[len(b.text) // 2:]):
                    yield _block(type="text", text=part)

    def get_final_message(self):
        return self.message


class FakeClaude:
    """Rejoue des réponses écrites d'avance et garde les requêtes reçues."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.requests = []
        self.beta = _block(messages=_block(stream=self._stream, create=self._create))

    def _next(self, kw):
        # Copie superficielle des messages : la boucle les complète ensuite en place.
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.scripted.pop(0)

    def _stream(self, **kw):
        return FakeStream(self._next(kw))

    def _create(self, **kw):
        return self._next(kw)


def _msg(*blocks, stop="end_turn"):
    return _block(content=list(blocks), stop_reason=stop, model="claude-test")


def _use(name, tool_id, **inp):
    return _block(type="tool_use", id=tool_id, name=name, input=inp)


@pytest.fixture
def with_claude(monkeypatch):
    def install(scripted):
        import anthropic
        fake = FakeClaude(scripted)
        monkeypatch.setattr(config, "claude_available", lambda: True)
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
        return fake
    return install


def _project_with_rush(rush, name="Agent"):
    pid = client.post("/api/projects", json={"name": name}).json()["id"]
    with rush.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    return pid


def test_sample_instants_prioritises_combo_ends_and_dedupes():
    m = Media(filename="x.mp4", path="x.mp4", duration=40.0)
    sig = {"hits": [[6.9, 1.0], [7.5, 0.9], [8.2, 0.8], [9.0, 1.0]], "video": {"red_flashes": [8.6], "cuts": [20.0]}}
    ts = vision.sample_instants(m, sig)
    assert ts == sorted(ts) and len(ts) <= vision.MAX_FRAMES
    assert any(abs(t - 9.6) < 0.01 for t in ts), "0,6 s après la fin du combo : l'issue du combat"
    assert any(abs(t - 8.7) < 0.01 for t in ts) and any(abs(t - 20.4) < 0.01 for t in ts)
    assert all(b - a > 0.6 for a, b in zip(ts, ts[1:]))
    assert all(0 <= t < 40 for t in ts)
    assert len(vision.sample_instants(m, sig, limit=5)) == 5
    assert vision.sample_instants(Media(filename="v.mp4", path="v", duration=0.0), {}) == []


def test_jpeg_and_zone(rush):
    full = vision.jpeg(str(rush), 1.0)
    part = vision.jpeg(str(rush), 1.0, "bas_gauche")
    assert full and full[:2] == b"\xff\xd8" and part and part[:2] == b"\xff\xd8" and part != full
    assert vision.jpeg(str(rush) + ".absent", 1.0) is None


def test_vision_pass_is_stored_and_enters_the_dossier(rush, with_claude):
    pid = _project_with_rush(rush, "Vision")
    est = client.get(f"/api/projects/{pid}/vision").json()
    assert est["pending"] == 1 and est["images"] > 3 and est["media"][0]["done"] is False
    answer = {"resume": "Mire de test puis barres de couleur.", "observations": [
        {"index": 0, "vu": "Mire animée", "type": "autre", "texte": ""},
        {"index": 1, "vu": "Message de kill visible", "type": "elimination", "texte": "Steve a tué Alex"},
        {"index": 999, "vu": "hors liste", "type": "autre", "texte": ""}]}
    fake = with_claude([_msg(_block(type="text", text=json.dumps(answer)))])
    j = _wait(client.post(f"/api/projects/{pid}/vision").json())
    assert j["status"] == "termine", j
    assert j["result"] == {"media": 1, "images": est["images"], "observations": 2}
    req = fake.requests[0]
    images = [b for b in req["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == est["images"] and images[0]["source"]["media_type"] == "image/jpeg"
    assert req["output_config"]["format"]["type"] == "json_schema" and req["fallbacks"] == "default"
    mid = client.get(f"/api/projects/{pid}").json()["media"][0]["id"]
    seen = storage.load_signals(pid, mid)["vision"]
    assert [o["type"] for o in seen["observations"]] == ["autre", "elimination"] and seen["images"] == est["images"]
    d = dossier.project_dossier(storage.load_project(pid))
    assert "Vu dans les images" in d and "« Steve a tué Alex »" in d and "Mire de test" in d
    assert "Steve a tué Alex" in vision.notes_as_instructions(storage.load_project(pid))
    # Déjà regardé : une seconde passe ne rappelle pas Claude.
    assert client.get(f"/api/projects/{pid}/vision").json()["pending"] == 0
    j = _wait(client.post(f"/api/projects/{pid}/vision").json())
    assert j["result"]["media"] == 0 and len(fake.requests) == 1


def test_vision_needs_a_key(rush):
    pid = _project_with_rush(rush, "Sans clé")
    assert client.post(f"/api/projects/{pid}/vision").status_code == 400


def test_agent_looks_then_edits(rush, with_claude):
    """Le réalisateur regarde des images, lit les paroles, refait le découpage, pose texte et repères, puis répond."""
    pid = _project_with_rush(rush, "Boucle")
    fake = with_claude([
        _msg(_block(type="text", text="Je regarde la fin du passage."),
             _use("voir_images", "t1", media="rush.mp4", instants=[2.0, 17.5], zone="bas_gauche"),
             _use("lire_paroles", "t2", media="RUSH.MP4"), stop="tool_use"),
        _msg(_use("definir_plans", "t3", titre="Le pic sonore", resume="On ouvre sur le pic.",
                  plans=[{"media": "rush.mp4", "debut": 16.5, "fin": 19.0, "raison": "pic sonore"},
                         {"media": "rush.mp4", "debut": 0.0, "fin": 4.0, "vitesse": 2.0}]), stop="tool_use"),
        _msg(_use("ajouter_texte", "t4", texte="LE PIC", debut=0.2, style="gros_texte"),
             _use("poser_reperes", "t5", reperes=[{"t": 0.0, "label": "Pic"}, {"t": 2.5, "label": "Début"}]),
             _use("retoucher_plan", "t6", numero=9),  # plan inexistant : erreur renvoyée au modèle
             _use("voir_images", "t7", media="inconnu.mp4", instants=[1]),
             _use("definir_plans", "t8", plans="pas une liste"), stop="tool_use"),
        _msg(_block(type="text", text="C'est fait : le montage ouvre sur le pic à 0:16.5, puis le début accéléré.")),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Ouvre sur le moment le plus fort."}).json())
    assert j["status"] == "termine", j
    assert j["preview"] is None, "l'aperçu au fil de l'eau est effacé une fois la réponse enregistrée"
    res = j["result"]
    assert res["applied"] is True and res["message"]["by"] == "claude"
    assert res["message"]["text"].startswith("C'est fait")
    assert res["message"]["actions"][0] == "A regardé 2 image(s) de rush.mp4"
    assert any("2 plan(s)" in a for a in res["message"]["actions"]) and any("repère" in a for a in res["message"]["actions"])

    # Requêtes : outils déclarés, cache, repli, dossier dans le premier message.
    first = fake.requests[0]
    names = [t["name"] for t in first["tools"]]
    assert names[:2] == ["voir_images", "lire_paroles"] and names[-1] == "web_search"
    assert all(t.get("eager_input_streaming") for t in first["tools"] if "input_schema" in t)
    assert first["cache_control"] == {"type": "ephemeral"} and first["fallbacks"] == "default"
    assert "DOSSIER DU PROJET" in first["messages"][-1]["content"] and "voir_images" in first["messages"][-1]["content"]
    # Deuxième requête : le tour de l'assistant est renvoyé tel quel, suivi de tous ses résultats en un seul message.
    second = fake.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-1]["role"] == "user"
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    images = [b for b in results[0]["content"] if b["type"] == "image"]
    assert len(images) == 2 and "zone bas_gauche" in results[0]["content"][0]["text"]
    assert isinstance(results[1]["content"], str) and "is_error" not in results[1]
    # Quatrième requête : les erreurs d'outil reviennent au modèle sans arrêter la conversation.
    errs = {r["tool_use_id"]: r for r in fake.requests[3]["messages"][-1]["content"]}
    assert errs["t6"]["is_error"] and "inexistant" in errs["t6"]["content"]
    assert errs["t7"]["is_error"] and "Rush inconnu" in errs["t7"]["content"]
    assert errs["t8"]["is_error"] and "INVALID_JSON" in errs["t8"]["content"]
    assert "is_error" not in errs["t4"] and "is_error" not in errs["t5"]

    p = client.get(f"/api/projects/{pid}").json()
    tl = p["timeline"]
    assert [(c["src_in"], c["src_out"], c["speed"]) for c in tl["clips"]] == [(16.5, 19.0, 1.0), (0.0, 4.0, 2.0)]
    assert tl["title"] == "Le pic sonore" and tl["director"] == "claude"
    assert [t["text"] for t in tl["texts"]] == ["LE PIC"] and [k["label"] for k in tl["markers"]] == ["Pic", "Début"]
    assert p["media"][0]["proxy"], "le rush jamais analysé a reçu son proxy de lecture"
    assert [m["role"] for m in p["chat"]] == ["monteur", "realisateur"]

    # Tour suivant : l'historique part en texte, avec le rappel des actions.
    fake.scripted.append(_msg(_block(type="text", text="Oui, deux plans.")))
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Combien de plans ?"}).json())
    assert j["result"]["applied"] is False and j["result"]["message"]["text"] == "Oui, deux plans."
    hist = fake.requests[-1]["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant", "user"]
    assert "Actions faites" in hist[1]["content"] and "2. " in hist[2]["content"]


def test_agent_image_budget_and_refusal(rush, with_claude, monkeypatch):
    pid = _project_with_rush(rush, "Budget")
    monkeypatch.setattr(chat, "MAX_IMAGES", 3)
    fake = with_claude([
        _msg(_use("voir_images", "a", media="rush.mp4", instants=[1, 2, 3, 4, 5]), stop="tool_use"),
        _msg(_use("voir_images", "b", media="rush.mp4", instants=[6]), stop="tool_use"),
        _msg(_block(type="text", text="Partiel."), stop="refusal"),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Regarde tout."}).json())
    assert j["status"] == "termine" and j["result"]["message"]["text"] == "Partiel."
    first = fake.requests[1]["messages"][-1]["content"][0]
    assert len([b for b in first["content"] if b["type"] == "image"]) == 3
    second = fake.requests[2]["messages"][-1]["content"][0]
    assert second["is_error"] and "Limite de 3 images" in second["content"]


def test_web_search_tool_follows_the_model(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_MODEL", "claude-opus-5-5")
    assert chat.web_search_tool()["type"] == "web_search_20260209"
    monkeypatch.setattr(config, "CLAUDE_MODEL", "claude-haiku-5-5")
    assert chat.web_search_tool(5) == {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


def test_agent_color_and_mix_tools(rush, with_claude):
    """Le réalisateur corrige les couleurs d'un plan et règle le mixage ; le dossier le reflète."""
    pid = _project_with_rush(rush, "Couleur et mixage")
    with_claude([
        _msg(_use("definir_plans", "p", plans=[{"media": "rush.mp4", "debut": 0, "fin": 4}]), stop="tool_use"),
        _msg(_use("retoucher_plan", "c", numero=1, couleur={"exposition": 0.3, "saturation": 5}),
             _use("regler_mixage", "m", reglages=[{"groupe": "musique", "volume": 0.5}, {"groupe": "plans", "coupe": True}]),
             _use("regler_mixage", "x", reglages=[{"groupe": "batterie", "volume": 1}]), stop="tool_use"),
        _msg(_block(type="text", text="Plan éclairci, musique à 50 %, son du jeu coupé.")),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Trop sombre, et coupe le son du jeu."}).json())
    assert j["status"] == "termine" and j["result"]["applied"] is True
    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["clips"][0]["color"] == {"exposure": 0.3, "contrast": 0.0, "saturation": 1.0, "temperature": 0.0}
    assert tl["mix"]["music"] == {"volume": 0.5, "mute": False} and tl["mix"]["rushes"]["mute"] is True
    d = dossier.project_dossier(storage.load_project(pid))
    assert "couleurs corrigées" in d and "Mixage : plans coupé, musique 50 %" in d
    assert any("mixage" in a for a in j["result"]["message"]["actions"])
