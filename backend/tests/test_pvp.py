"""Moteur 3D, détection des coups et style d'edit PvP."""
import subprocess
import time

import numpy as np
from fastapi.testclient import TestClient

from app import config
from app.ai import pvp
from app.analysis import hits
from app.main import app
from app.media import ffmpeg
from app.models import Clip, ImpactItem, TextItem, Timeline
from app.render import engine3d as e3
from app.render.scene3d import EFFECTS_3D, IMPACTS, TRANSITIONS_3D, Scene3D, needs_3d

SR = config.AUDIO_SR
COMBO_A = [2.0, 2.4, 2.8, 3.3, 3.7, 4.2]
COMBO_B = [8.0, 8.35, 8.7, 9.1, 9.5]


def _clicks(times, seconds=12.0, steps=True):
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.003, int(seconds * SR)).astype(np.float32)
    t = np.arange(int(0.2 * SR)) / SR
    hit = (np.sin(2 * np.pi * (150 - 200 * t) * t) * 0.9 + rng.normal(0, 0.4, t.size)) * np.exp(-25 * t)
    for h in times:
        i = int(h * SR)
        x[i:i + hit.size] += hit[: x.size - i]
    if steps:  # bruits de pas : attaque nette mais faible volume
        st = rng.normal(0, 0.1, int(0.08 * SR)) * np.exp(-60 * np.arange(int(0.08 * SR)) / SR)
        for k in np.arange(0.3, seconds - 0.2, 0.38):
            i = int(k * SR)
            x[i:i + st.size] += st
    return x


def test_hits_and_combos():
    found = hits.detect_hits(_clicks(COMBO_A + COMBO_B), SR)
    ts = [h[0] for h in found]
    for h in COMBO_A + COMBO_B:
        assert any(abs(h - t) < 0.04 for t in ts), (h, ts)
    assert len(ts) <= len(COMBO_A + COMBO_B) + 1, "les pas ne doivent pas compter comme des coups"
    cs = hits.combos(found)
    assert len(cs) == 2 and cs[0]["n"] == len(COMBO_A)


def test_generated_beat_has_expected_tempo():
    path = pvp.beat_track(128, bars=12)
    pcm = ffmpeg.decode_audio(path)
    bpm, _ = hits.beat_grid(pcm, SR)
    assert abs(bpm - 128) < 3 or abs(bpm - 64) < 2


def test_engine_projection_and_card():
    cam = e3.Camera(640, 360)
    full = e3.card(cam, e3.rotation())
    assert np.allclose(full, [[0, 0], [640, 0], [640, 360], [0, 360]], atol=1e-6)
    assert e3.faces_camera(full) and not e3.faces_camera(e3.card(cam, e3.rotation(0, 180, 0)))


def test_scene_renders_every_3d_item():
    W, H, fps = 320, 180, 30
    a = np.zeros((H, W, 3), np.uint8)
    a[:, :, 2] = 200
    b = np.zeros((H, W, 3), np.uint8)
    b[:, :, 0] = 200
    clips = [Clip(media_id="a", src_in=0, src_out=1.5)]
    for k, tr in enumerate(TRANSITIONS_3D):
        clips.append(Clip(media_id="b" if k % 2 == 0 else "a", src_in=0, src_out=1.2, transition=tr, transition_duration=0.6))
    for k, fx in enumerate(EFFECTS_3D):
        clips.append(Clip(media_id="a", src_in=0, src_out=1.2, effects=[fx]))
    tl = Timeline(clips=clips, texts=[TextItem(start=0.2, duration=1.0, text="COMBO x5", style="texte_3d")],
                  impacts=[ImpactItem(start=0.5, kind=k) for k in IMPACTS])
    assert needs_3d(tl)
    sc = Scene3D(tl, W, H, fps)
    changed = 0
    for n in range(int(sc.total * fps)):
        i = sc._clip_at(n / fps)
        src = a if tl.clips[i].media_id == "a" else b
        out = sc.process(n, src.copy())
        assert out.shape == (H, W, 3) and out.dtype == np.uint8
        changed += int(not np.array_equal(out, src))
    assert changed > sc.total * fps * 0.4


