"""Moteur 3D, détection des coups et style d'edit PvP."""
import subprocess
import time

import numpy as np
import pytest
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
    subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
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
    shakes = sum(i["kind"] == "secousse" for i in tl["impacts"])
    assert 2 <= shakes <= 2 * pvp.SHAKES_PER_COMBO, "une secousse par coup final, quelques coups forts, pas chaque coup"
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
    # Chaque combo de 15 coups est réduit à sa portion la plus dense (un plan court) : la cible est donc plus basse pour
    # que les quatre bons combos suffisent à l'atteindre.
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=16), verdicts=verdicts)
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


def _four_combos():
    from app.ai import jury
    m = _M()
    h = _train(20, 8) + _train(60, 8) + _train(100, 8) + _train(140, 8)
    cands = pvp.candidates([m], {"m1": h})
    notes = [6, 9, 7, 8]  # meilleur : 60 s ; deuxième : 140 s
    verdicts = {("m1", jury.key(c)): {"verdict": "joueur_domine", "fin": "rien", "note": n, "raison": "", "n": 8}
                for (_, c), n in zip(cands, notes)}
    return m, h, cands, verdicts


def test_edit_opens_on_a_teaser_and_closes_on_the_best_combo():
    from app.ai import jury
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "2026-11-07 14-50-54", verdicts=verdicts)
    teaser = tl.clips[0]
    assert 60 <= teaser.src_in and teaser.src_out <= 64.5 and teaser.duration < 2, "aperçu du coup final du meilleur combo"
    beat = 60 / pvp.DEFAULT_BPM
    assert abs(teaser.duration / beat - round(teaser.duration / beat)) < 0.02, "l'aperçu dure un nombre entier de temps"
    normal = [round(c.src_in) for c in tl.clips[1:] if c.speed == 1]
    assert normal[0] in range(137, 141), "le deuxième meilleur combo suit l'aperçu"
    assert normal[-1] in range(60, 64), "le meilleur ferme (ses derniers coups : le plan tient, ralenti compris, dans PLAN_MAX)"
    assert sum(c.speed < 1 for c in tl.clips) == 1 and tl.clips[-1].speed < 1, "un seul ralenti, sur le coup final du meilleur"
    assert tl.clips[-1].duration >= 2.8, "le plan final laisse le temps de voir le coup"
    texts = [t.text for t in tl.texts]
    vus = [t for t, _ in h if 59 <= t <= 68 and any(c.src_in <= t < c.src_out for c in tl.clips[1:] if 55 < c.src_in < 70)]
    assert texts == [f"COMBO x{len(vus)}"] and 3 <= len(vus) < 8, "une seule carte avec les coups montrés, et l'aperçu ne dévoile pas le compte"
    assert all(t.start + t.duration <= tl.duration + 1e-6 for t in tl.texts), "aucun texte ne dépasse la fin"
    assert [c.transition for c in tl.clips].count(pvp.CUT) == 1 and tl.clips[-2].transition == pvp.CUT, "un seul flash, à l'entrée du meilleur combo"
    assert {c.transition for c in tl.clips} == {"aucune", pvp.CUT}
    named = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "Duels du samedi", verdicts=verdicts)
    assert named.texts[0].text == "DUELS DU SAMEDI" and named.texts[0].start + named.texts[0].duration <= named.clips[0].duration
    assert pvp._title("Duels du samedi") == "DUELS DU SAMEDI" and pvp._title("") == "HIGHLIGHTS PVP"
    # Un kill garde son ralenti et sa carte même s'il n'est pas le meilleur combo.
    verdicts[("m1", jury.key(cands[0][1]))]["fin"] = "kill"
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts)
    assert sum(c.speed < 1 for c in tl.clips) == 2 and "KILL" in [t.text for t in tl.texts]


def test_dressing_is_sparse_and_breathes_before_the_finale():
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts)
    kinds = [i.kind for i in tl.impacts]
    assert kinds.count("secousse") <= 4 * pvp.SHAKES_PER_COMBO, "32 coups, mais au plus 2 secousses par combo"
    assert kinds.count("etincelles") == 1 and kinds.count("zoom") == 1, "étincelles et zoom seulement sur le coup final de l'edit"
    assert kinds.count("flash") == 1 and kinds.count("onde") == 1, "un seul grand final"
    assert [x.kind for x in tl.sfx].count("boom") == 1 and [x.kind for x in tl.sfx].count("whoosh") == 1
    slow_start = tl.duration - tl.clips[-1].duration
    assert not any(slow_start - 0.8 <= i.start < slow_start - 0.01 for i in tl.impacts), "respiration avant le ralenti final"
    assert any(i.kind == "secousse" and i.intensity >= 1.5 for i in tl.impacts), "le coup final garde sa secousse franche"


def test_music_peak_lands_on_a_cut(monkeypatch):
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    beat = 60 / 120
    monkeypatch.setattr(pvp, "music_grid", lambda path: (120.0, 0.25))
    monkeypatch.setattr(pvp, "_music_peak", lambda path: (15.25, 200.0))
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", "morceau.mp3", verdicts=verdicts)
    cuts, t = [], 0.0
    for c in tl.clips:
        if c.speed == 1:  # début de l'aperçu et de chaque combo
            cuts.append(round(t, 3))
        t += c.duration
    peak_at = round(15.25 - tl.music.start, 3)  # instant du montage où tombe le pic
    assert any(abs(peak_at - x) < 0.02 for x in cuts), (peak_at, cuts)
    assert abs((tl.music.start - 0.25) / beat - round((tl.music.start - 0.25) / beat)) < 0.02, "la musique part toujours sur un temps"
    assert "Pic de la musique posé sur la coupe" in tl.summary
    # Morceau trop court pour tenir tout l'edit après ce décalage : on ne touche pas au départ.
    monkeypatch.setattr(pvp, "_music_peak", lambda path: (15.25, tl.duration + 0.5))
    tl2 = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", "morceau.mp3", verdicts=verdicts)
    assert tl2.music.start == 0.25 and "Pic de la musique" not in tl2.summary


