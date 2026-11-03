"""Recadrage automatique : le cadre 9:16 suit un visage qui traverse l'image 16:9.

tests/data/face.jpg : portrait d'Eileen Collins (NASA, domaine public), via scikit-image.
"""
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.analysis import faces
from app.media import ffmpeg
from app.models import Clip, Media, Project, Timeline
from app.render import render
from app import storage

DATA = Path(__file__).parent / "data" / "face.jpg"
pytestmark = pytest.mark.skipif(not faces.AVAILABLE, reason="OpenCV absent")


@pytest.fixture(scope="module")
def moving_face(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("face") / "face.mp4"
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x445566:s=1280x720:r=30:d=10",
        "-loop", "1", "-i", str(DATA),
        "-f", "lavfi", "-i", "sine=f=220:d=10",
        "-filter_complex", "[1:v]scale=420:450[a];[0:v][a]overlay=x='20+t*80':y=120:shortest=1[v]",
        "-map", "[v]", "-map", "2:a", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
    ], check=True)
    return out


def test_track_follows_face(moving_face):
    fr = ffmpeg.decode_gray_small(moving_face, faces.FACE_FPS)
    r = faces.analyze(fr, [])
    assert r["found"] > 0.8
    cx = r["cx"]
    assert cx[-1] - cx[0] > 0.3  # le visage traverse ~0,55 de la largeur
    assert all(b >= a - 1e-6 for a, b in zip(cx, cx[1:]))  # trajectoire monotone, sans à-coups
    kf = faces.keyframes(r, 1.0, 9.0)
    assert 2 <= len(kf) <= 8


def test_vertical_render_keeps_face_in_frame(moving_face, tmp_path):
    p = Project(name="cadrage")
    info = ffmpeg.probe(moving_face)
    m = Media(filename="face.mp4", path=str(moving_face), **info)
    p.media.append(m)
    storage.save_project(p)
    fr = ffmpeg.decode_gray_small(moving_face, faces.FACE_FPS)
    storage.save_signals(p.id, m.id, {"faces": faces.analyze(fr, [])})
    tl = Timeline(format="9:16", captions="aucun", clips=[Clip(media_id=m.id, src_in=0.5, src_out=9.5)])
    out = tmp_path / "v.mp4"
    render.render(p, tl, {}, out, tmp_path, "720p")
    assert "clip((" in (tmp_path / "filtergraph.txt").read_text()
    frames = ffmpeg.decode_gray_small(out, 1.0, width=360)
    hits = [faces.detect(f[None])[0] for f in frames]
    centred = [h for h in hits if h and 0.2 < h[0] < 0.8]
    assert len(centred) >= 0.7 * len(frames), hits
    # Sans suivi : recadrage centré classique.
    tl.reframe = False
    render.build_command(p, tl, {}, tmp_path / "c.mp4", tmp_path, "720p")
    assert "clip((" not in (tmp_path / "filtergraph.txt").read_text()


def test_blur_follows_face(moving_face, tmp_path):
    """« Floute ce visage » sur une zone : le flou suit le visage qui se déplace."""
    from app.ai import comments as cm
    from app.models import Comment, EditSettings, Region

    fr = ffmpeg.decode_gray_small(moving_face, faces.FACE_FPS)
    track = faces.analyze(fr, [])
    raw0 = faces.detect(fr[3:4])[0]  # visage réel à t = 1 s
    tl = Timeline(format="16:9", captions="aucun", clips=[Clip(media_id="m", src_in=0.5, src_out=9.5)])
    c = Comment(media_id="m", src_t=1.0, text="Floute ce visage",
                region=Region(x=raw0[0] - 0.08, y=raw0[1] - 0.15, w=0.16, h=0.3))
    tl.comments = [c]
    cm.apply_all(tl, [], EditSettings(), {"m": 10.0}, faces={"m": track})
    mk = tl.clips[0].masks[0]
    assert mk.kind == "flou" and mk.follow_dx is not None and "suit le visage" in c.reply

    p = Project(name="flou")
    m = Media(id="m", filename="face.mp4", path=str(moving_face), **ffmpeg.probe(moving_face))
    p.media.append(m)
    storage.save_project(p)
    storage.save_signals(p.id, m.id, {"faces": track})
    out = tmp_path / "flou.mp4"
    render.render(p, tl, {}, out, tmp_path, "720p")
    # Le visage flouté ne doit plus être détecté, ni au début ni à la fin (il a bougé de ~0,5 largeur).
    frames = ffmpeg.decode_gray_small(out, 1.0, width=480)
    seen = [faces.detect(f[None])[0] for f in frames]
    assert sum(s is not None for s in seen) <= 1, seen
