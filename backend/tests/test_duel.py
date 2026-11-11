"""Signal local « qui porte le combo ? » : lecture de la barre de vie, roulis de la caméra, verdict et cache.

Les images sont dessinées en numpy : une barre de cœurs à l'échelle 2 en 1920x1080, comme dans le jeu."""
import subprocess
import types

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import storage
from app.analysis import duel
from app.main import app
from app.media.ffmpeg import FFMPEG

W, H, S = 1920, 1080, 2
X0, Y0 = int(W / 2 - 91 * S), int(H - 39 * S)  # coin de la barre de cœurs, comme dans le jeu


def _fond(seed=1):
    """Fond doux à gros motifs : de quoi suivre la caméra, rien de rouge."""
    rng = np.random.default_rng(seed)
    petit = rng.integers(60, 120, (27, 48, 3), dtype=np.uint8)
    return cv2.resize(petit, (W, H), interpolation=cv2.INTER_CUBIC)


def _image(coeurs: float, fond: np.ndarray, rouge=(0, 0, 200)) -> np.ndarray:
    """Image du jeu avec `coeurs` cœurs pleins (demi-cœurs compris) sur dix."""
    img = fond.copy()
    for i in range(10):
        xa = X0 + 8 * S * i
        ya = Y0
        img[ya:ya + 9 * S, xa:xa + 9 * S] = (25, 25, 25)  # cadre du sprite
        y1, y2 = ya + int(1.5 * S), ya + int(7 * S)
        a, b, c = xa + int(1.5 * S), xa + int(4.5 * S), xa + int(7.5 * S)
        img[y1:y2, a:c] = (40, 40, 40)  # cœur vide
        if coeurs >= i + 0.5:
            img[y1:y2, a:b] = rouge
        if coeurs >= i + 1:
            img[y1:y2, b:c] = rouge
    return img


# ---------------------------------------------------------------- lecture de la barre de vie

def test_hearts_bar_is_found_and_read():
    fond = _fond()
    lay = duel._layout_image(_image(10, fond))
    assert lay is not None and lay.s == 2
    assert abs(lay.x0 - X0) <= 2 and abs(lay.y0 - Y0) <= 2, "position détectée, pas codée en dur"
    cw, ch, x, y = lay.crop()
    for n in (10, 7.5, 3, 0.5):
        img = _image(n, fond)[y:y + ch, x:x + cw]
        assert duel.lire_vie(img[None], lay)[0] == pytest.approx(n), n
    # Les cœurs cyan du pack de textures du joueur se lisent aussi.
    cyan = _image(6.5, fond, rouge=(160, 168, 5))[y:y + ch, x:x + cw]
    assert duel.lire_vie(cyan[None], lay)[0] == pytest.approx(6.5)
    # Un flash blanc ou une scène sans barre : illisible (nan), jamais une vie inventée.
    blanc = np.full((ch, cw, 3), 255, np.uint8)
    assert np.isnan(duel.lire_vie(blanc[None], lay)[0])
    assert duel._layout_image(fond) is None, "pas de cœurs : pas de barre"


def test_life_verdict_rules():
    d = duel.descripteurs
    plat = np.array([7.0] * 20)
    chute = np.concatenate([[10.0] * 5, np.linspace(10, 4, 8), [4.0] * 7])
    assert duel.verdict_vie(d(plat), False) == "donne"
    assert duel.verdict_vie(d(chute), False) == "subit", "6 cœurs perdus"
    assert duel.verdict_vie(d(np.array([8.0] * 10 + [6.5] * 10)), False) == "echange"
    assert duel.verdict_vie(d(np.array([8.0] * 10 + [7.5] * 10)), False) == "donne", "un demi-cœur : bruit de lecture"
    assert duel.verdict_vie(d(np.array([8.0] * 10 + [7.0] * 10)), False) == "echange", "un cœur perdu : un coup reçu"
    assert duel.verdict_vie(d(np.array([10.0] * 20)), False) == "inconnu", "vie pleine : mode sans dégâts, on ne sait pas"
    assert duel.verdict_vie(d(plat), True) == "inconnu", "aucun dégât autour : on ne sait pas"
    assert duel.verdict_vie(d(np.array([2.5] * 8 + [0.5] * 12)), False) == "subit", "finit presque mort"
    assert duel.verdict_vie(d(np.full(20, np.nan)), False) == "inconnu", "illisible : jamais « donne »"
    assert duel.verdict_vie(d(np.array([7.0, 7.0])), False) == "inconnu", "trop peu d'images"
    soin = np.concatenate([[3.0] * 6, [8.0] * 14])
    # Une potion ne compte pas comme une perte, mais elle dit que le joueur était en danger : jamais « donne ».
    assert d(soin)["soin"] and d(soin)["perte"] == 0 and duel.verdict_vie(d(soin), False) == "echange"


