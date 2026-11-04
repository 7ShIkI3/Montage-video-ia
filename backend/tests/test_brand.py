"""Identité de marque tirée d'un logo ou d'un site, appliquée aux textes et aux sous-titres."""
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import storage
from app.ai import brand
from app.main import app
from app.models import Brand, TextItem
from app.render import captions as cap

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def _logo() -> bytes:
    """Fond transparent, grand disque bleu marine, petit éclair orange vif, texte blanc."""
    im = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 190, 190), fill=(20, 40, 120, 255))
    d.polygon([(250, 40), (300, 40), (270, 100), (310, 100), (240, 180), (260, 115), (225, 115)], fill=(255, 120, 0, 255))
    d.rectangle((320, 80, 390, 120), fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_logo_colors_primary_and_accent(tmp_path):
    p = tmp_path / "logo.png"
    p.write_bytes(_logo())
    b = brand.from_logo(p)
    pr, ac = brand._rgb(b.primary), brand._rgb(b.accent)
    assert pr[2] > 90 and pr[0] < 60  # bleu marine, la couleur la plus présente
    assert ac[0] > 220 and 80 < ac[1] < 160 and ac[2] < 50  # orange, la plus vive
    assert b.text == "#FFFFFF"  # texte blanc sur fond foncé
    assert all(h != "#FFFFFF" for h in b.colors)  # le blanc est écarté


def test_site_identity_from_html():
    html = """<html><head><title>Atelier Pixel</title><meta name="theme-color" content="#7C3AED">
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@700&display=swap" rel="stylesheet">
    <style>body{font-family:'Poppins',sans-serif;color:#222} .btn{background:#F59E0B} a{color:#7C3AED}</style>
    </head><body><h1>Tu veux apprendre le montage ?</h1><p>Rejoins-nous, c'est parti !</p></body></html>"""
    b = brand.from_html(html, url="https://atelier.example")
    assert b.primary == "#7C3AED" and b.font == "Poppins" and b.name == "Atelier Pixel"
    assert "tutoie" in b.tone and b.source == "site:https://atelier.example"
    with pytest.raises(ValueError):
        brand._check_public("http://127.0.0.1/admin")
    with pytest.raises(ValueError):
        brand._check_public("file:///etc/passwd")


def test_brand_colors_in_subtitles_and_texts():
    b = Brand(primary="#7C3AED", accent="#F59E0B", text="#FFFFFF")
    ln = cap.CaptionLine(start=0, end=1, words=[cap.Word(start=0, end=0.5, text="salut"), cap.Word(start=0.5, end=1, text="toi")])
    ass = cap.to_ass([ln], 1280, 720, "karaoke", "16:9", [TextItem(start=0, text="Go", style="bandeau")], b)
    assert r"\c&H000B9EF5&" in ass  # mot prononcé en orange (BGR)
    assert "&H00ED3A7C,&H00ED3A7C" in ass  # bandeau violet
    assert r"\c&H0000E5FF&" in cap.to_ass([ln], 1280, 720, "karaoke", "16:9")  # sans marque : jaune d'origine


def test_brand_api_template_and_export(rush):
    pid = c.post("/api/projects", json={"name": "Marque"}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    asset = c.post(f"/api/projects/{pid}/assets", files={"file": ("logo.png", _logo(), "image/png")}).json()["asset"]
    r = c.post(f"/api/projects/{pid}/brand", json={"asset": asset, "template": True})
    assert r.status_code == 200, r.text
    assert r.json()["template"]["brand"]["accent"] == r.json()["brand"]["accent"]
    assert c.post(f"/api/projects/{pid}/brand", json={}).status_code == 400
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    assert storage.load_project(pid).timeline.brand is not None  # gardée au re-montage
    assert _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())["status"] == "termine"
    # Le modèle de style rempli depuis le logo s'applique à un autre projet.
    other = c.post("/api/projects", json={"name": "Autre"}).json()["id"]
    tid = r.json()["template"]["id"]
    assert c.post(f"/api/projects/{other}/templates/{tid}/apply").json()["timeline"]["brand"]["accent"]
    assert c.delete(f"/api/projects/{pid}/brand").json()["ok"]