def test_quality_check_does_not_add_slow_zoom_on_gameplay_nor_push_text_past_the_end():
    from app.ai import quality
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    s = EditSettings(style="pvp", target_duration=60)
    tl = pvp.build([m], {"m1": h}, s, "x", verdicts=verdicts)
    tl = quality.check_and_fix(tl, {}, s)
    assert not any("zoom_lent" in c.effects for c in tl.clips), "du jeu filmé n'est pas un plan fixe"
    assert all(t.start + t.duration <= tl.duration + 1e-6 for t in tl.texts), "un texte prolongé ne dépasse pas la fin"


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


def test_a_duel_ending_in_noise_is_still_a_fight_ending():
    """Après un kill, le jeu fait du bruit (victoire, clics) : la dernière rafale du duel reste une fin de combat."""
    duel = _train(10, 10) + _train(15.8, 9)  # un duel de 19 coups en deux rafales, la dernière finit à 19,8 s
    noise = [[20.9, 0.5], [22.2, 0.5], [23.6, 0.5]]  # sons isolés juste après : l'ancienne règle du silence échouait
    cands = pvp.candidates([_M()], {"m1": duel + noise + _train(100, 5)})
    last = max((c for _, c in cands if c["start"] < 30), key=lambda c: c["end"])
    assert last["closing"] and last["fight"] >= 19
    assert pvp.shortlist(cands, 2)[0][1] is last, "le plus gros duel est montré au jury en premier"


# ---------------------------------------------------------------- qui porte le combo : le signal local

def _res(label, cout=None, part=None, confirme=False, raison="vie 9,5 → 4,0 cœurs (creux 4,0)"):
    return {"label": label, "confirme": confirme, "cout": cout, "part_recue": part, "vie": None, "camera": None,
            "fenetre": None, "raison": raison}


def _duel_for(cands, results):
    from app.ai import jury
    return {(mid, jury.key(c)): r for (mid, c), r in zip(cands, results)}


def _all_like(cands, verdict="joueur_domine", note=9, fin="kill"):
    from app.ai import jury
    return {(mid, jury.key(c)): {"verdict": verdict, "fin": fin, "note": note, "raison": "", "n": c["n"]} for mid, c in cands}


def test_a_combo_taken_by_the_opponent_never_enters_even_if_the_jury_likes_it():
    from app.models import EditSettings
    m = _M()
    h = _three_fights()  # A : 14 coups forts (20 s), B : 9 coups (60 s), C : 7 coups (100 s)
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [_res("subit", cout=5.0), _res("donne", cout=0.5, confirme=True), _res("inconnu")])
    loved = _all_like(cands, fin="rien")  # le jury adore tout, y compris la rafale subie, mais n'y voit aucun kill
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=loved, duel=duel)
    kept = [(x.src_in, x.src_out) for x in tl.clips]
    assert not any(s < 27 and e > 20 for s, e in kept), "subit : jamais retenu"
    assert any(s < 64.5 and e > 60 for s, e in kept)
    assert any("tu subissais" in x.reason and x.src_in == 20.0 for x in tl.rejected)
    ranked, why, _ = pvp.rank_candidates(pvp.annotate(cands, duel), loved, 30.0)
    assert all(c["start"] != 20.0 for _, c in ranked) and any("tu subissais" in w for w in why.values())
    assert "1 rafale écartée parce que tu la subissais" in tl.summary
    assert "Signal local (vie et caméra) : 1 où tu enchaînes" in tl.summary
    assert "Signal local : tu enchaînes" in " ".join(x.reason for x in tl.clips)


def test_costly_exchange_is_dropped_and_cheap_one_stays():
    from app.models import EditSettings
    m = _M()
    h = _three_fights()
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [_res("echange", cout=4.0), _res("echange", cout=0.5), _res("echange", part=0.8)])
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=_all_like(cands, "echange", 6, "rien"), duel=duel)
    kept = [(x.src_in, x.src_out) for x in tl.clips]
    assert any(s < 64.5 and e > 60 for s, e in kept), "l'échange qui coûte un demi-cœur reste"
    assert not any(s < 27 and e > 20 for s, e in kept), "4 cœurs perdus : écarté"
    assert not any(s < 107 and e > 100 for s, e in kept), "80 % de coups reçus : écarté"
    assert sum("tu encaissais aussi" in x.reason for x in tl.rejected) == 2
    # Un cœur perdu, une potion de soin ou la moitié des coups reçus ne passent pas non plus.
    pas_bon = _duel_for(cands, [_res("echange", cout=1.0), {**_res("echange", cout=0.5), "vie": {"soin": True}},
                                _res("echange", part=0.5)])
    ranked, why, _ = pvp.rank_candidates(pvp.annotate(cands, pas_bon), _all_like(cands, "echange", 6, "rien"), 30.0)
    assert ranked == [] and len(why) == 3 and all("tu encaissais aussi" in w for w in why.values())


def test_gives_confirmed_by_the_signal_come_first():
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()  # notes du jury : 6, 9, 7, 8 (le meilleur au jury est à 60 s)
    duel = _duel_for(cands, [_res("donne", cout=0.0, confirme=True), _res("inconnu"), _res("inconnu"), _res("inconnu")])
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts, duel=duel)
    assert 20 <= tl.clips[-1].src_in < 25, "le combo que tu donnes (note 6) passe avant la note 9 non tranchée : il ferme l'edit"
    ranked, _, _ = pvp.rank_candidates(pvp.annotate(cands, duel), verdicts, 60.0)
    assert ranked[0][1]["start"] == 20.0


def test_unknown_signal_changes_nothing():
    """Interface masquée, autre jeu, barre introuvable : « inconnu » partout donne le montage d'avant."""
    from app.analysis import duel as duel_an
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    s = EditSettings(style="pvp", target_duration=60)
    base = pvp.build([m], {"m1": h}, s, "x", verdicts=verdicts)
    inconnu = _duel_for(cands, [duel_an.inconnu("barre de vie introuvable")] * 4)
    same = pvp.build([m], {"m1": h}, s, "x", verdicts=verdicts, duel=inconnu)
    assert [(c.src_in, c.src_out, c.speed) for c in same.clips] == [(c.src_in, c.src_out, c.speed) for c in base.clips]
    assert "0 où tu enchaînes" in same.summary and "4 non tranchées" in same.summary and "écartée" not in same.summary
    assert "Signal local" not in base.summary, "jamais analysé : pas une ligne de plus dans le résumé"