# ---------------------------------------------------------------- roulis de la caméra

def _serie(n=60, pics=()):
    """Roulis par pas de 1/12 s : 0, et une impulsion (+14 puis -14) à chaque indice de `pics`."""
    s = [(0.0, 120)] * n
    for i in pics:
        s[i] = (14.0, 120)
        s[i + 1] = (-14.0, 120)
    return s


def test_camera_tilt_is_a_hit_received():
    hits = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5]
    t0 = 8.0
    # une impulsion juste avant chaque coup (le son suit l'image de ~0,15 s)
    recus = _serie(pics=[int((h - 0.15 - t0) * duel.FPS_ROULIS) for h in hits])
    r = duel.verdict_roulis(10.0, 12.5, hits, recus, t0)
    assert r["verdict"] == "subit" and r["recus"] == len(hits)
    calme = duel.verdict_roulis(10.0, 12.5, hits, _serie(), t0)
    assert calme["verdict"] == "donne" and calme["recus"] == 0 and calme["fenetre"] is not None
    # Suivi mauvais (aucun point suivi) : on ne tranche jamais « donne ».
    aveugle = duel.verdict_roulis(10.0, 12.5, hits, [(0.0, 0)] * 60, t0)
    assert aveugle["verdict"] == "inconnu"
    # Une caméra retournée (angle aberrant) interdit « donne ».
    fou = _serie()
    fou[20] = (80.0, 120)
    assert duel.verdict_roulis(10.0, 12.5, hits, fou, t0)["verdict"] != "donne"
    # Trois coups reçus sur six : ni donné ni subi.
    mi = _serie(pics=[int((h - 0.15 - t0) * duel.FPS_ROULIS) for h in hits[::2]])
    assert duel.verdict_roulis(10.0, 12.5, hits, mi, t0)["verdict"] == "echange"


def test_roll_is_measured_on_real_images():
    fond = cv2.cvtColor(_fond(3), cv2.COLOR_BGR2GRAY)
    petit = cv2.resize(fond, (duel.W, duel.H))
    cadres = []
    for ang in (0, 0, 14, 0, 0):
        M = cv2.getRotationMatrix2D((duel.W / 2, duel.H / 2), ang, 1.0)
        cadres.append(cv2.cvtColor(cv2.warpAffine(petit, M, (duel.W, duel.H), borderMode=cv2.BORDER_REFLECT), cv2.COLOR_GRAY2BGR))
    angles = [a for a, _ in duel.roulis(np.array(cadres))]
    assert max(abs(a) for a in angles) == pytest.approx(14, abs=2.5)
    assert abs(angles[0]) < 1 and abs(angles[3]) < 1 and angles[1] * angles[2] < 0  # le retour est de signe opposé


# ---------------------------------------------------------------- verdict combiné et règles de sélection

def _vie(verdict, perte=0.0, debut=8.0, fin=8.0, mini=8.0, valide=True, soin=False):
    return {"verdict": verdict, "valide": valide, "perte": perte, "debut": debut, "fin": fin, "min": mini, "soin": soin}


def _cam(verdict, coups=8, recus=0, fiable=0.99, fenetre=None):
    return {"verdict": verdict, "coups": coups, "recus": recus, "impulsions": recus, "fiable": fiable, "fenetre": fenetre}


