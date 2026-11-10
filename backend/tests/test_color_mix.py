"""Correction colorimétrique par plan, mixeur audio et effets audio « voix studio » : modèle, commande FFmpeg
et rendu réel (luminosité, saturation, silence mesurés)."""
import re
import subprocess

import numpy as np
import pytest
from pydantic import ValidationError

from app import storage
from app.media import ffmpeg
from app.models import AudioItem, Clip, ColorAdjust, Media, MixChannel, Mixer, MusicSettings, Project, SfxItem, Timeline
from app.render import effects as fx
from app.render import render


def _project(rush):
    p = Project(name="couleur")
    m = Media(filename="rush.mp4", path=str(rush), **ffmpeg.probe(rush))
    p.media.append(m)
    storage.save_project(p)
    return p, m


def _graph(p, tl, tmp_path, **kw) -> str:
    render.build_command(p, tl, {}, tmp_path / "out.mp4", tmp_path, "720p", **kw)
    return (tmp_path / "filtergraph.txt").read_text(encoding="utf-8")


def _export(p, tl, tmp_path, name="out.mp4", music_path=None):
    out = tmp_path / name
    render.render(p, tl, {}, out, tmp_path, "720p", music_path=music_path)
    return out


def _frame(path, t=0.5) -> np.ndarray:
    """Une image RVB de l'export, en tableau (h, l, 3)."""
    info = ffmpeg.probe(path)
    w, h = info["width"], info["height"]
    raw = subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(float)


def _saturation(img: np.ndarray) -> float:
    return float((img.max(axis=2) - img.min(axis=2)).mean())


def _mean_volume(path) -> float:
    """Volume moyen (dB) de la piste audio de l'export."""
    r = subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr).group(1))


# --- Modèle -----------------------------------------------------------------------------------------

def test_defaults_are_neutral_and_old_documents_still_load():
    assert Clip(media_id="m", src_in=0, src_out=1).color is None
    assert ColorAdjust().neutral and not ColorAdjust(contrast=0.1).neutral
    tl = Timeline.model_validate({"clips": []})  # un document sans `mix` ni `color` (ancien projet, client ancien)
    assert tl.mix == Mixer()
    assert all(g.volume == 1.0 and not g.mute for g in (tl.mix.rushes, tl.mix.music, tl.mix.sfx, tl.mix.tracks))
    assert MixChannel(volume=1.5, mute=True).gain == 0.0 and MixChannel(volume=1.5).gain == 1.5


def test_bounds_are_validated():
    for bad in ({"exposure": 1.5}, {"contrast": -2}, {"saturation": 9}, {"temperature": -1.1}):
        with pytest.raises(ValidationError):
            ColorAdjust(**bad)
    with pytest.raises(ValidationError):
        MixChannel(volume=2.5)
    with pytest.raises(ValidationError):
        MixChannel(volume=-0.1)


def test_timeline_roundtrip_keeps_color_and_mix():
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=1, color=ColorAdjust(exposure=0.3))],
                  mix=Mixer(music=MixChannel(volume=0.4, mute=True)))
    back = Timeline.model_validate_json(tl.model_dump_json())
    assert back.clips[0].color.exposure == 0.3 and back.mix.music.mute and back.mix.music.volume == 0.4


# --- Couleur ----------------------------------------------------------------------------------------

def test_color_filters_reach_the_command(rush, tmp_path):
    p, m = _project(rush)
    color = ColorAdjust(exposure=0.5, contrast=0.5, saturation=-0.5, temperature=0.5)
    g = _graph(p, Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=2, color=color)]), tmp_path)
    assert "eq=brightness=0.1000:contrast=1.3000:saturation=0.5000" in g, g
    assert "colorbalance=" in g
    g = _graph(p, Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=2)]), tmp_path)
    assert "eq=" not in g and "colorbalance" not in g
    g = _graph(p, Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=2, color=ColorAdjust())]),
               tmp_path)
    assert "eq=" not in g and "colorbalance" not in g  # tout à zéro = neutre