def test_few_good_combos_make_a_shorter_honest_edit():
    from app.models import EditSettings
    m, h, cands, verdicts = _four_combos()
    duel = _duel_for(cands, [_res("subit", cout=5.0), _res("donne", cout=0.5, confirme=True),
                             _res("subit", cout=4.0), _res("subit", cout=6.0)])
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts, duel=duel)
    assert all(55 < c.src_in < 65 for c in tl.clips if c.speed == 1), "seul le combo donné est monté, pas de remplissage"
    assert tl.duration < 0.5 * 60
    assert "3 rafales écartées parce que tu les subissais" in tl.summary
    assert "Montage plus court que la cible" in tl.summary and "ne servent pas de remplissage" in tl.summary
    assert sum("tu subissais" in x.reason for x in tl.rejected) == 3


def test_when_everything_is_taken_the_edit_is_empty_and_says_why():
    from app.models import EditSettings
    m = _M()
    h = _three_fights()
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [_res("subit", cout=6.0)] * 3)
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=_all_like(cands, fin="rien"), duel=duel)
    assert tl.clips == [] and "3 sont écartées parce que tu les subissais" in tl.summary
    assert len(tl.rejected) == 3 and all("tu subissais" in x.reason for x in tl.rejected)


def test_shortlist_never_sends_a_taken_combo_to_the_jury():
    m = _M()
    h = _three_fights()
    cands = pvp.candidates([m], {"m1": h})
    # Trois combats d'une seule rafale : chacune est la fin de son combat.
    duel = _duel_for(cands, [_res("subit", cout=5.0), _res("echange", cout=3.0), _res("donne", confirme=True)])
    pvp.annotate(cands, duel)
    sl = pvp.shortlist(cands, 14)
    assert [c["start"] for _, c in sl] == [100.0, 20.0], "l'échange coûteux ne part pas au jury (payant) ; la fin de combat " \
                                                         "subie oui, pour voir si le joueur tue (derrière le combo donné)"
    pvp.annotate(cands, _duel_for(cands, [_res("inconnu"), _res("donne"), _res("donne", confirme=True)]))
    assert [c["start"] for _, c in pvp.shortlist(cands, 14)][:2] == [100.0, 60.0], "les combos donnés d'abord"
    # Une rafale subie au milieu d'un combat (suivie d'autres coups) ne part jamais au jury.
    milieu = pvp.candidates([_M()], {"m1": _train(20, 6) + _train(25, 6) + _train(29, 6) + _train(60, 6)})
    assert [c["closing"] for _, c in milieu] == [False, False, True, True]
    pvp.annotate(milieu, _duel_for(milieu, [_res("subit", cout=5.0)] * 4))
    assert {c["start"] for _, c in pvp.shortlist(milieu, 14)} == {29.0, 60.0}, "les subies du milieu d'un combat ne partent pas"


def test_the_won_duel_enters_only_if_the_jury_sees_the_kill():
    """La seule victoire du rush : la vie a chuté pendant le combat, la mesure la donne pour subie, mais c'est la
    dernière rafale du combat. Gardée quand le jury y voit le kill du joueur ; refusée sans verdict ou sans kill
    (c'est alors la défaite : mort, ou rien)."""
    from app.models import EditSettings
    m = _M()
    h = _three_fights()
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [_res("inconnu"), _res("subit", cout=5.0), _res("inconnu")])  # B = 60 s, le duel gagné
    s = EditSettings(style="pvp", target_duration=20)

    def rafale_b(tl):
        return [x for x in tl.clips if 55 < x.src_in < 65]
    sans_verdict = pvp.build([m], {"m1": h}, s, duel=duel)
    assert not rafale_b(sans_verdict), "pas de verdict : on ne sait pas si c'est le kill"
    assert any("tu subissais probablement cette fin de combat" in x.reason for x in sans_verdict.rejected)
    mort = _all_like(cands, "adversaire_domine", 1, "mort")
    assert not rafale_b(pvp.build([m], {"m1": h}, s, verdicts=mort, duel=duel))
    rien = _all_like(cands, "echange", 6, "rien")
    tl = pvp.build([m], {"m1": h}, s, verdicts=rien, duel=duel)
    assert not rafale_b(tl) and any("n'y voit pas ton kill" in x.reason for x in tl.rejected)
    kill = _all_like(cands, "joueur_domine", 9, "kill")
    tl = pvp.build([m], {"m1": h}, s, verdicts=kill, duel=duel)
    assert rafale_b(tl), "le jury voit le joueur tuer l'adversaire : le duel gagné entre"
    assert "Signal local : tu subis" in " ".join(x.reason for x in rafale_b(tl)), "et la mesure reste affichée telle quelle"
    assert "écartée" not in tl.summary, "elle n'est pas comptée comme écartée"


def test_pipeline_pays_the_jury_only_for_bursts_the_signal_kept(monkeypatch):
    from app import config, pipeline, storage
    from app.models import EditSettings, Media
    pid = TestClient(app).post("/api/projects", json={"name": "Pipeline duel"}).json()["id"]
    project = storage.load_project(pid)
    media = Media(id="m_pipe", filename="rush.mp4", path="rush.mp4", duration=300.0)
    project.media = [media]
    storage.save_signals(pid, media.id, {"hits": _three_fights()})
    cands = pvp.candidates([media], {media.id: _three_fights()})
    duel = _duel_for(cands, [_res("subit", cout=6.0), _res("donne", confirme=True), _res("echange", cout=3.5)])
    sent = []

    def juge(project_id, rows, ctx=None):
        sent.extend(c["start"] for _, c in rows)
        return {}

    class Ctx:
        def step(self, *a, **k):
            pass

        def check(self):
            pass

    def plante(*a, **k):
        raise RuntimeError("ffmpeg absent")

    monkeypatch.setattr(pipeline.duel_an, "analyse", lambda *a, **k: duel)
    monkeypatch.setattr(pipeline.jury_mod, "judge", juge)
    monkeypatch.setattr(config, "claude_available", lambda: True)
    tl = pipeline._pvp_edit(project, EditSettings(style="pvp"), None, Ctx())
    assert sent == [60.0, 20.0], "le jury voit la rafale donnée, puis la fin de combat subie (à confirmer) ; pas l'échange coûteux"
    assert tl.clips and not any(x.src_in < 27 and x.src_out > 20 for x in tl.clips), "sans kill confirmé, la subie reste dehors"
    # Si la mesure plante, le montage du bouton se fait quand même (comme avant) et le dit.
    monkeypatch.setattr(pipeline.duel_an, "analyse", plante)
    project.warnings = []
    tl = pipeline._pvp_edit(project, EditSettings(style="pvp"), None, Ctx())
    assert tl.clips and any("Mesure locale" in w for w in project.warnings)
    # Mais un edit sur des rafales choisies (chat) n'a plus aucun filtre, ni jury : il est refusé et dit pourquoi.
    with pytest.raises(ValueError, match="mesure de la vie et de la caméra a échoué"):
        pipeline._pvp_edit(project, EditSettings(style="pvp"), None, Ctx(), chosen=[("m_pipe", 58.0, 66.0)])