def test_subit_is_a_veto_from_either_measure():
    assert duel.classer(_vie("subit", 5, 9, 4, 4), _cam("donne", fenetre=[1, 3]))["label"] == "subit"
    assert duel.classer(_vie("donne"), _cam("subit", recus=8))["label"] == "subit"
    assert duel.classer(None, _cam("subit", recus=8))["label"] == "subit"
    r = duel.classer(_vie("subit", 5, 9, 4, 4), None)
    assert not duel.admissible(r) and "tu subissais" in duel.pourquoi(r)


def test_giving_needs_proof_and_never_comes_from_doubt():
    assert duel.classer(_vie("donne"), _cam("donne", fenetre=[1, 3]))["confirme"] is True
    assert duel.classer(_vie("donne"), _cam("echange", recus=2))["label"] == "donne"
    assert duel.classer(_vie("inconnu", mini=10), _cam("donne", fenetre=[1, 3]))["label"] == "donne", "Boxing : la caméra seule"
    assert duel.classer(_vie("echange", 1.5), _cam("donne", fenetre=[1, 3]))["label"] == "echange", "la vie baisse : pas « donne »"
    assert duel.classer(None, None)["label"] == "inconnu"
    assert duel.classer(_vie("inconnu", valide=False), _cam("inconnu"))["label"] == "inconnu"
    # La vie tient mais la caméra voit surtout des coups reçus (coups de côté) : plus « donne ».
    assert duel.classer(_vie("donne"), _cam("echange", coups=8, recus=6))["label"] == "echange"
    # La moitié des coups reçus suffit : jamais « donne » à 0,5 ou au-dessus (0,57 laissait passer un combo à moitié subi).
    assert duel.classer(_vie("donne"), _cam("echange", coups=8, recus=4))["label"] == "echange"
    assert duel.classer(_vie("donne"), _cam("echange", coups=7, recus=4))["label"] == "echange"
    assert duel.classer(_vie("donne"), _cam("echange", coups=10, recus=4))["label"] == "donne"


def test_costly_exchange_is_not_admitted():
    cheap = duel.classer(_vie("echange", 0.5, 8, 7.5, 7.5), _cam("echange", coups=8, recus=3))
    dear = duel.classer(_vie("echange", 1.5, 8, 6.5, 6.5), _cam("echange", coups=8, recus=3))
    assert cheap["label"] == "echange" and duel.admissible(cheap)
    assert dear["label"] == "echange" and not duel.admissible(dear), "un cœur et demi perdu : on t'a touché"
    assert "tu encaissais" in duel.pourquoi(dear)
    boxing = duel.classer(_vie("inconnu", mini=10), _cam("echange", coups=10, recus=7))
    assert boxing["cout"] is None and not duel.admissible(boxing), "sans vie à lire, trop de coups reçus coûtent"
    moitie = duel.classer(_vie("inconnu", mini=10), _cam("echange", coups=10, recus=5))
    assert not duel.admissible(moitie), "la moitié des coups reçus : écarté (le seuil est atteint, pas dépassé)"
    assert duel.admissible(duel.classer(_vie("inconnu", mini=10), _cam("echange", coups=10, recus=4)))
    assert duel.admissible(None) and duel.admissible(duel.inconnu("rien")), "non mesuré : on ne bloque rien"
    # Une potion de soin dans la fenêtre : le joueur était en danger, ce n'est pas un bon moment même à faible coût.
    potion = duel.classer(_vie("echange", 0.5, 4, 8, 4, soin=True), _cam("echange", coups=8, recus=3))
    assert potion["label"] == "echange" and not duel.admissible(potion)


def test_preference_order_and_balance():
    donne2 = duel.classer(_vie("donne"), _cam("donne", fenetre=[1, 3]))
    donne1 = duel.classer(_vie("donne"), _cam("echange", recus=2))
    ech = duel.classer(_vie("echange", 0.5), _cam("echange", recus=3))
    assert [duel.rang(x) for x in (donne2, donne1, ech, duel.inconnu("x"), None)] == [0, 1, 2, 3, 3]
    subit = duel.classer(_vie("subit", 5), None)
    cands = [("m", {"duel": x}) for x in (donne2, donne1, ech, duel.inconnu("x"), subit)] + [("m", {})]
    assert duel.bilan(cands) == {"donne": 2, "echange": 1, "ecartees": 1, "inconnu": 2, "subies": 1}


