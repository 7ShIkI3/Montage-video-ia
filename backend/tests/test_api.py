"""Test de bout en bout : import → montage IA → retouche → consigne → export."""
import time

import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.media import ffmpeg

client = TestClient(app)


def _wait(job):
    for _ in range(600):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def test_full_flow(rush):
    assert client.get("/api/health").json()["ok"]
    p = client.post("/api/projects", json={"name": "Test"}).json()
    pid = p["id"]
    with rush.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    media = r.json()[0]
    assert abs(media["duration"] - 24) < 0.5

    job = client.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json()
    j = _wait(job)
    assert j["status"] == "termine", j
    proj = client.get(f"/api/projects/{pid}").json()
    tl = proj["timeline"]
    assert tl["clips"], "le réalisateur doit produire des clips"
    assert proj["media"][0]["proxy"]
    # les silences (5-8 s, 13-16 s, 21-24 s) doivent être retirés
    total = sum(c["src_out"] - c["src_in"] for c in tl["clips"])
    assert total < 22
    sig = client.get(f"/api/projects/{pid}/media/{media['id']}/signals").json()
    assert sig["cuts"] and abs(sig["cuts"][0] - 12) < 0.5

    # retouche manuelle : on supprime le dernier clip
    tl["clips"] = tl["clips"][:-1] or tl["clips"]
    assert client.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200

    # consigne en langage naturel (mode local)
    j = _wait(client.post(f"/api/projects/{pid}/command", json={"instruction": "format vertical"}).json())
    assert j["status"] == "termine", j
    assert client.get(f"/api/projects/{pid}").json()["timeline"]["format"] == "9:16"

    j = _wait(client.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())
    assert j["status"] == "termine", j
    name = j["result"]
    r = client.get(f"/api/projects/{pid}/exports/{name}")
    assert r.status_code == 200 and len(r.content) > 10_000
    out = rush.parent / "out.mp4"
    out.write_bytes(r.content)
    info = ffmpeg.probe(out)
    assert (info["width"], info["height"]) == (720, 1280)
    assert info["has_audio"]


def test_every_effect_renders(rush, tmp_path):
    """Chaque effet, transition, effet audio, vitesse, piste Textes/Bruitages/B-roll passe dans FFmpeg."""
    from app import pipeline, storage
    from app.models import Clip, OverlayItem, Project, SfxItem, TextItem, Timeline
    from app.render import effects as fx
    from app.render.render import render

    p = Project(name="Effets")
    m = pipeline.ingest(p, rush, "rush.mp4")
    clips = []
    t = 0.0
    vfx = list(fx.VIDEO_EFFECTS)
    afx = list(fx.AUDIO_EFFECTS)
    trs = list(fx.TRANSITIONS)
    n = max(len(vfx), len(afx), len(trs))
    for i in range(n):
        a = (i * 1.1) % 20
        clips.append(Clip(media_id=m.id, src_in=a, src_out=a + 0.8, speed=[1.0, 0.5, 2.0][i % 3],
                          effects=[vfx[i % len(vfx)]], audio_effects=[afx[i % len(afx)]],
                          transition=trs[i % len(trs)], zoom=1.0 + 0.05 * (i % 3)))
    from app.models import Mask
    clips[0].masks = [Mask(x=0.1, y=0.1, w=0.3, h=0.3, kind=k) for k in ("flou", "pixel", "noir", "cadre")]
    clips[1].focus_x, clips[1].focus_y, clips[1].zoom = 0.8, 0.3, 1.8
    tl = Timeline(clips=clips, look="cinema", captions="aucun", format="9:16",
                  texts=[TextItem(start=0.2, text="Titre", style=s) for s in
                         ["titre", "bandeau", "legende", "gros_texte", "emoji"]],
                  sfx=[SfxItem(start=0.3 * k, kind=k_) for k, k_ in enumerate(fx.SFX)],
                  overlays=[OverlayItem(media_id=m.id, src_in=2, src_out=3.5, start=1.0, mode="plein"),
                            OverlayItem(media_id=m.id, src_in=5, src_out=6.5, start=4.0, mode="incrustation")])
    out = tmp_path / "effets.mp4"
    render(p, tl, {}, out, tmp_path, "720p")
    info = ffmpeg.probe(out)
    assert (info["width"], info["height"]) == (720, 1280)
    assert abs(info["duration"] - tl.duration) < 0.5, (info["duration"], tl.duration)