def test_jury_sees_the_life_bar_and_knows_the_rule(monkeypatch, tmp_path):
    import json as _json
    import types

    import anthropic

    from app import config
    from app.ai import jury, vision
    pid = TestClient(app).post("/api/projects", json={"name": "Jury vie"}).json()["id"]
    m = _M("m_vie")
    m.width, m.height = 1920, 1080
    mesure = _res("inconnu", raison="vie 9,5 → 8,0 cœurs (creux 8,0)")
    mesure["vie"] = {"verdict": "donne"}
    plain = pvp.candidates([m], {m.id: _three_fights()})
    cands = pvp.candidates([m], {m.id: _three_fights()}, duel=_duel_for(plain, [mesure] * 3))
    asked, zones = [], []

    def create(**kw):
        asked.append(kw)
        rows = [{"index": 0, "verdict": "echange", "fin": "rien", "note": 5, "raison": "ok"}]
        text = types.SimpleNamespace(type="text", text=_json.dumps({"candidats": rows}))
        return types.SimpleNamespace(content=[text], stop_reason="end_turn", model="claude-test")

    (tmp_path / "proxy_540p.mp4").write_bytes(b"x")
    m.proxy = str(tmp_path / "proxy_540p.mp4")

    def jpeg(path, t, zone="entier", width=768):
        zones.append((zone, width, path))
        return b"faux"

    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(config, "claude_available", lambda: True)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
    monkeypatch.setattr(vision, "jpeg", jpeg)
    jury.judge(pid, [(m, c) for _, c in cands])
    content = asked[0]["messages"][0]["content"]
    assert sum(b["type"] == "image" for b in content) == 18, "4 images et 2 recadrages de la vie par rafale"
    crops = [(z, w, p) for z, w, p in zones if isinstance(z, tuple)]
    assert len(crops) == 6 and all(w == jury.LIFE_WIDTH for _, w, _ in crops)
    assert all(p == m.path for _, _, p in crops), "le recadrage de la vie est agrandi : lu dans le rush, pas dans le proxy"
    assert all(p == m.proxy for z, _, p in zones if not isinstance(z, tuple)), "les images entières viennent du proxy"
    x, y, w, h = crops[0][0]
    assert 0.38 < x < 0.42 and 0.88 < y < 0.92 and 0.1 < w < 0.3 and h < 0.1, "zone de la barre de cœurs, en fractions de l'image"
    assert any("barre de vie du joueur, recadrage zoomé (avant la rafale)" in b.get("text", "") for b in content)
    assert "Mesure locale (indicative) : vie 9,5 → 8,0" in content[0]["text"]
    system = asked[0]["system"]
    assert "QUE les combos que le joueur enchaîne" in system and "perd nettement plus" in system
    assert "adversaire_domine" in system and "Hits" in system and "le kill prime sur la vie perdue" in system
    # Un verdict déjà en cache, rendu avant ce changement, reste lisible.
    assert jury.cached(pid, m.id)[jury.key(cands[0][1])]["verdict"] == "echange"


def test_jury_is_told_when_a_fight_ending_may_be_the_win(monkeypatch):
    import json as _json
    import types

    import anthropic

    from app import config
    from app.ai import jury, vision
    pid = TestClient(app).post("/api/projects", json={"name": "Jury fin de combat"}).json()["id"]
    m = _M("m_fin")
    m.width, m.height = 1920, 1080
    plain = pvp.candidates([m], {m.id: _three_fights()})
    mesure = _res("subit", cout=5.0)
    cands = pvp.candidates([m], {m.id: _three_fights()}, duel=_duel_for(plain, [mesure, _res("inconnu"), _res("inconnu")]))
    assert all(c["closing"] for _, c in cands) and not any(c["duel"]["fermeture"] is False for _, c in cands)
    asked = []

    def create(**kw):
        asked.append(kw)
        text = types.SimpleNamespace(type="text", text=_json.dumps({"candidats": []}))
        return types.SimpleNamespace(content=[text], stop_reason="end_turn", model="claude-test")
    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(config, "claude_available", lambda: True)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
    monkeypatch.setattr(vision, "jpeg", lambda *a, **k: b"faux")
    jury.judge(pid, [(m, c) for _, c in cands])
    texts = [b["text"] for b in asked[0]["messages"][0]["content"] if b["type"] == "text" and b["text"].startswith("Candidat")]
    assert "TUE l'adversaire" in texts[0] and not any("TUE" in t for t in texts[1:]), "seule la fin de combat subie est signalée"
    assert "le kill prime sur la vie perdue" in asked[0]["system"]