def test_cases_seen_on_the_real_rush_after_checking_the_frames():
    """Mesures relevées sur le vrai rush (14 min), planche en main. Les montages de preuve ont laissé passer cinq plans que
    trois contrôleurs ne jugeaient pas « le joueur enchaîne » : un cœur perdu, une potion de soin dans la fenêtre, ou
    la moitié des coups reçus. Les trois combos que les contrôleurs validaient restent."""
    def cas(debut, fin, mini, perte, soin, coups, recus, cam="echange", sans_degats=False):
        d = {"valide": True, "debut": debut, "fin": fin, "min": mini, "perte": perte, "soin": soin}
        vie = {**d, "verdict": duel.verdict_vie(d, sans_degats), "sans_degats": sans_degats}
        return duel.classer(vie, _cam(cam, coups=coups, recus=recus, fenetre=[1, 3] if cam == "donne" else None))

    ecartes = {
        "plan 3 (5:48.9) : un cœur perdu en continu": cas(8, 7.5, 7.0, 1.0, False, 4, 1, "donne"),
        "plan 4 (0:39.8) : cœur et demi perdu, buadier touche aussi": cas(7.5, 6.5, 6.5, 1.5, False, 5, 1),
        "plan 5 (3:02.2) : 3 cœurs puis potion de soin": cas(3.0, 6.5, 2.5, 0.5, True, 4, 0),
        "plan 8 (8:34.9) : potion au début, cœur et demi perdu": cas(3.5, 8.5, 3.5, 1.5, True, 6, 2),
        "plan 10 (13:33.9) : potion, un cœur perdu, 4 coups reçus sur 7": cas(4.0, 9.0, 4.0, 1.0, True, 7, 4),
        "3:47.8 : Boxing, 3 coups reçus sur 5": cas(10, 10, 10, 0.0, False, 5, 3, sans_degats=True),
    }
    for nom, r in ecartes.items():
        assert not duel.admissible(r), (nom, r["label"], r["cout"], r["part_recue"])
    gardes = {
        "12:22.8 : vie 7,5 stable": cas(7.5, 7.5, 7.5, 0.0, False, 4, 1),
        "1:50.5 : un demi-cœur, vue qui ne penche pas": cas(3.5, 3.0, 3.0, 0.5, False, 5, 1, "donne"),
        "4:20.2 : Boxing, 17 coups dont 8 reçus (0,47)": cas(10, 10, 10, 0.0, False, 17, 8, "donne", sans_degats=True),
    }
    for nom, r in gardes.items():
        assert r["label"] == "donne" and duel.admissible(r), (nom, r["label"])


def test_last_burst_of_a_fight_is_judged_not_vetoed():
    """Le duel gagné : la vie a chuté pendant le combat, mais la dernière rafale est le kill. Elle n'est pas écartée
    d'office : le jury (qui voit l'issue) tranche. Une rafale subie au milieu d'un combat reste écartée."""
    subit = duel.classer(_vie("subit", 5, 3, 10, 2), _cam("subit", coups=9, recus=7))
    milieu = {**subit, "fermeture": False}
    fin = {**subit, "fermeture": True}
    assert not duel.admissible(milieu) and not duel.a_confirmer(milieu)
    assert duel.admissible(fin) and duel.a_confirmer(fin) and duel.rang(fin) == 2
    assert duel.label(fin).startswith("tu subis"), "le verdict mesuré reste honnête"
    assert duel.bilan([("m", {"duel": fin}), ("m", {"duel": milieu})])["ecartees"] == 2, "à confirmer = pas encore gardée"


def test_stale_or_odd_cache_never_crashes():
    assert duel.rafales_de(None) == {} and duel.rafales_de({}) == {}
    assert duel.rafales_de({"duel": {"v": duel.VERSION - 1, "rafales": {"1.00-2.00": {"label": "donne"}}}}) == {}
    assert duel.rafales_de({"duel": {"v": duel.VERSION, "rafales": {"a": "texte", "b": {"label": "?"}, "c": {"label": "subit"}}}}) \
        == {"c": {"label": "subit"}}
    assert duel.label({"label": "inconnu-futur", "raison": "x"}).startswith("non tranché")
    assert duel.label({"label": "subit"}).startswith("tu subis")


