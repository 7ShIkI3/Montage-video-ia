"""Réalisateur à outils et vision des rushes, avec un faux client Claude (aucun appel réseau)."""
import json
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, storage
from app.ai import chat, dossier, vision
from app.main import app
from app.media.ffmpeg import FFMPEG
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


# ------------------------------------------------------------------ edit PvP depuis le chat

@pytest.fixture(scope="module")
def gameplay(tmp_path_factory) -> Path:
    """Rush de 12 s avec deux combos dans le son : 2,0 → 4,2 s (6 coups) et 8,0 → 9,5 s (5 coups)."""
    import subprocess
    import wave

    import numpy as np
    from test_pvp import COMBO_A, COMBO_B, SR, _clicks
    d = tmp_path_factory.mktemp("gameplay")
    pcm = (np.clip(_clicks(COMBO_A + COMBO_B), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(d / "hits.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    out = d / "gameplay.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=12",
                    "-i", str(d / "hits.wav"), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
                   check=True)
    return out


def _gameplay_project(gameplay, name):
    pid = client.post("/api/projects", json={"name": name}).json()["id"]
    with gameplay.open("rb") as f:
        assert client.post(f"/api/projects/{pid}/media", files=[("files", ("gameplay.mp4", f, "video/mp4"))]).status_code == 200
    return pid


def test_pvp_tool_is_declared_and_the_prompt_sends_pvp_requests_to_it():
    tool = next(t for t in chat.TOOLS if t["name"] == "monter_edit_pvp")
    assert "Edit PvP" in tool["description"] and "musique" in tool["description"] and tool["input_schema"]["required"] == []
    assert "monter_edit_pvp" in chat.SYSTEM and "highlights" in chat.SYSTEM and "enchaîner" in chat.SYSTEM
    assert "monter_edit_pvp" in chat._INPUTS


def test_agent_builds_the_pvp_edit_with_music_and_refuses_what_the_player_takes(gameplay, with_claude, monkeypatch):
    """Le réalisateur choisit deux combos plus un instant sans combo : l'edit est monté sur le tempo d'une musique posée
    sur la timeline, le combo subi reste refusé (et signalé), l'instant sans combo est signalé, le jury n'est pas payé."""
    from app import pipeline
    from app.ai import jury
    pid = _gameplay_project(gameplay, "Chat PvP")

    def duel(project_id, media, cands, ctx=None, **k):
        res = lambda lab, ok: {"label": lab, "confirme": ok, "cout": 6.0 if lab == "subit" else None, "part_recue": None,
                               "vie": None, "camera": None, "fenetre": None, "raison": "vie 9,5 → 3,0 cœurs"}
        return {(mid, jury.key(c)): res("subit" if c["start"] > 6 else "donne", c["start"] < 6) for mid, c in cands}

    def no_jury(*a, **k):
        raise AssertionError("le jury payant ne doit pas être appelé quand le réalisateur a choisi ses rafales")
    monkeypatch.setattr(pipeline.duel_an, "analyse", duel)
    monkeypatch.setattr(pipeline.jury_mod, "judge", no_jury)
    fake = with_claude([
        _msg(_use("monter_edit_pvp", "pvp", rafales=[{"media": "gameplay.mp4", "debut": 1.8, "fin": 4.6},
                                                        {"media": "gameplay.mp4", "debut": 7.8, "fin": 9.8},
                                                        {"media": "gameplay.mp4", "debut": 11.0, "fin": 11.8}]), stop="tool_use"),
        _msg(_block(type="text", text="Edit monté sur le combo de 0:02 ; celui de 0:08 est subi, je l'ai écarté.")),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Fais-moi un edit PvP de mes combos."}).json())
    assert j["status"] == "termine", j
    assert j["result"]["applied"] is True
    assert any("edit PvP" in a and "musique beat_pvp_" in a for a in j["result"]["message"]["actions"])
    res = fake.requests[1]["messages"][-1]["content"][0]
    assert "is_error" not in res
    text = res["content"]
    assert "Edit PvP monté." in text and "Musique : beat_pvp_" in text and "rythme généré" in text
    # Le combo de 0:08 finit son combat : la mesure le donne pour subi, et sans verdict du jury (non appelé ici) le kill
    # n'est pas confirmé. Il est écarté, et le réalisateur le sait.
    assert "écartées" in text and "gameplay.mp4 0:08.0" in text and "tu subissais probablement cette fin de combat" in text
    assert "Aucun combo détecté pour : gameplay.mp4 0:11.0→0:11.8" in text
    assert "Musique : beat_pvp_" in text.split("Montage actuel")[1], "le dossier du montage donne la musique au réalisateur"

    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["director"] == "pvp" and tl["clips"]
    assert tl["music"]["track"] and Path(tl["music"]["track"]).exists() and tl["music"]["volume"] > 0
    assert all(c["src_out"] <= 6.0 for c in tl["clips"]), "aucun plan du combo subi (8,0 → 9,5 s)"
    assert tl["mix"]["music"] == {"volume": 1.0, "mute": False}


def test_agent_pvp_tool_lets_the_engine_choose_and_honours_a_chosen_track(gameplay, with_claude, monkeypatch, tmp_path):
    """Sans rafales : le moteur choisit seul (avec le jury, comme le bouton). Un morceau demandé par son nom est posé ;
    un nom inconnu renvoie la liste des morceaux sans toucher au montage."""
    import shutil
    from app import config, pipeline
    music_dir = tmp_path / "bib"
    music_dir.mkdir()
    shutil.copyfile(pipeline.pvp_mod.beat_track(100), music_dir / "mon_morceau.wav")
    monkeypatch.setattr(config, "MUSIC_DIR", music_dir)
    pid = _gameplay_project(gameplay, "Chat PvP libre")
    judged = []

    def jury(project_id, rows, ctx=None):
        judged.extend(c["start"] for _, c in rows)
        return {}
    monkeypatch.setattr(pipeline.jury_mod, "judge", jury)
    fake = with_claude([
        _msg(_use("monter_edit_pvp", "x", musique="inconnu.mp3"), stop="tool_use"),
        _msg(_use("monter_edit_pvp", "y", musique="mon_morceau", duree=12), stop="tool_use"),
        _msg(_block(type="text", text="Fait.")),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Edit PvP avec ma musique."}).json())
    assert j["status"] == "termine", j
    bad = fake.requests[1]["messages"][-1]["content"][0]
    assert bad["is_error"] and "Musique inconnue" in bad["content"] and "mon_morceau.wav" in bad["content"]
    ok = fake.requests[2]["messages"][-1]["content"][0]
    assert "is_error" not in ok and "Musique : mon_morceau.wav" in ok["content"] and "rythme généré" not in ok["content"]
    assert judged, "sans rafales choisies, le jury regarde les rafales comme pour le bouton"
    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["director"] == "pvp" and Path(tl["music"]["track"]).name == "mon_morceau.wav"



# ---------------------------------------------------------------- un edit PvP ne se défait pas par un autre chemin

class _Ctx:
    cancel_event = None  # lu par l'analyse du média (annulation de ffmpeg) : None = jamais annulé

    def step(self, *a, **k):
        pass

    def check(self):
        pass

    def preview(self, *a, **k):
        pass

    def sub(self, *a, **k):
        return lambda p: None


def _duel_says(label, taken_ranges=None):
    """Faux `duel.analyse` : la même étiquette pour toutes les rafales (et la liste des plages vues, si demandée)."""
    from app.ai import jury

    def duel(project_id, media, cands, ctx=None, **k):
        if taken_ranges is not None:
            taken_ranges.extend((c["start"], c["end"]) for _, c in cands)
        return {(mid, jury.key(c)): {"label": label, "confirme": False, "cout": 6.0 if label == "subit" else None,
                                     "part_recue": None, "vie": None, "camera": None, "fenetre": None,
                                     "raison": "vie 9,5 → 3,0 cœurs"} for mid, c in cands}
    return duel


def test_pvp_fallback_never_goes_back_to_the_combos_the_player_takes(gameplay, monkeypatch):
    """Tout est écarté parce que le joueur subit : le montage classique de repli ne reprend pas ces plans."""
    from app import pipeline
    pid = _gameplay_project(gameplay, "Repli sans subis")
    taken: list = []
    monkeypatch.setattr(pipeline.duel_an, "analyse", _duel_says("subit", taken))
    j = _wait(client.post(f"/api/projects/{pid}/auto-edit", json={"style": "pvp", "music": False}).json())
    assert j["status"] == "termine", j
    p = client.get(f"/api/projects/{pid}").json()
    assert taken, "des rafales ont été vues"
    assert not any(c["src_in"] < b - 0.3 and c["src_out"] > a + 0.3 for c in p["timeline"]["clips"] for a, b in taken), \
        "aucun plan du repli ne recouvre un combo subi"
    assert any("tu subis un combo" in w or "Pas de montage classique" in w for w in p["warnings"]), p["warnings"]


def test_a_settings_request_on_a_pvp_edit_stays_a_pvp_edit(gameplay, monkeypatch):
    """« Ajoute de la musique », « fais 20 secondes » ne rebâtissent pas un montage classique sur tout le rush : l'edit
    est refait par le moteur PvP (le combo subi reste dehors) et les réglages du projet ne deviennent pas « pvp »."""
    from app import pipeline
    from app.ai import jury
    pid = _gameplay_project(gameplay, "Consigne sur edit PvP")
    base = _duel_says("donne")

    def duel(project_id, media, cands, ctx=None, **k):
        out = base(project_id, media, cands, ctx)
        for mid, c in cands:
            if c["start"] > 6:
                out[(mid, jury.key(c))] = {**out[(mid, jury.key(c))], "label": "subit", "cout": 6.0}
        return out
    monkeypatch.setattr(pipeline.duel_an, "analyse", duel)
    first = pipeline.pvp_edit_chat(pid, _Ctx())
    assert first["applied"] and first["timeline"].clips
    settings = client.get(f"/api/projects/{pid}").json()["settings"]
    assert settings["style"] != "pvp", "un edit demandé en passant ne change pas le style du projet"
    before = {k: settings[k] for k in ("style", "captions")}

    res = pipeline.ai_command(pid, "ajoute de la musique et fais 20 secondes", _Ctx())
    assert res["director"] == "pvp" and "Edit PvP refait" in res["message"]
    p = client.get(f"/api/projects/{pid}").json()
    tl = p["timeline"]
    assert tl["director"] == "pvp" and tl["clips"] and tl["music"]["track"]
    assert all(c["src_out"] <= 6.0 for c in tl["clips"]), "le combo subi (8,0 → 9,5 s) n'est jamais revenu"
    assert p["settings"]["target_duration"] == 20
    assert {k: p["settings"][k] for k in before} == before, "style et sous-titres du projet inchangés"
    assert p["settings"]["music"] is True, "seule la musique demandée est enregistrée"
    # « Sans musique » retire la musique de l'edit, sans le défaire.
    pipeline.ai_command(pid, "sans musique", _Ctx())
    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["director"] == "pvp" and tl["clips"] and not tl["music"]["track"]


def test_a_command_rewrite_never_brings_back_a_taken_combo():
    from app import pipeline
    from app.models import Clip, Timeline
    old = Timeline(director="pvp", rejected=[Clip(media_id="m", src_in=8.0, src_out=9.5, reason="Écarté : tu subissais ce combo (x)."),
                                             Clip(media_id="m", src_in=20.0, src_out=22.0, reason="Écarté par le jury IA : échange.")])
    new = Timeline(clips=[Clip(media_id="m", src_in=1.0, src_out=3.0), Clip(media_id="m", src_in=7.5, src_out=10.0),
                          Clip(media_id="m", src_in=20.0, src_out=22.0), Clip(media_id="autre", src_in=8.0, src_out=9.0)])
    tl, retires = pipeline._sans_combos_subis(new, old)
    assert retires == 1 and [(c.media_id, c.src_in) for c in tl.clips] == [("m", 1.0), ("m", 20.0), ("autre", 8.0)]
    assert pipeline._sans_combos_subis(new, Timeline())[1] == 0


def test_pvp_tool_refuses_when_the_measure_fails_and_warns_when_it_cannot_judge(gameplay, with_claude, monkeypatch):
    from app import pipeline

    def plante(*a, **k):
        raise RuntimeError("ffmpeg indisponible")
    monkeypatch.setattr(pipeline.duel_an, "analyse", plante)
    pid = _gameplay_project(gameplay, "Mesure en panne")
    fake = with_claude([
        _msg(_use("monter_edit_pvp", "pvp", rafales=[{"media": "gameplay.mp4", "debut": 1.8, "fin": 4.6}]), stop="tool_use"),
        _msg(_block(type="text", text="Je n'ai rien monté.")),
    ])
    j = _wait(client.post(f"/api/projects/{pid}/chat", json={"text": "Edit PvP de ce combo."}).json())
    assert j["status"] == "termine", j
    res = fake.requests[1]["messages"][-1]["content"][0]
    assert res["is_error"] and "mesure de la vie et de la caméra a échoué" in res["content"] and "rien monté" in res["content"]
    assert not client.get(f"/api/projects/{pid}").json()["timeline"]["clips"], "rien n'a été remplacé"
    # Sans rafales choisies (le moteur choisit, comme le bouton) : monté quand même, mais l'avertissement remonte.
    out = pipeline.pvp_edit_chat(pid, _Ctx())
    assert out["applied"] and any("Mesure locale de la vie et de la caméra indisponible" in w for w in out["avertissements"])


def test_dossier_hides_taken_combos_and_ignores_an_old_measure_format():
    import re
    from app.ai import jury, pvp
    pid = client.post("/api/projects", json={"name": "Dossier duel"}).json()["id"]
    project = storage.load_project(pid)
    media = Media(id="m_dos", filename="rush.mp4", path="rush.mp4", duration=300.0, width=1920, height=1080, fps=60)
    project.media = [media]
    train = lambda t0: [[round(t0 + 0.5 * k, 3), 0.7] for k in range(6)]
    hits = train(20) + train(25) + train(29) + train(60)  # les deux premières rafales sont au milieu d'un combat
    cands = pvp.candidates([media], {media.id: hits})
    assert [c["closing"] for _, c in cands] == [False, False, True, True]

    def res(label, cout=None, confirme=False):
        return {"label": label, "confirme": confirme, "cout": cout, "part_recue": None, "vie": None, "camera": None,
                "fenetre": None, "raison": f"mesure {label}"}
    rows = [res("subit", 5.0), res("donne", 0.0, True), res("inconnu"), res("echange", 0.5)]
    mesures = {jury.key(c): r for (_, c), r in zip(cands, rows)}
    storage.save_signals(pid, media.id, {"hits": hits, "duel": {"v": duel_version(), "rafales": mesures}})
    text = "\n".join(dossier.media_section(project, media))
    starts = re.findall(r"· combo (\S+) →", text)
    assert starts == ["0:25.0", "1:00.0", "0:29.0"], "les donnés d'abord, puis les échanges, puis le reste ; la subie est absente"
    assert "1 non listées : tu les subis ou y encaisses trop" in text and "mesure subit" not in text
    # Une mesure gardée dans un ancien format (autre version, ou résultat mal formé) ne casse rien et ne s'affiche pas.
    storage.save_signals(pid, media.id, {"hits": hits, "duel": {"v": duel_version() - 1, "rafales": mesures}})
    ancien = "\n".join(dossier.media_section(project, media))
    assert len(re.findall(r"· combo (\S+) →", ancien)) == 4 and "mesure locale" not in ancien
    storage.save_signals(pid, media.id, {"hits": hits, "duel": {"v": duel_version(), "rafales": {jury.key(cands[0][1]): "abîmé"}}})
    assert "· combo" in "\n".join(dossier.media_section(project, media))


def duel_version():
    from app.analysis import duel
    return duel.VERSION


def test_definir_plans_warns_when_a_plan_reuses_a_taken_combo(gameplay):
    """`definir_plans` n'a pas de filtre propre (le réalisateur choisit) : il prévient quand un plan recouvre un combo que
    la mesure donne pour subi, sans refuser (le réalisateur a peut-être une raison de le montrer)."""
    from app.analysis import duel
    pid = _gameplay_project(gameplay, "Plans sur subi")
    m = storage.load_project(pid).media[0]
    res = {"label": "subit", "confirme": False, "cout": 6.0, "part_recue": None, "vie": None, "camera": None,
           "fenetre": None, "raison": "vie 9,5 → 3,0 cœurs"}
    storage.save_signals(pid, m.id, {"duel": {"v": duel.VERSION, "rafales": {"7.80-9.80": res}}})
    agent = chat.Agent(pid, _Ctx())
    out = agent.definir_plans(chat.DefinirPlans(plans=[chat.PlanIn(media="gameplay.mp4", debut=1.8, fin=4.6),
                                                       chat.PlanIn(media="gameplay.mp4", debut=7.0, fin=10.0)]))
    assert out.startswith("Montage remplacé.") and "ATTENTION" in out and "plan 2 (gameplay.mp4 0:07.0→0:10.0)" in out
    assert "plan 1" not in out.split("ATTENTION")[1].split("Retire")[0], "le plan sans combo subi n'est pas signalé"
    assert len(client.get(f"/api/projects/{pid}").json()["timeline"]["clips"]) == 2