def test_color_is_applied_before_the_3d_engine(rush, tmp_path):
    """Les plans à effets 3D passent par build_command(three_d=True) : la correction y est aussi."""
    p, m = _project(rush)
    tl = Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=2, effects=["3d_orbite"],
                                                color=ColorAdjust(saturation=0.4))])
    assert render.scene3d.needs_3d(tl)
    g = _graph(p, tl, tmp_path, three_d=True)
    assert "eq=saturation=1.4000" in g and "format=bgr24[vraw]" in g


@pytest.fixture(scope="module")
def dull(tmp_path_factory):
    """Plan terne (couleur unie peu saturée, mi-clair) : laisse de la marge dans les deux sens."""
    out = tmp_path_factory.mktemp("terne") / "terne.mp4"
    subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x907860:s=640x360:r=30:d=3", "-f", "lavfi", "-i", "sine=f=220:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


def test_color_changes_the_rendered_picture(dull, tmp_path):
    p, m = _project(dull)

    def shot(name, color):
        tl = Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=1.5, color=color)])
        return _frame(_export(p, tl, tmp_path, name))

    base = shot("base.mp4", None)
    assert _saturation(shot("neutre.mp4", ColorAdjust())) == pytest.approx(_saturation(base), abs=0.5)
    assert shot("clair.mp4", ColorAdjust(exposure=0.8)).mean() > base.mean() + 15
    assert shot("sombre.mp4", ColorAdjust(exposure=-0.8)).mean() < base.mean() - 15
    assert _saturation(shot("gris.mp4", ColorAdjust(saturation=-1))) < 0.1 * _saturation(base)
    assert _saturation(shot("vive.mp4", ColorAdjust(saturation=0.8))) > _saturation(base) * 1.3
    chaud, froid = shot("chaud.mp4", ColorAdjust(temperature=1)), shot("froid.mp4", ColorAdjust(temperature=-1))

    def rb(im):  # rouge moins bleu
        return im[..., 0].mean() - im[..., 2].mean()

    assert rb(chaud) > rb(base) + 10 and rb(froid) < rb(base) - 10


# --- Mixeur -----------------------------------------------------------------------------------------

def _mix_timeline(m, p, mix, ducking=True):
    audio = storage.audio_dir(p.id) / "voix.wav"
    subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=330:d=2", str(audio)], check=True)
    return Timeline(captions="aucun", mix=mix, music=MusicSettings(volume=0.5, ducking=ducking),
                    clips=[Clip(media_id=m.id, src_in=0, src_out=2, volume=0.8)],
                    sfx=[SfxItem(start=0.2, kind="pop", volume=0.6)],
                    audio=[AudioItem(asset="voix.wav", src_out=2.0, volume=0.9)])


def test_mixer_gains_multiply_each_group(rush, tmp_path):
    p, m = _project(rush)
    tl = _mix_timeline(m, p, Mixer(rushes=MixChannel(volume=0.5), music=MixChannel(volume=0.5),
                                   sfx=MixChannel(volume=0.5), tracks=MixChannel(volume=2.0)))
    g = _graph(p, tl, tmp_path, music_path=str(rush))
    assert "volume=0.40," in g  # plan : 0.8 x 0.5
    assert "volume=0.30," in g  # bruitage : 0.6 x 0.5
    assert "volume=1.800," in g  # piste audio : 0.9 x 2
    assert "volume=0.250," in g  # musique : 0.5 x 0.5
    assert "sidechaincompress" in g


def test_mixer_mute_zeroes_the_group_and_rushes_mute_stops_ducking(rush, tmp_path):
    p, m = _project(rush)
    mix = Mixer(rushes=MixChannel(mute=True), music=MixChannel(mute=True), sfx=MixChannel(mute=True),
                tracks=MixChannel(mute=True))
    g = _graph(p, _mix_timeline(m, p, mix), tmp_path, music_path=str(rush))
    assert "volume=0.00," in g and "volume=0.000," in g
    assert "sidechaincompress" not in g
    assert "loudnorm" in g  # la normalisation finale reste
    g = _graph(p, _mix_timeline(m, p, Mixer(music=MixChannel(mute=True))), tmp_path, music_path=str(rush))
    assert "sidechaincompress" in g  # musique seule coupée : les rushes déclenchent toujours le ducking