def _wait(c, job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_pvp_edit_and_3d_export(tmp_path):
    wav = tmp_path / "hits.wav"
    import wave
    pcm = (np.clip(_clicks(COMBO_A + COMBO_B), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    src = tmp_path / "gameplay.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=30:d=12", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Test PvP"}).json()["id"]
    with src.open("rb") as f:
        assert c.post(f"/api/projects/{pid}/media", files=[("files", ("gameplay.mp4", f, "video/mp4"))]).status_code == 200
    j = _wait(c, c.post(f"/api/projects/{pid}/auto-edit", json={"style": "pvp"}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["director"] == "pvp" and len(tl["clips"]) >= 3
    assert any(x["speed"] < 1 for x in tl["clips"]), "ralenti sur le coup final"
    assert sum(i["kind"] == "secousse" for i in tl["impacts"]) >= len(COMBO_A + COMBO_B) - 1
    assert any(t["style"] == "texte_3d" for t in tl["texts"])
    assert any(x["transition"] in TRANSITIONS_3D or x["transition"] != "aucune" for x in tl["clips"][1:])
    # chaque coupe entre combos tombe sur un temps de la musique
    beat = 60 / pvp.DEFAULT_BPM
    t, cuts = 0.0, []
    for x in tl["clips"]:
        if x["transition"] != "aucune":
            cuts.append(t)
        t += (x["src_out"] - x["src_in"]) / x["speed"]
    for cut in cuts:
        assert abs(cut / beat - round(cut / beat)) < 0.02, cut
    j = _wait(c, c.post(f"/api/projects/{pid}/export", json={"quality": "720p", "fps": 30}).json())
    assert j["status"] == "termine", j
    r = c.get(f"/api/projects/{pid}/exports/{j['result']}")
    out = tmp_path / "pvp.mp4"
    out.write_bytes(r.content)
    info = ffmpeg.probe(out)
    assert info["has_audio"] and info["height"] == 720 and abs(info["duration"] - t) < 0.3


# ---------------------------------------------------------------- rafales, menus, jury

def _train(start, n, gap=0.5, strength=0.7):
    return [[round(start + k * gap, 3), strength] for k in range(n)]


def test_bursts_split_a_whole_duel_into_real_combos():
    """Un duel où ça frappe sans arrêt n'est pas « un combo de cent coups » : les rafales suivent le rythme."""
    # trois séries séparées de 1,2 s et 1,3 s : assez pour rompre un combo, pas assez pour l'ancien regroupement
    duel = _train(10, 6) + _train(13.7, 5) + _train(17.0, 24)
    assert len(hits.combos(duel)) == 1, "l'ancien regroupement rend le duel entier"
    bs = hits.bursts(duel)
    assert [b["n"] for b in bs[:2]] == [6, 5] and len(bs) == 3
    last = bs[2]
    assert last["end"] - last["start"] <= hits.BURST_MAX + 1e-6, "une rafale trop longue est bornée"
    assert last["end"] == duel[-1][0], "on garde la fin : c'est là que tombe le coup final"
    assert hits.bursts(_train(0, 3)) == [], "trois coups ne font pas un combo"


def test_overlays_find_a_menu_and_hits_inside_are_dropped():
    from app.analysis import video
    fps = 5
    lum = [110.0] * 200
    lum[60:75] = [40.0] * 15  # inventaire ouvert de 12 s à 15 s
    lum[150] = 108.0
    ov = video.overlays(lum, fps)
    assert len(ov) == 1 and ov[0][0] <= 12.0 and ov[0][1] >= 15.0
    assert video.overlays([20.0] * 200, fps) == [], "une scène durablement sombre n'est pas un menu"
    clicks = [[11.0, 0.9], [12.5, 0.9], [13.0, 0.9], [13.5, 0.9], [14.0, 0.9], [16.0, 0.9]]
    assert [h[0] for h in hits.outside(clicks, ov)] == [11.0, 16.0]


class _M:
    def __init__(self, mid="m1", duration=300.0):
        self.id, self.duration, self.filename, self.path, self.proxy = mid, duration, "rush.mp4", "rush.mp4", None


def _three_fights():
    # A : la plus longue rafale (subie), B : un combo qui finit par un kill, C : un échange.
    return _train(20, 14, strength=0.9) + _train(60, 9) + _train(100, 7)


def test_build_never_returns_one_giant_block():
    from app.models import EditSettings
    duel = [[round(5 + k * 0.5, 3), 0.7] for k in range(150)]  # 75 s de coups sans pause
    tl = pvp.build([_M()], {"m1": duel}, EditSettings(style="pvp"))
    assert tl.clips and max(c.src_out - c.src_in for c in tl.clips) <= hits.BURST_MAX + 2.0
    assert tl.duration <= 30 * 1.3 + 3


def test_build_follows_the_jury():
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _three_fights()
    cands = pvp.candidates([m], {"m1": h})
    assert len(cands) == 3 and cands[0][1]["n"] == 14
    a, b, c = (jury.key(x) for _, x in cands)
    verdicts = {("m1", a): {"verdict": "adversaire_domine", "fin": "mort", "note": 1, "raison": "le joueur meurt", "n": 14},
                ("m1", b): {"verdict": "joueur_domine", "fin": "kill", "note": 9, "raison": "kill net", "n": 9},
                ("m1", c): {"verdict": "echange", "fin": "rien", "note": 5, "raison": "échange", "n": 7}}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=verdicts)
    kept = [(x.src_in, x.src_out) for x in tl.clips]
    assert not any(s < 27 and e > 20 for s, e in kept), "la rafale subie n'entre jamais, même la mieux notée au son"
    assert any(s < 64.5 and e > 60 for s, e in kept) and any(s < 103.5 and e > 100 for s, e in kept)
    assert "Jury IA : à ton avantage, kill, 9/10" in " ".join(x.reason for x in tl.clips)
    assert any("Écarté par le jury IA" in x.reason and "tu meurs" in x.reason for x in tl.rejected)
    assert "3 rafales regardées (à ton avantage : 1, subies : 1, kills : 1)" in tl.summary
    # Sans jury : classement au son, et le résumé le dit.
    local = pvp.build([m], {"m1": h}, EditSettings(style="pvp"))
    assert any(s < 27 and e > 20 for s, e in ((x.src_in, x.src_out) for x in local.clips))
    assert "ne sait pas qui porte les coups" in local.summary


def test_build_prefers_order_over_filling():
    """Une bonne rafale n'est pas sautée au profit d'une plus faible qui « rentre » dans la durée cible."""
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _train(20, 15) + _train(60, 15) + _train(100, 15) + _train(140, 15) + _train(180, 4)
    cands = pvp.candidates([m], {"m1": h})
    notes = [9, 8, 7, 7, 3]
    verdicts = {("m1", jury.key(c)): {"verdict": "joueur_domine", "fin": "rien", "note": n, "raison": "", "n": c["n"]}
                for (_, c), n in zip(cands, notes)}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=verdicts)
    assert not any(c.src_in >= 179 for c in tl.clips), "la petite rafale notée 3 ne bouche pas le trou"


def test_shortlist_shows_fight_endings_first_and_spreads():
    h = _train(10, 12) + _train(17, 12) + _train(24, 12) + _train(200, 5) + _train(260, 6)
    cands = pvp.candidates([_M()], {"m1": h})
    closing = [c["start"] for _, c in cands if c["closing"]]
    assert closing == [24.0, 200.0, 260.0], "dernière rafale de chaque combat"
    sl = pvp.shortlist(cands, 4)
    assert {c["start"] for _, c in sl[:2]} <= set(closing), "la moitié des places va aux fins de combat"
    assert len({(mid, c["start"]) for mid, c in sl}) == 4, "pas de doublon"
    from app.ai import jury
    known = {("m1", jury.key(sl[0][1]))}
    assert sl[0][1] not in [c for _, c in pvp.shortlist(cands, 4, known)], "une rafale déjà jugée n'est pas remontrée"


def test_jury_judges_once_then_reads_its_cache(monkeypatch):
    import types

    import anthropic

    from app import config, storage
    from app.ai import jury, vision
    pid = TestClient(app).post("/api/projects", json={"name": "Jury"}).json()["id"]
    m = _M("m_jury")
    cands = pvp.candidates([m], {m.id: _three_fights()})
    asked = []

    def create(**kw):
        asked.append(kw)
        rows = [{"index": 0, "verdict": "adversaire_domine", "fin": "mort", "note": 1, "raison": "Le joueur meurt."},
                {"index": 1, "verdict": "joueur_domine", "fin": "kill", "note": 14, "raison": "Kill net."},
                {"index": 7, "verdict": "echange", "fin": "rien", "note": 5, "raison": "index hors liste"}]
        text = types.SimpleNamespace(type="text", text=__import__("json").dumps({"candidats": rows}))
        return types.SimpleNamespace(content=[text], stop_reason="end_turn", model="claude-test")

    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(config, "claude_available", lambda: True)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
    monkeypatch.setattr(vision, "jpeg", lambda path, t, zone="entier", width=768: b"\xff\xd8faux")
    got = jury.judge(pid, [(m, c) for _, c in cands])
    assert len(asked) == 1
    content = asked[0]["messages"][0]["content"]
    assert sum(b["type"] == "image" for b in content) == 12, "4 images par rafale"
    assert "Candidat 0 — rafale de 14 coups" in content[0]["text"]
    k = [jury.key(c) for _, c in cands]
    assert got[(m.id, k[0])]["fin"] == "mort" and got[(m.id, k[1])]["note"] == 10, "note bornée à 10"
    assert (m.id, k[2]) not in got, "pas de verdict inventé pour une rafale sans réponse"
    assert storage.load_signals(pid, m.id)["jury"]["verdicts"][k[1]]["raison"] == "Kill net"
    again = jury.judge(pid, [(m, c) for _, c in cands[:2]])
    assert len(asked) == 1 and again[(m.id, k[1])]["verdict"] == "joueur_domine", "second montage : cache, aucun appel"
    assert jury.label(got[(m.id, k[1])]) == "à ton avantage, kill, 10/10 — Kill net"
    # Sans clé : seulement le cache.
    monkeypatch.setattr(config, "claude_available", lambda: False)
    assert set(jury.judge(pid, [(m, c) for _, c in cands])) == {(m.id, k[0]), (m.id, k[1])} and len(asked) == 1


def test_pvp_brief_is_about_combos_not_speech():
    from app.ai import roles
    from app.models import EditSettings, Project
    b, by = roles.brief(Project(name="x"), [], EditSettings(style="pvp"))
    assert by == "local" and "combos" in b["objectif"] and "menu" in b["promesse"]


def test_best_combo_closes_the_edit_and_only_it_is_slowed():
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 8) + _train(100, 8) + _train(140, 8)
    cands = pvp.candidates([m], {"m1": h})
    notes = [6, 9, 7, 8]  # meilleur : 60 s ; deuxième : 140 s
    verdicts = {("m1", jury.key(c)): {"verdict": "joueur_domine", "fin": "rien", "note": n, "raison": "", "n": 8}
                for (_, c), n in zip(cands, notes)}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "2026-11-07 14-50-54", verdicts=verdicts)
    starts = [round(c.src_in) for c in tl.clips if c.speed == 1]
    assert starts[0] in range(137, 141), "le deuxième meilleur combo ouvre"
    assert starts[-1] in range(57, 61), "le meilleur ferme"
    assert sum(c.speed < 1 for c in tl.clips) == 1 and tl.clips[-1].speed < 1, "un seul ralenti, sur le coup final du meilleur"
    assert tl.texts[0].text == "HIGHLIGHTS PVP", "un nom de fichier n'est pas un titre"
    assert [t.text for t in tl.texts].count("COMBO x8") == 4
    assert pvp._title("Duels du samedi") == "DUELS DU SAMEDI" and pvp._title("") == "HIGHLIGHTS PVP"
    # Un kill garde son ralenti même s'il n'est pas le meilleur combo.
    verdicts[("m1", jury.key(cands[0][1]))]["fin"] = "kill"
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts)
    assert sum(c.speed < 1 for c in tl.clips) == 2