# ---------------------------------------------------------------- de bout en bout sur une vidéo dessinée

def _video(tmp_path, nom, vie_a):
    """6 s à 10 i/s, 1920x1080 : `vie_a(t)` donne la vie à chaque instant. Une image clé tous les 5 images."""
    fond = _fond()
    out = tmp_path / nom
    p = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", f"{W}x{H}", "-r", "10", "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
                          "-pix_fmt", "yuv420p", "-g", "5", str(out)], stdin=subprocess.PIPE)
    for k in range(60):
        p.stdin.write(_image(vie_a(k / 10), fond).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return out


def _media(mid, path, duree=6.0):
    return types.SimpleNamespace(id=mid, filename=path.name, path=str(path), proxy=None, width=W, height=H, duration=duree,
                                 has_video=True, has_audio=True)


def _burst():
    hits = [[round(2.0 + 0.5 * k, 2), 0.8] for k in range(5)]
    return {"start": 2.0, "end": 4.0, "hits": hits, "n": 5, "score": 5.0}


def test_analysis_on_drawn_video_then_cache(tmp_path, monkeypatch):
    pid = TestClient(app).post("/api/projects", json={"name": "Duel"}).json()["id"]
    c = _burst()
    k = f"{c['start']:.2f}-{c['end']:.2f}"
    tient = _media("m_tient", _video(tmp_path, "tient.mp4", lambda t: 7))
    subit = _media("m_subit", _video(tmp_path, "subit.mp4", lambda t: 10 if t < 2 else max(4.0, 10 - 4 * (t - 2))))
    pas_de_barre = _media("m_nu", _video(tmp_path, "nu.mp4", lambda t: 0))

    got = duel.analyse(pid, [tient, subit, pas_de_barre], [(m.id, c) for m in (tient, subit, pas_de_barre)])
    assert got[("m_tient", k)]["label"] == "donne" and duel.admissible(got[("m_tient", k)])
    assert got[("m_tient", k)]["vie"]["verdict"] == "donne"
    assert got[("m_subit", k)]["label"] == "subit" and not duel.admissible(got[("m_subit", k)])
    assert got[("m_subit", k)]["cout"] >= 3 and "vie" in got[("m_subit", k)]["raison"]
    # Aucune barre de vie à l'image (interface masquée, autre jeu) : rien ne casse, le verdict n'est jamais « donne ».
    nu = got[("m_nu", k)]
    assert nu["label"] == "inconnu" and nu["vie"] is None and "introuvable" in nu["raison"], "sans barre : jamais donne ni echange"
    assert duel.admissible(nu) and duel.rang(nu) == 3

    # Gardé avec les signaux du rush ; un second passage ne relit aucune image.
    sig = storage.load_signals(pid, "m_subit")
    assert sig["duel"]["v"] == duel.VERSION and k in sig["duel"]["rafales"] and abs(sig["duel"]["interface"]["s"] - 2) < 0.2
    assert storage.load_signals(pid, "m_nu")["duel"]["interface"] is None, "l'absence de barre est gardée aussi"
    assert duel.cached(pid, "m_subit")[k]["label"] == "subit"
    assert duel.plages_subies(pid, "m_subit") == [(2.0, 4.0)] and duel.plages_subies(pid, "m_tient") == []
    # La zone de vie donnée au jury vient de l'interface détectée (position et échelle), pas d'une estimation.
    lay = duel.Layout(**sig["duel"]["interface"])
    zone = duel.zone_vie(pid, subit)
    assert zone == lay.zone_jury() and abs(zone[0] * W - (X0 - 4 * S)) <= 4 and abs(zone[1] * H - (Y0 - 14 * S)) <= 4
    assert duel.zone_vie(pid, pas_de_barre) is None, "barre cherchée sans être trouvée : pas de zone"

    def boom(*a, **kw):
        raise AssertionError("image relue malgré le cache")
    for nom in ("_lire_flux", "mesure_roulis", "detecter_layout", "_contexte"):
        monkeypatch.setattr(duel, nom, boom)
    again = duel.analyse(pid, [tient, subit, pas_de_barre], [(m.id, c) for m in (tient, subit, pas_de_barre)])
    assert again == got
    # Version changée : on recalcule (l'ancien résultat n'est pas relu) et la zone de vie redevient une estimation.
    monkeypatch.setattr(duel, "VERSION", duel.VERSION + 1)
    assert duel.cached(pid, "m_subit") == {}
    estimee = duel.Layout(W, H, 2, W / 2 - 91 * 2, H - 39 * 2, 0.0).zone_jury()
    assert duel.zone_vie(pid, subit) == estimee


def test_failures_are_never_cached(tmp_path, monkeypatch):
    """Une panne ponctuelle (lecture, ffmpeg) donne « inconnu » pour cette fois, sans rien graver : l'analyse suivante
    réessaie. Seule l'absence de barre, constatée pour de bon, est gardée."""
    pid = TestClient(app).post("/api/projects", json={"name": "Duel panne"}).json()["id"]
    c = _burst()
    k = f"{c['start']:.2f}-{c['end']:.2f}"
    m = _media("m_panne", _video(tmp_path, "panne.mp4", lambda t: 7))

    vrai_layout = duel.detecter_layout

    def casse(*a, **kw):
        raise RuntimeError("ffmpeg a planté")
    monkeypatch.setattr(duel, "detecter_layout", casse)
    got = duel.analyse(pid, [m], [(m.id, c)])
    assert got[(m.id, k)]["label"] == "inconnu" and got[(m.id, k)]["echec"]
    sig = storage.load_signals(pid, m.id) or {}
    assert "interface" not in (sig.get("duel") or {}) and k not in duel.cached(pid, m.id), "rien gravé"

    monkeypatch.setattr(duel, "detecter_layout", vrai_layout)
    got = duel.analyse(pid, [m], [(m.id, c)])
    assert got[(m.id, k)]["label"] == "donne" and "echec" not in got[(m.id, k)], "réessayé et réussi"
    assert duel.cached(pid, m.id)[k]["label"] == "donne"

    # Un échec de lecture sur une seule rafale : pas gardé non plus.
    c2 = {"start": 4.0, "end": 5.0, "hits": [[4.0, 0.5], [4.5, 0.5]], "n": 2, "score": 1.0}
    k2 = f"{c2['start']:.2f}-{c2['end']:.2f}"
    vrai = duel.mesure_roulis
    monkeypatch.setattr(duel, "mesure_roulis", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("lecture")))
    got = duel.analyse(pid, [m], [(m.id, c), (m.id, c2)])
    assert got[(m.id, k2)]["echec"] and k2 not in duel.cached(pid, m.id) and k in duel.cached(pid, m.id)
    monkeypatch.setattr(duel, "mesure_roulis", vrai)
    assert "echec" not in duel.analyse(pid, [m], [(m.id, c2)])[(m.id, k2)] and k2 in duel.cached(pid, m.id)


