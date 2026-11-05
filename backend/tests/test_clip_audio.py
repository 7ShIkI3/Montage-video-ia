"""Audio par plan : volume, fondus d'entrée et de sortie réglables, coupure du son, dans la commande de rendu."""
import re

from app import storage
from app.media import ffmpeg
from app.models import Clip, Media, Project, Timeline
from app.render import render


def _project(rush):
    p = Project(name="audio")
    m = Media(filename="rush.mp4", path=str(rush), **ffmpeg.probe(rush))
    p.media.append(m)
    storage.save_project(p)
    return p, m


def _graph(p, tl, tmp_path) -> str:
    render.build_command(p, tl, {}, tmp_path / "out.mp4", tmp_path, "720p")
    return (tmp_path / "filtergraph.txt").read_text(encoding="utf-8")


def test_clip_fades_and_volume_reach_ffmpeg(rush, tmp_path):
    p, m = _project(rush)
    tl = Timeline(captions="aucun", clips=[
        Clip(media_id=m.id, src_in=0.0, src_out=4.0, volume=0.5, fade_in=0.5, fade_out=1.0),
    ])
    g = _graph(p, tl, tmp_path)
    assert "volume=0.50" in g
    assert "afade=t=in:d=0.50" in g
    assert re.search(r"afade=t=out:st=3\.000:d=1\.00", g), g


def test_fades_are_clamped_to_half_the_clip(rush, tmp_path):
    p, m = _project(rush)
    tl = Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0.0, src_out=1.0, fade_in=5.0, fade_out=5.0)])
    g = _graph(p, tl, tmp_path)
    assert "afade=t=in:d=0.50" in g and "afade=t=out:st=0.500:d=0.50" in g


def test_default_clip_keeps_micro_fades(rush, tmp_path):
    """Sans réglage, les micro-fondus anti-clic restent (20 et 30 ms)."""
    p, m = _project(rush)
    g = _graph(p, Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0.0, src_out=2.0)]), tmp_path)
    assert "afade=t=in:d=0.02" in g and "afade=t=out:st=1.970:d=0.03" in g


def test_mute_effect_silences_the_clip(rush, tmp_path):
    p, m = _project(rush)
    g = _graph(p, Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0.0, src_out=2.0, audio_effects=["muet"])]), tmp_path)
    assert "volume=0," in g or "volume=0\n" in g or "volume=0[" in g or ",volume=0," in g


def test_fade_bounds_are_validated():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Clip(media_id="m", src_in=0, src_out=1, fade_in=-1)
    with pytest.raises(ValidationError):
        Clip(media_id="m", src_in=0, src_out=1, fade_out=9)