def test_everything_rejected_by_the_jury_does_not_crash():
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _train(10, 6, strength=0.9)
    (mid, c), = pvp.candidates([m], {"m1": h})
    verdicts = {(mid, jury.key(c)): {"verdict": "adversaire_domine", "fin": "mort", "note": 1, "raison": "tué", "n": 6}}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=verdicts)
    assert tl.clips == [] and "aucune à ton avantage" in tl.summary
    assert len(tl.rejected) == 1 and "tu meurs" in tl.rejected[0].reason


def test_slow_rush_keeps_its_fights_next_to_a_fast_one():
    """Le repli (coups espacés) se décide rush par rush : un rush rapide n'efface pas les combats d'un rush lent."""
    slow = [[round(10 + f * 30 + k * 1.0, 3), 0.8] for f in range(5) for k in range(5)]
    alone = pvp.candidates([_M("A")], {"A": slow})
    both = pvp.candidates([_M("A"), _M("B")], {"A": slow, "B": _train(50, 4)})
    assert len(alone) == 5 and len(both) == 6
    from app.ai import jury
    assert {jury.key(c) for mid, c in both if mid == "A"} == {jury.key(c) for _, c in alone}, "mêmes clés : le cache du jury tient"


def test_overlays_ignore_a_long_dark_passage():
    from app.analysis import video
    lum = [100.0] * 500 + [45.0] * 60 + [100.0] * 500  # 12 s sombres à 5 images/s : un tunnel, pas un menu
    assert video.overlays(lum, 5) == []
    lum = [100.0] * 500 + [45.0] * 15 + [100.0] * 500  # 3 s : un inventaire
    assert len(video.overlays(lum, 5)) == 1