def _gameplay_mp4(tmp_path):
    import wave
    wav = tmp_path / "hits.wav"
    pcm = (np.clip(_clicks(COMBO_A + COMBO_B), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    src = tmp_path / "gameplay.mp4"
    subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=30:d=12", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


def _quiet_share(path):
    """Part des fenêtres de 0,25 s qui sont presque muettes : sans musique, le son du jeu entre deux coups l'est."""
    pcm = ffmpeg.decode_audio(path)
    n = int(0.25 * SR)
    rms = np.array([np.sqrt(np.mean(pcm[i:i + n] ** 2)) for i in range(0, pcm.size - n, n)])
    return float(np.mean(rms < 0.2 * rms.max()))


def test_edit_pvp_puts_music_on_the_timeline_and_the_export_mixes_it(tmp_path):
    """Même avec « Musique de fond » décochée, l'edit PvP pose un rythme sur la timeline ; l'export le mixe (muet
    seulement si le mixeur coupe la musique)."""
    from pathlib import Path
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Musique PvP"}).json()["id"]
    with _gameplay_mp4(tmp_path).open("rb") as f:
        assert c.post(f"/api/projects/{pid}/media", files=[("files", ("gameplay.mp4", f, "video/mp4"))]).status_code == 200
    j = _wait(c, c.post(f"/api/projects/{pid}/auto-edit", json={"style": "pvp", "music": False}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["music"]["track"] and Path(tl["music"]["track"]).exists() and tl["music"]["volume"] >= 0.3
    # Export sans décor 3D (plus rapide) : seuls la musique et le son du jeu comptent ici.
    tl["texts"], tl["impacts"], tl["sfx"], tl["overlays"] = [], [], [], []
    for x in tl["clips"]:
        x["effects"], x["transition"] = [], "aucune"
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    shares = {}
    for label, mute in (("avec", False), ("coupee", True)):
        tl["mix"]["music"]["mute"] = mute
        assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
        j = _wait(c, c.post(f"/api/projects/{pid}/export", json={"quality": "720p", "fps": 30}).json())
        assert j["status"] == "termine", j
        out = tmp_path / f"{label}.mp4"
        out.write_bytes(c.get(f"/api/projects/{pid}/exports/{j['result']}").content)
        shares[label] = _quiet_share(out)
    assert shares["avec"] < 0.1 and shares["coupee"] > shares["avec"] + 0.2, shares


def test_requested_bursts_are_matched_and_only_those_enter():
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _three_fights()  # A : 20 s (14 coups), B : 60 s (9 coups), C : 100 s (7 coups)
    cands = pvp.candidates([m], {"m1": h})
    keys, lost = pvp.match_requested(cands, [("m1", 58.0, 66.0), ("m1", 30.0, 40.0), ("autre", 0.0, 500.0)])
    assert keys == {("m1", jury.key(c)) for _, c in cands if 59 < c["start"] < 62}
    assert lost == [("m1", 30.0, 40.0), ("autre", 0.0, 500.0)]
    # Un passage qui recouvre un duel entier retient toutes ses rafales.
    allk, _ = pvp.match_requested(cands, [("m1", 0.0, 500.0)])
    assert allk == {("m1", jury.key(c)) for _, c in cands}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), only=keys)
    assert tl.clips and all(55 < x.src_in < 75 and x.src_out < 80 for x in tl.clips), "seule la rafale choisie est montée"
    assert any(r.reason == "Non choisi pour cet edit." for r in tl.rejected)
    assert tl.music.track, "musique posée (générée) même quand les rafales sont imposées"
    none = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), only=set())
    assert not none.clips and "ne correspond à un combo" in none.summary


def test_pvp_fallback_edit_keeps_a_music(rush):
    """Rush sans combo : le montage de repli du bouton « Edit PvP » n'est pas muet, même sans bibliothèque ni case cochée."""
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Repli PvP"}).json()["id"]
    with rush.open("rb") as f:
        assert c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))]).status_code == 200
    j = _wait(c, c.post(f"/api/projects/{pid}/auto-edit", json={"style": "pvp", "music": False}).json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["clips"] and tl["music"]["track"]


# ---------------------------------------------------------------- le kill sans la raclée, plans courts, temps forts

def _avec_vie(cands, vie_a, label, **kw):
    """Résultat du signal local pour la 1re rafale de `cands`, avec une courbe de vie mesurée à 10 i/s (`vie_a(t)` cœurs)."""
    from app.ai import jury
    (mid, c), = cands
    t0 = round(c["start"] - 0.5, 3)
    v = [round(vie_a(t0 + k / 10), 1) for k in range(int((c["end"] + 1.0 - t0) * 10) + 1)]
    r = _res(label, **kw)
    r["vie"] = {"valide": True, "soin": False, "courbe": {"t0": t0, "fps": 10, "v": v}}
    return {(mid, jury.key(c)): r}


def _perte(duel_res, a, b):
    """Chute de vie (pic -> creux, en cœurs) sur [a, b] d'après la courbe mesurée."""
    cb = duel_res["vie"]["courbe"]
    t = cb["t0"] + np.arange(len(cb["v"])) / cb["fps"]
    v = np.array(cb["v"])[(t >= a - 1e-9) & (t <= b + 1e-9)]
    return float((np.maximum.accumulate(v) - v).max())


def _vie_du_kill(t):
    """Le joueur boit une potion, puis encaisse : 4 -> 9 cœurs à 41 s, retombe à 8 puis à 5,5 juste avant le kill ; remis à 10 après."""
    if t < 41.0:
        return 4.0
    if t < 44.0:
        return 9.0
    if t < 44.85:
        return 8.0
    if t < 45.55:
        return 5.5
    return 10.0


def test_a_kill_kept_over_a_beating_shows_only_the_last_blows_never_the_life_drop():
    from app.models import EditSettings
    m = _M()
    h = _train(40, 12)  # 40 s -> 45,5 s : le kill est le dernier coup
    cands = pvp.candidates([m], {"m1": h})
    duel = _avec_vie(cands, _vie_du_kill, "subit", cout=6.0, part=0.8)
    res = next(iter(duel.values()))
    assert _perte(res, 40.0, 45.5) > 3, "la mesure donne bien cette fin de combat pour subie"
    kill = _all_like(cands, "joueur_domine", 9, "kill")
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=kill, duel=duel)
    assert tl.clips, "le kill est gardé (le jury le voit)"
    for x in tl.clips:
        assert _perte(res, x.src_in, x.src_out) <= 1.0, (x.src_in, x.src_out, "ce plan couvre une chute de vie de plus d'un cœur")
    assert min(x.src_in for x in tl.clips) >= 44.5, "seulement les derniers coups (de l'ordre de la dernière seconde), pas le combat"
    ralenti = [x for x in tl.clips if x.speed < 1]
    assert len(ralenti) == 1 and ralenti[0].src_in < 45.5 < ralenti[0].src_out, "le kill reste montré, au ralenti"
    assert "KILL" in [t.text for t in tl.texts]
    assert any("jamais le passage où ta vie chute" in x.reason for x in tl.clips)
    # Sans la courbe (résultat gardé avant son ajout), on ne garde quand même que la dernière seconde.
    cle = next(iter(duel))
    sans = {**res, "vie": {k: v for k, v in res["vie"].items() if k != "courbe"}}
    tl2 = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=kill, duel={cle: sans})
    assert tl2.clips and min(x.src_in for x in tl2.clips) >= 44.4
    # Un kill gardé alors que la mesure dit « donne » n'est pas rogné à sa dernière seconde : c'est un combo qu'on a donné.
    donne = _avec_vie(cands, lambda t: 7.0, "donne", cout=0.0, confirme=True)
    tl3 = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=kill, duel=donne)
    assert min(x.src_in for x in tl3.clips) < 44.5