def test_muting_the_rushes_really_silences_the_export(rush, tmp_path):
    p, m = _project(rush)
    clips = [Clip(media_id=m.id, src_in=0.0, src_out=3.0)]  # de la parole (0-5 s du rush)
    loud = _export(p, Timeline(captions="aucun", clips=clips), tmp_path, "avec.mp4")
    mute = _export(p, Timeline(captions="aucun", clips=clips, mix=Mixer(rushes=MixChannel(mute=True))),
                   tmp_path, "sans.mp4")
    assert _mean_volume(loud) > -35
    assert _mean_volume(mute) < -60
    zero = _export(p, Timeline(captions="aucun", clips=clips, mix=Mixer(rushes=MixChannel(volume=0.0))),
                   tmp_path, "zero.mp4")
    assert _mean_volume(zero) < -60  # un volume à 0 vaut une coupure


def test_muting_music_removes_it_from_the_export(rush, tmp_path):
    p, m = _project(rush)
    tone = tmp_path / "musique.wav"
    subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=440:d=4", str(tone)], check=True)
    clips = [Clip(media_id=m.id, src_in=0.0, src_out=3.0, volume=0.0)]  # plan muet : seule la musique s'entend
    tl = Timeline(captions="aucun", clips=clips, music=MusicSettings(volume=0.5, ducking=False))
    assert _mean_volume(_export(p, tl, tmp_path, "mus.mp4", str(tone))) > -35
    tl.mix.music.mute = True
    assert _mean_volume(_export(p, tl, tmp_path, "mus_off.mp4", str(tone))) < -60


# --- Voix studio ------------------------------------------------------------------------------------

def test_studio_voice_effects_are_in_the_catalog_and_render(rush, tmp_path):
    ids = {e["id"] for e in fx.catalog()["audio_effects"]}
    assert {"voix_studio", "anti_bruit"} <= ids  # « réduction de bruit » existait déjà sous l'id anti_bruit
    assert fx.AUDIO_EFFECTS["anti_bruit"]["filter"].startswith("afftdn")
    spec = fx.AUDIO_EFFECTS["voix_studio"]["filter"]
    assert "highpass=f=80" in spec and "afftdn" in spec and "acompressor" in spec and "equalizer" in spec
    p, m = _project(rush)
    tl = Timeline(captions="aucun", clips=[Clip(media_id=m.id, src_in=0, src_out=2,
                                                audio_effects=["voix_studio", "anti_bruit"])])
    assert spec in _graph(p, tl, tmp_path)
    out = _export(p, tl, tmp_path)
    assert _mean_volume(out) > -40  # le son passe toujours après le traitement


# --- Le mixage survit au re-montage -----------------------------------------------------------------

def test_mix_survives_auto_edit_and_ai_command(rush):
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)

    def wait(job):
        for _ in range(600):
            j = client.get(f"/api/jobs/{job['id']}").json()
            if j["status"] in ("termine", "erreur"):
                return j
            time.sleep(0.2)
        raise TimeoutError(job)

    pid = client.post("/api/projects", json={"name": "Mixage"}).json()["id"]
    with rush.open("rb") as f:
        client.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    tl = client.get(f"/api/projects/{pid}").json()["timeline"]
    tl["mix"] = {"rushes": {"volume": 0.6, "mute": False}, "music": {"volume": 1.0, "mute": True},
                 "sfx": {"volume": 1.0, "mute": False}, "tracks": {"volume": 1.4, "mute": False}}
    assert client.put(f"/api/projects/{pid}/timeline", json=tl).status_code == 200

    def mix():
        return client.get(f"/api/projects/{pid}").json()["timeline"]["mix"]

    assert wait(client.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    assert mix() == tl["mix"]
    j = wait(client.post(f"/api/projects/{pid}/command", json={"instruction": "format vertical"}).json())
    assert j["status"] == "termine"
    assert mix() == tl["mix"]