def test_only_asked_bursts_are_measured_one_step_each(tmp_path):
    """Un edit qui vise quelques rafales ne mesure que celles-là ; chaque rafale mesurée met la tâche à jour (le délai
    du chat ne court pas pendant toute l'analyse d'un rush)."""
    pid = TestClient(app).post("/api/projects", json={"name": "Duel ciblé"}).json()["id"]
    m = _media("m_cible", _video(tmp_path, "cible.mp4", lambda t: 7))
    a = _burst()
    b = {"start": 4.0, "end": 5.0, "hits": [[4.0, 0.5], [4.5, 0.5]], "n": 2, "score": 1.0}
    ka, kb = (f"{x['start']:.2f}-{x['end']:.2f}" for x in (a, b))

    class Ctx:
        def __init__(self):
            self.etapes = []

        def step(self, message, progress=None, stage=None):
            self.etapes.append(message)

        def check(self):
            pass
    ctx = Ctx()
    got = duel.analyse(pid, [m], [(m.id, a), (m.id, b)], ctx, seulement={(m.id, ka)})
    assert set(got) == {(m.id, ka)} and set(duel.cached(pid, m.id)) == {ka}
    assert any("1/1" in e for e in ctx.etapes)
    ctx = Ctx()
    got = duel.analyse(pid, [m], [(m.id, a), (m.id, b)], ctx)
    assert set(got) == {(m.id, ka), (m.id, kb)}, "la rafale déjà mesurée garde son résultat, l'autre est mesurée"
    assert sum("rafales mesurées" in e for e in ctx.etapes) == 1