def test_jury_does_not_resend_candidates_left_unanswered(monkeypatch):
    import json as _json
    import types

    import anthropic

    from app import config, storage
    from app.ai import jury, vision
    pid = TestClient(app).post("/api/projects", json={"name": "Jury muet"}).json()["id"]
    m = _M("m_muet")
    cands = pvp.candidates([m], {m.id: _three_fights()})
    calls = []

    def create(**kw):
        calls.append(kw)
        rows = [{"index": 0, "verdict": "joueur_domine", "fin": "rien", "note": 7, "raison": "ok"}]  # 2 candidats sans réponse
        text = types.SimpleNamespace(type="text", text=_json.dumps({"candidats": rows}))
        return types.SimpleNamespace(content=[text], stop_reason="end_turn", model="claude-test")

    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(config, "claude_available", lambda: True)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
    monkeypatch.setattr(vision, "jpeg", lambda path, t, zone="entier", width=768: b"faux")
    assert len(jury.judge(pid, [(m, c) for _, c in cands])) == 1
    assert len(jury.skipped(pid, m.id)) == 2
    assert len(jury.judge(pid, [(m, c) for _, c in cands])) == 1 and len(calls) == 1, "pas de second appel pour les mêmes rafales"
    known = {(m.id, jury.key(cands[0][1]))} | {(m.id, k) for k in jury.skipped(pid, m.id)}
    assert pvp.shortlist(cands, 14, known) == []
    assert storage.load_signals(pid, m.id)["jury"]["verdicts"]