def test_a_burst_never_covers_a_life_drop_of_more_than_a_heart_even_without_a_kill():
    from app.models import EditSettings
    m = _M()
    h = _train(40, 10)
    cands = pvp.candidates([m], {"m1": h})
    # Une chute de 2 cœurs à 42 s au milieu d'un échange que le signal local admet (coût mesuré : un demi-cœur).
    duel = _avec_vie(cands, lambda t: 9.0 if t < 42.0 else 7.0, "echange", cout=0.5)
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20),
                   verdicts=_all_like(cands, "echange", 6, "rien"), duel=duel)
    res = next(iter(duel.values()))
    assert tl.clips
    assert all(_perte(res, x.src_in, x.src_out) <= 1.0 for x in tl.clips)
    assert min(x.src_in for x in tl.clips) >= 41.9, "le plan commence après la chute"


def test_a_long_combo_becomes_a_short_plan_that_starts_on_a_hit():
    from app.models import EditSettings
    m = _M()
    # 17 coups en 8 s : le milieu est le plus dense (coups à 0,35 s) et le plus fort, les bords sont plus faibles.
    h = [[round(100 + k * 0.5, 3), 0.5] for k in range(6)] + [[round(103 + k * 0.35, 3), 0.9] for k in range(8)] \
        + [[round(105.8 + k * 0.5, 3), 0.5] for k in range(3)]
    cands = pvp.candidates([m], {"m1": h})
    (mid, c), = cands
    assert c["n"] >= 15 and c["end"] - c["start"] > 6
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=_all_like(cands, "joueur_domine", 9, "rien"))
    x = tl.clips[0]
    assert x.duration <= 5.0, f"plan trop long pour la musique : {x.duration:.1f} s"
    beat = 60 / pvp.DEFAULT_BPM
    assert abs(x.duration / beat - round(x.duration / beat)) < 0.02, "toujours un nombre entier de temps"
    premier = min(t for t, _ in h if t >= x.src_in)
    assert premier - x.src_in <= pvp.LEAD_MAX + 1e-6, "le plan démarre sur un coup, pas sur un temps mort"
    plans = tl.clips
    assert sum(p.duration for p in plans) <= pvp.PLAN_MAX + 0.25 + pvp.SPAN_MIN_RALENTI, "main + ralenti : le plafond vaut pour les deux"
    forts = [t for t, s in h if any(p.src_in <= t < p.src_out for p in plans) and s >= 0.9]
    assert len(forts) >= 4 and all(103 <= t <= 105.8 for t in forts), "la portion la plus dense (et la plus forte) du combo est gardée"
    assert "Portion la plus dense du combo" in x.reason
    # Un combo court démarre aussi sur un coup (ses premiers coups cèdent la place au ralenti, plafond oblige).
    court = pvp.build([m], {"m1": _train(100, 6)}, EditSettings(style="pvp"))
    x2 = court.clips[0]
    assert 0 <= min(t for t, _ in _train(100, 6) if t >= x2.src_in - 1e-6) - x2.src_in <= pvp.LEAD_MAX + 1e-6


def test_a_long_combo_prefers_the_part_where_the_camera_sees_no_blow_received():
    from app.models import EditSettings
    m = _M()
    h = _train(100, 17)  # 100 -> 108 s, régulier
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [{**_res("donne", cout=0.0), "fenetre": [100.4, 104.2]}])
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=_all_like(cands, "joueur_domine", 9, "rien"), duel=duel)
    assert min(x.src_in for x in tl.clips) >= 100.2 and max(x.src_out for x in tl.clips) <= 105.6,         "la portion reste dans la fenêtre sans coup reçu (plus le temps de voir le coup final)"