def test_unreadable_media_gives_unknown_not_a_crash(tmp_path):
    pid = TestClient(app).post("/api/projects", json={"name": "Duel cassé"}).json()["id"]
    faux = tmp_path / "pas_une_video.mp4"
    faux.write_bytes(b"rien")
    m = _media("m_faux", faux)
    got = duel.analyse(pid, [m], [(m.id, _burst())])
    assert list(got.values())[0]["label"] == "inconnu"
    audio = types.SimpleNamespace(id="m_son", filename="a.wav", path="a.wav", proxy=None, width=0, height=0, duration=6.0,
                                  has_video=False, has_audio=True)
    r = duel.analyse(pid, [audio], [("m_son", _burst())])
    assert list(r.values())[0]["label"] == "inconnu"


# ---------------------------------------------------------------- courbe de vie : où le joueur cesse de perdre de la vie

def _courbe_res(t0, valeurs, fps=10):
    return {"label": "subit", "vie": {"valide": True, "courbe": {"t0": t0, "fps": fps, "v": valeurs}}}


def test_safe_window_stops_where_the_life_drops_more_than_a_heart():
    """Le cas du rush : 3 → 8 cœurs (potion), retombe à 4, puis 2,5 juste avant le kill (remise à 10 après)."""
    v = [3.0] * 4 + [6.0, 7.5, 7.5, 6.5, 6.5, 6.5, 5.0, 5.0, 4.0, 4.0, 4.0, 2.5, 2.5] + [10.0] * 8
    res = _courbe_res(70.0, v)  # t = 70,0 .. 72,4 ; la dernière chute (4 -> 2,5) est entre 71,4 et 71,5
    lo, hi = duel.fenetre_sans_chute(res, 71.65)  # le coup final du kill
    assert lo == pytest.approx(71.5 + 0.1, abs=1e-6), "le plan commence après la chute, avec une mesure de marge"
    assert hi == pytest.approx(72.4, abs=1e-6), "la remise à 10 n'est pas une perte : on va jusqu'au bout de la courbe"
    # Perte tolérée : un cœur, du pic au creux. Sur une vie qui tient, toute la courbe est montrable.
    plat = _courbe_res(10.0, [7.0] * 5 + [6.0] * 5)
    assert duel.fenetre_sans_chute(plat, 10.5) == (10.0, pytest.approx(10.9, abs=1e-6))
    assert duel.fenetre_sans_chute(_courbe_res(10.0, [7.0] * 5 + [5.5] * 5), 10.9)[0] > 10.4, "1,5 cœur perdu : coupé"
    # Sans courbe (ancien résultat) ou courbe illisible : None, jamais une fenêtre inventée.
    assert duel.fenetre_sans_chute({"vie": {"valide": True}}, 1.0) is None
    assert duel.fenetre_sans_chute(None, 1.0) is None
    assert duel.fenetre_sans_chute(_courbe_res(0.0, [5.0, float("nan"), 5.0]), 0.1) is None