def test_xml_and_srt_export(rush):
    """L'export XML (Premiere / Resolve / Final Cut) se relit et couvre toute la timeline."""
    import xml.dom.minidom as md
    c = client
    pid = c.post("/api/projects", json={"name": "XML"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "vlog"}).json())["status"] == "termine"
    r = c.get(f"/api/projects/{pid}/export.xml")
    assert r.status_code == 200
    doc = md.parseString(r.text)
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    vitems = doc.getElementsByTagName("video")[0].getElementsByTagName("clipitem")
    assert len(vitems) == len(tl["clips"])
    end = int(vitems[-1].getElementsByTagName("end")[0].firstChild.data)
    total = sum((x["src_out"] - x["src_in"]) / x["speed"] for x in tl["clips"])
    assert abs(end - round(total * 30)) <= 1
    assert c.get(f"/api/projects/{pid}/captions.srt").status_code == 200
    j = _wait(c.post(f"/api/projects/{pid}/publish-kit").json())
    assert j["status"] == "termine", j
    kit = c.get(f"/api/projects/{pid}").json()["publish_kit"]
    assert kit["titles"] and kit["description"]
    r = c.get(f"/api/projects/{pid}/exports/{kit['thumbnail']}")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


def test_high_fps_and_resolution(rush, tmp_path):
    """Export 1440p à 60 i/s et 4K à 120 i/s, avec interpolation de mouvement sur un ralenti."""
    from app import pipeline
    from app.models import Clip, Project, Timeline
    from app.render.render import render, source_fps

    p = Project(name="HFR")
    m = pipeline.ingest(p, rush, "rush.mp4")
    assert source_fps(p, Timeline(clips=[Clip(media_id=m.id, src_in=0, src_out=1)])) == 30
    tl = Timeline(captions="aucun", look="vif",
                  clips=[Clip(media_id=m.id, src_in=1, src_out=2, effects=["zoom_lent"]),
                         Clip(media_id=m.id, src_in=3, src_out=3.6, speed=0.5, transition="zoom_avant")])
    out = tmp_path / "60.mp4"
    render(p, tl, {}, out, tmp_path, "1440p", fps=60, smooth=True)
    assert "minterpolate" in (tmp_path / "filtergraph.txt").read_text()
    info = ffmpeg.probe(out)
    assert (info["width"], info["height"], round(info["fps"])) == (2560, 1440, 60)
    assert abs(info["duration"] - tl.duration) < 0.3
    tl.clips = tl.clips[:1]
    out = tmp_path / "120.mp4"
    render(p, tl, {}, out, tmp_path, "2160p", fps=120)
    info = ffmpeg.probe(out)
    assert (info["width"], info["height"], round(info["fps"])) == (3840, 2160, 120)


def test_comments_guide_the_ai(rush):
    """Un commentaire posé sur un moment est appliqué, puis respecté quand l'IA refait le montage."""
    c = client
    pid = c.post("/api/projects", json={"name": "Commentaires"}).json()["id"]
    with rush.open("rb") as f:
        mid = c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))]).json()[0]["id"]
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    covers = lambda tl, t: any(x["media_id"] == mid and x["src_in"] <= t < x["src_out"] for x in tl["clips"])
    assert covers(tl, 9.5)
    tl["comments"] = [{"media_id": mid, "src_t": 9.5, "text": "Supprime ce passage"},
                      {"media_id": mid, "src_t": 1.0, "text": "Ajoute un texte « Salut ! »"}]
    assert c.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200
    j = _wait(c.post(f"/api/projects/{pid}/comments/apply").json())
    assert j["status"] == "termine", j
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert not covers(tl, 9.5)
    assert all(x["status"] == "applique" and x["reply"] for x in tl["comments"])
    assert any(t["text"] == "Salut !" for t in tl["texts"])
    # Re-montage complet : le commentaire est conservé et toujours respecté.
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert len(tl["comments"]) == 2 and not covers(tl, 9.5)
    assert c.post(f"/api/projects/{pid}/comments/apply").status_code == 400


def test_index_html_is_never_cached():
    """L'interface est servie sans cache : après une mise à jour, le navigateur ne garde pas un index périmé."""
    from app import config
    if not config.FRONTEND_DIST.exists():
        pytest.skip("frontend non construit")
    for path in ("/", "/index.html", "/p/x"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache", path
        assert "<div id=\"root\">" in r.text