def test_the_music_peak_lands_on_the_kill_and_the_full_stack_serves_only_one_moment(monkeypatch):
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 12) + _train(100, 8) + _train(140, 8)
    cands = pvp.candidates([m], {"m1": h})
    notes = [6, 9, 7, 8]  # le meilleur combo : 60 s (12 coups) ; le kill : 100 s
    verdicts = {("m1", jury.key(c)): {"verdict": "joueur_domine", "fin": "kill" if i == 2 else "rien", "note": n, "raison": "",
                                      "n": c["n"]} for i, ((_, c), n) in enumerate(zip(cands, notes))}
    beat = 60 / 120
    monkeypatch.setattr(pvp, "music_grid", lambda path: (120.0, 0.25))
    monkeypatch.setattr(pvp, "_music_peak", lambda path: (30.25, 200.0))
    s = EditSettings(style="pvp", target_duration=60)
    tl = pvp.build([m], {"m1": h}, s, "x", "morceau.mp3", verdicts=verdicts)

    def kind(k):
        return [i for i in tl.impacts if i.kind == k]
    # La pile complète (ralenti x0,4 + contraste, secousse franche, étincelles, flash, onde, zoom, boom) : une seule fois, sur le kill.
    kill_at = kind("flash")[0].start
    assert len(kind("flash")) == len(kind("onde")) == len(kind("zoom")) == 1
    assert len([x for x in tl.sfx if x.kind == "boom"]) == 1
    assert [t for t in tl.texts if t.text == "KILL"][0].start == pytest.approx(kill_at, abs=0.01)
    assert all(abs(i.start - kill_at) < 0.01 for i in kind("onde") + kind("zoom"))
    lents = [x for x in tl.clips if x.speed < 1]
    assert sorted(x.speed for x in lents) == [pvp.SLOW, pvp.SLOW_LEGER], "un ralenti plein, un ralenti nettement plus doux"
    plein = next(x for x in lents if x.speed == pvp.SLOW)
    doux = next(x for x in lents if x.speed == pvp.SLOW_LEGER)
    assert "contraste" in plein.effects and "contraste" not in doux.effects
    assert tl.clips[-1] is plein, "le moment fort ferme l'edit"
    etincelles = sorted(i.intensity for i in kind("etincelles"))
    assert len(etincelles) == 2 and etincelles[0] <= 0.8 < 1.5 <= etincelles[1], "l'autre temps fort a une variante légère"
    finales = sorted(i.intensity for i in kind("secousse") if i.intensity >= 1.0)
    assert finales[-1] >= 1.5 and finales.count(finales[-1]) == 1, "une seule secousse franche"
    # Le pic de la musique (30,25 s) tombe sur le kill, pas sur une coupe quelconque.
    assert tl.music.start > 0.25 and "Pic de la musique posé sur le kill" in tl.summary
    assert round(30.25 - tl.music.start, 3) == pytest.approx(kill_at, abs=0.02)
    assert abs((tl.music.start - 0.25) / beat - round((tl.music.start - 0.25) / beat)) < 0.02, "la musique part toujours sur un temps"
    assert abs(kill_at / beat - round(kill_at / beat)) < 0.02, "le coup final du kill tombe sur un temps de la musique"
    # La fin ne traîne pas : après un kill l'adversaire disparaît, le ralenti ne garde que `HOLD_KILL` secondes de rush
    # (à un temps près), et le montage s'arrête peu après la carte « KILL ».
    coup_final = max(t for t, _ in h if 100 <= t < 110)
    assert 0 < plein.src_out - coup_final <= pvp.HOLD_KILL + beat * pvp.SLOW / 2 + 1e-6
    carte = [t for t in tl.texts if t.text == "KILL"][0]
    assert 0 <= tl.duration - (carte.start + carte.duration) <= beat + 1e-6
    # Sans kill, la règle d'avant reste : le pic sur une coupe, la pile complète sur le meilleur combo.
    sans = {k: {**v, "fin": "rien"} for k, v in verdicts.items()}
    tl2 = pvp.build([m], {"m1": h}, s, "x", "morceau.mp3", verdicts=sans)
    # ... et le coup final d'un combo, lui, garde le temps de voir l'adversaire reculer.
    assert tl2.clips[-1].src_out - max(t for t, _ in h if 60 <= t < 70) > pvp.HOLD_KILL + beat * pvp.SLOW / 2
    assert "Pic de la musique posé sur la coupe" in tl2.summary and [x.speed for x in tl2.clips if x.speed < 1] == [pvp.SLOW]
    assert [i.kind for i in tl2.impacts].count("flash") == 1


def test_combo_card_counts_the_blows_shown_in_its_plan_and_stays_short():
    from app.ai import jury
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 16) + _train(100, 8)  # le meilleur combo (16 coups, 7,5 s) est rogné : le compteur doit suivre
    cands = pvp.candidates([m], {"m1": h})
    notes = [6, 9, 7]
    verdicts = {("m1", jury.key(c)): {"verdict": "joueur_domine", "fin": "rien", "note": n, "raison": "", "n": c["n"]}
                for (_, c), n in zip(cands, notes)}
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=verdicts)
    carte = next(t for t in tl.texts if t.text.startswith("COMBO x"))
    assert carte.duration <= 1.6 + 1e-6, "la carte ne reste pas plus de 1,6 s"
    plans = [x for x in tl.clips if 55 < x.src_in < 70]
    montres = [t for t, _ in h if 59 <= t <= 68 and any(x.src_in <= t < x.src_out for x in plans)]
    assert carte.text == f"COMBO x{len(montres)}" and len(montres) < 16, (carte.text, len(montres))
    assert carte.start + carte.duration <= tl.duration + 1e-6
    assert carte.start >= tl.duration - plans[-1].duration - 1.0, "la carte arrive sur les derniers coups du plan"


# ---------------------------------------------------------------- relecture : bornes, tempo, textes, plafond

def _duree_en_temps(clips, beat):
    return [c.duration / beat for c in clips]


def _entiers(clips, beat, tol=0.03):
    return all(abs(d - round(d)) < tol for d in _duree_en_temps(clips, beat))


def _avis(cands, fins_notes):
    """Verdicts « joueur domine » avec la fin et la note voulues pour chaque rafale, dans l'ordre."""
    from app.ai import jury
    return {(mid, jury.key(c)): {"verdict": "joueur_domine", "fin": f, "note": n, "raison": "", "n": c["n"]}
            for (mid, c), (f, n) in zip(cands, fins_notes)}


def test_a_long_combo_stops_with_its_calm_window_and_the_card_counts_every_visible_blow():
    from app.models import EditSettings
    m = _M()
    h = _train(100, 17)  # 100 -> 108 s ; la caméra voit un coup reçu à 104,2 s qui ferme la fenêtre calme [100,4 ; 104,2]
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [{**_res("donne", cout=0.0), "fenetre": [100.4, 104.2]}])
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp"), verdicts=_all_like(cands, "joueur_domine", 9, "rien"), duel=duel)
    limite = 104.2 + 0.35  # l'impulsion qui ferme la fenêtre, plus le décalage de son avec le son
    assert max(x.src_out for x in tl.clips) <= limite + 1e-3, "ni le plan ni son ralenti ne passent le coup reçu qui ferme la fenêtre"
    visibles = [t for t, _ in h if any(x.src_in - 1e-6 <= t < x.src_out for x in tl.clips)]
    carte = next(t for t in tl.texts if t.text.startswith("COMBO x"))
    assert carte.text == f"COMBO x{len(visibles)}", (carte.text, visibles)


def test_texts_never_run_past_their_own_plan_whatever_the_tempo(monkeypatch):
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 12) + _train(100, 8) + _train(140, 9)
    cands = pvp.candidates([m], {"m1": h})
    # Le meilleur combo (note 9) n'est pas un kill : le kill (note plus basse) est le moment fort, le meilleur combo a le ralenti léger.
    v = _avis(cands, [("rien", 6), ("rien", 9), ("kill", 6), ("rien", 5)])
    for bpm in (70, 90, 100, 128, 182):
        monkeypatch.setattr(pvp, "music_grid", lambda path, b=bpm: (float(b), 0.0))
        tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=v)
        lents, t = [], 0.0
        for x in tl.clips:
            if x.speed < 1:
                lents.append((t, t + x.duration))
            t += x.duration
        cartes = tl.texts[1:]  # le titre d'ouverture n'est pas rattaché à un ralenti
        for texte in cartes:
            fin_plan = min(e for s, e in lents if e > texte.start)
            assert texte.start + texte.duration <= fin_plan + 1e-3, (bpm, texte.text, texte.start + texte.duration, fin_plan)
        assert "KILL" in [x.text for x in cartes] and any(x.text.startswith("COMBO x") for x in cartes), bpm