def test_the_life_curve_is_kept_and_old_results_get_it_without_remeasuring_the_camera(tmp_path, monkeypatch):
    pid = TestClient(app).post("/api/projects", json={"name": "Duel courbe"}).json()["id"]
    c = _burst()
    k = f"{c['start']:.2f}-{c['end']:.2f}"
    m = _media("m_courbe", _video(tmp_path, "courbe.mp4", lambda t: 10 if t < 3 else 7.5))
    got = duel.analyse(pid, [m], [(m.id, c)])
    courbe = got[(m.id, k)]["vie"]["courbe"]
    assert courbe["fps"] == duel.FPS_VIE and courbe["t0"] == pytest.approx(c["start"] - duel.MARGE, abs=0.05)
    assert max(courbe["v"]) - min(courbe["v"]) >= 2 and len(courbe["v"]) > 30, "la baisse de vie de la vidéo se lit"
    assert (c["end"] + duel.MARGE_COURBE) - (courbe["t0"] + len(courbe["v"]) / courbe["fps"]) < 0.25, "elle va au-delà de la rafale"
    assert duel.cached(pid, m.id)[k]["vie"]["courbe"] == courbe, "gardée avec les signaux du rush"
    # Un résultat gardé avant que la courbe existe : seule la vie est relue, pas la caméra, et la courbe est gardée.
    sig = storage.load_signals(pid, m.id)
    del sig["duel"]["rafales"][k]["vie"]["courbe"]
    storage.save_signals(pid, m.id, sig)
    assert duel.fenetre_sans_chute(duel.cached(pid, m.id)[k], 3.0) is None

    def boom(*a, **kw):
        raise AssertionError("caméra relue pour une courbe de vie")
    monkeypatch.setattr(duel, "mesure_roulis", boom)
    monkeypatch.setattr(duel, "detecter_layout", boom)
    again = duel.analyse(pid, [m], [(m.id, c)])
    assert again[(m.id, k)]["vie"]["courbe"]["v"] == courbe["v"] and again[(m.id, k)]["label"] == got[(m.id, k)]["label"]
    assert duel.cached(pid, m.id)[k]["vie"]["courbe"]["v"] == courbe["v"], "écrite pour de bon"
    # Une lecture qui échoue laisse le résultat intact (sans courbe), sans rien casser ni graver.
    sig = storage.load_signals(pid, m.id)
    del sig["duel"]["rafales"][k]["vie"]["courbe"]
    storage.save_signals(pid, m.id, sig)
    monkeypatch.setattr(duel, "_lire_flux", boom)
    sans = duel.analyse(pid, [m], [(m.id, c)])
    assert "courbe" not in sans[(m.id, k)]["vie"] and sans[(m.id, k)]["label"] == got[(m.id, k)]["label"]


def test_an_unusable_life_curve_is_noted_and_not_read_again(tmp_path, monkeypatch):
    """Une courbe impossible à tirer (trop de trous sur la fenêtre élargie) est notée `None` : la vidéo n'est pas relue à chaque montage."""
    pid = TestClient(app).post("/api/projects", json={"name": "Duel courbe vide"}).json()["id"]
    c = _burst()
    k = f"{c['start']:.2f}-{c['end']:.2f}"
    m = _media("m_vide", _video(tmp_path, "vide.mp4", lambda t: 10 if t < 3 else 7.5))
    monkeypatch.setattr(duel, "_courbe", lambda t, vie: None)
    got = duel.analyse(pid, [m], [(m.id, c)])
    assert "courbe" in got[(m.id, k)]["vie"] and got[(m.id, k)]["vie"]["courbe"] is None, "échec noté dès la première mesure"

    def boom(*a, **kw):
        raise AssertionError("vidéo relue pour une courbe déjà notée comme illisible")
    monkeypatch.setattr(duel, "_lire_flux", boom)
    assert duel.analyse(pid, [m], [(m.id, c)])[(m.id, k)]["vie"]["courbe"] is None
    assert duel.fenetre_sans_chute(duel.cached(pid, m.id)[k], 3.0) is None, "sans courbe, pas de fenêtre inventée"
    # Un ancien résultat sans le champ : relu une fois, l'échec est noté, puis plus rien ne se relit.
    monkeypatch.undo()
    monkeypatch.setattr(duel, "_courbe", lambda t, vie: None)
    sig = storage.load_signals(pid, m.id)
    del sig["duel"]["rafales"][k]["vie"]["courbe"]
    storage.save_signals(pid, m.id, sig)
    duel.analyse(pid, [m], [(m.id, c)])
    assert duel.cached(pid, m.id)[k]["vie"].get("courbe", "absent") is None
    monkeypatch.setattr(duel, "_lire_flux", boom)
    duel.analyse(pid, [m], [(m.id, c)])