def test_a_plan_stays_on_the_beat_when_the_player_is_hit_right_after_the_last_blow():
    from app.models import EditSettings
    m = _M()
    h = _train(100, 5)  # le coup final est à 102,0 s
    cands = pvp.candidates([m], {"m1": h})
    duel = _avec_vie(cands, lambda t: 10.0 if t < 102.15 else 8.0, "echange", cout=0.5)  # deux cœurs perdus 0,15 s après le coup final
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=_all_like(cands, "echange", 6, "rien"), duel=duel)
    beat = 60 / pvp.DEFAULT_BPM
    assert _entiers(tl.clips, beat), _duree_en_temps(tl.clips, beat)
    res = next(iter(duel.values()))
    assert all(_perte(res, x.src_in, x.src_out) <= 1.0 for x in tl.clips)
    ralenti = [x for x in tl.clips if x.speed < 1]
    assert len(ralenti) == 1 and ralenti[0].src_in < 102.0 < ralenti[0].src_out, "le coup final reste montré, au ralenti"
    # Le repli de `_caler_fin` (aucune place pour allonger) reste lui aussi sur un nombre entier de temps.
    s0, out = pvp._caler_fin(100.0, 101.3, 100.0, 101.35, beat, 101.25)
    assert abs((out - s0) / beat - round((out - s0) / beat)) < 1e-6 and s0 <= 101.25 <= out


def test_a_plan_with_slow_motion_stays_within_the_plan_ceiling_for_a_combo_and_for_a_kill():
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 17, gap=0.3) + _train(100, 8)  # le meilleur combo : 17 coups à 0,3 s
    cands = pvp.candidates([m], {"m1": h})
    duel = _duel_for(cands, [_res("donne", cout=0.0, confirme=True)] * 3)
    for fin in ("rien", "kill"):
        v = _avis(cands, [("rien", 6), (fin, 9), ("rien", 7)])
        tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=30), "x", verdicts=v, duel=duel)
        plan = [x for x in tl.clips[1:] if 55 < x.src_in < 70]  # sans l'aperçu en accroche, qui est un autre plan
        assert any(x.speed < 1 for x in plan), fin
        assert sum(x.duration for x in plan) <= 5.0, (fin, [round(x.duration, 2) for x in plan])


def test_a_kill_with_no_proof_that_the_player_gives_it_shows_only_its_last_second(monkeypatch):
    from app.models import EditSettings
    m = _M()
    h = _train(40, 12)  # le kill est le dernier coup, à 45,5 s
    cands = pvp.candidates([m], {"m1": h})
    kill = _all_like(cands, "joueur_domine", 9, "kill")
    for bpm in (60, 128):
        monkeypatch.setattr(pvp, "music_grid", lambda path, b=bpm: (float(b), 0.0))
        for duel in (None, _duel_for(cands, [_res("inconnu")])):
            tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=kill, duel=duel)
            assert min(x.src_in for x in tl.clips) >= 45.5 - pvp.KILL_LEAD - 0.06, (bpm, [x.src_in for x in tl.clips])
            assert any(x.speed < 1 for x in tl.clips) and "KILL" in [t.text for t in tl.texts]
        # Une mesure qui dit « donne » (sans courbe) est une preuve : le combo n'est pas rogné.
        donne = _duel_for(cands, [_res("donne", cout=0.0, confirme=True)])
        tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20), verdicts=kill, duel=donne)
        assert min(x.src_in for x in tl.clips) < 45.5 - pvp.KILL_LEAD - 0.5


def test_the_teaser_of_an_unproven_kill_does_not_show_more_than_its_last_second(monkeypatch):
    from app.models import EditSettings
    m = _M()
    h = _train(20, 8) + _train(60, 8) + _train(100, 8) + _train(140, 8)
    cands = pvp.candidates([m], {"m1": h})
    v = _avis(cands, [("rien", 8), ("rien", 8), ("kill", 9), ("rien", 8)])  # le kill : 100 -> 103,5 s, sans courbe de vie ni mesure
    for bpm in (60, 128):
        monkeypatch.setattr(pvp, "music_grid", lambda path, b=bpm: (float(b), 0.0))
        tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=60), "x", verdicts=v)
        teaser = tl.clips[0]
        assert 100 <= teaser.src_in and teaser.src_out <= 106
        assert teaser.src_in >= 103.5 - pvp.KILL_LEAD - 0.06, (bpm, teaser.src_in)
        # ... ni l'arène vide d'après : il s'arrête où s'arrêtera le ralenti final, à moins qu'il lui faille ses deux temps.
        assert teaser.src_out <= max(103.5 + pvp.HOLD_KILL, teaser.src_in + 2 * 60 / bpm) + 0.06, (bpm, teaser.src_out)


def test_a_kill_whose_life_drops_right_before_the_last_blow_still_gets_its_slow_motion():
    from app.models import EditSettings
    m = _M()
    h = _train(40, 8)  # le kill est à 43,5 s
    cands = pvp.candidates([m], {"m1": h})
    duel = _avec_vie(cands, lambda t: 10.0 if t < 43.2 else 7.5, "echange", cout=0.5)  # la vie chute 0,3 s avant le coup final
    tl = pvp.build([m], {"m1": h}, EditSettings(style="pvp", target_duration=20),
                   verdicts=_all_like(cands, "joueur_domine", 9, "kill"), duel=duel)
    res = next(iter(duel.values()))
    assert tl.clips and all(_perte(res, x.src_in, x.src_out) <= 1.0 for x in tl.clips), "jamais la chute"
    ralenti = [x for x in tl.clips if x.speed < 1]
    assert len(ralenti) == 1 and ralenti[0].src_in < 43.5 < ralenti[0].src_out, "le kill reste montré, au ralenti, même sur un seul coup"
    assert "KILL" in [t.text for t in tl.texts]
