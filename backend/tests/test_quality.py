"""Règles de réalisation, contrôle qualité (plan et vidéo finie), relecteur indépendant, feuille son/image."""
import subprocess
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import pipeline, storage
from app.ai import quality, review, soundsheet
from app.main import app
from app.models import (AudioItem, Clip, EditSettings, ImageItem, ImpactItem, SfxItem, TextItem, Timeline, Transcript, Word)

c = TestClient(app)


def _wait(job):
    for _ in range(1500):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("termine", "erreur"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job)


def _project(rush, name):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    with rush.open("rb") as f:
        c.post(f"/api/projects/{pid}/media", files=[("files", ("rush.mp4", f, "video/mp4"))])
    assert _wait(c.post(f"/api/projects/{pid}/auto-edit", json={"style": "youtube"}).json())["status"] == "termine"
    return pid


def _bad_timeline():
    tl = Timeline(format="9:16")
    tl.clips = [Clip(media_id="m", src_in=0, src_out=20, score=0.3), Clip(media_id="m", src_in=30, src_out=33, score=0.9),
                Clip(media_id="m", src_in=40, src_out=43, score=0.5)]
    tl.texts = [TextItem(start=4, duration=0.8, text="Voici un texte beaucoup trop long pour un téléphone", style="gros_texte")]
    tl.impacts = [ImpactItem(start=5 + 0.2 * k, kind="flash") for k in range(5)]
    tl.sfx = [SfxItem(start=10.37, kind="whoosh")]
    tr = {"m": Transcript(media_id="m", words=[Word(start=2.5 + 0.4 * k, end=2.8 + 0.4 * k, text=f"mot{k}")
                                               for k in range(30)]
                          + [Word(start=30.0, end=30.3, text="fort")])}
    return tl, tr


def test_plan_checks_find_and_fix_problems():
    tl, tr = _bad_timeline()
    st = EditSettings(style="short")
    rep = quality.plan_checks(tl, tr, st)
    checks = {i.check for i in rep.issues}
    assert {"accroche", "moment_fort", "plan_long", "texte", "lecture", "flashs", "bruitage"} <= checks
    assert rep.score < 50
    fixed = quality.check_and_fix(tl, tr, st)
    assert fixed.quality.score > rep.score and fixed.quality.fixed
    assert fixed.clips[0].score == 0.9  # moment fort en ouverture (short)
    assert all(len(x.text) <= 18 for x in fixed.texts if x.style == "gros_texte")
    assert sum(i.kind == "flash" for i in fixed.impacts) <= 3
    assert not any(i.check == "flashs" for i in fixed.quality.issues)
    # Le contrôle local note la version : meilleure après corrections.
    assert review.local_review(fixed.quality).score > review.local_review(rep).score


def test_moving_the_strongest_shot_first_carries_what_sits_on_it():
    """Placer un plan en ouverture déplace aussi ses impacts, textes et bruitages ; ceux des plans dépassés reculent."""
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=4, score=0.3), Clip(media_id="m", src_in=10, src_out=12, score=0.4),
                         Clip(media_id="m", src_in=30, src_out=33, score=0.9), Clip(media_id="m", src_in=40, src_out=42, score=0.5)])
    tl.impacts = [ImpactItem(start=1.0, kind="secousse"), ImpactItem(start=6.5, kind="secousse"), ImpactItem(start=9.5, kind="secousse")]
    tl.sfx = [SfxItem(start=6.0, kind="boom")]
    tl.texts = [TextItem(start=4.0, duration=1.0, text="Deux"), TextItem(start=7.0, duration=1.0, text="Fort")]
    quality._move_first(tl, 2)
    assert [c.score for c in tl.clips] == [0.9, 0.3, 0.4, 0.5] and tl.clips[0].transition == "aucune"
    # Le plan fort allait de 6 à 9 s : ce qui était posé dessus garde sa place dans le plan (6,5 → 0,5 ; 6,0 → 0,0 ; 7,0 → 1,0).
    assert [i.start for i in tl.impacts] == [4.0, 0.5, 9.5]  # avant lui : +3 s ; sur lui : -6 s ; après lui : inchangé
    assert tl.sfx[0].start == 0.0
    assert [x.start for x in tl.texts] == [7.0, 1.0]


def test_moving_a_shot_leaves_what_covers_the_whole_edit_and_supports_the_moved_shot():
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=4, score=0.3), Clip(media_id="m", src_in=10, src_out=12, score=0.4),
                         Clip(media_id="m", src_in=30, src_out=33, score=0.9), Clip(media_id="m", src_in=40, src_out=42, score=0.5)])
    tl.images = [ImageItem(asset="logo.png", start=0.0, duration=None)]  # filigrane
    tl.audio = [AudioItem(asset="voix.wav", start=0.0, src_out=11.0, kind="voix")]
    out = quality.check_and_fix(tl, {}, EditSettings(style="short"))
    assert out.clips[0].score == 0.9
    assert out.images[0].start == 0.0 and out.audio[0].start == 0.0  # ni l'un ni l'autre n'est posé sur un plan
    # L'appui du moment fort (bruitage) tombe sur le plan déplacé, à 0 s, pas à son ancienne place (6 s).
    assert [s.start for s in out.sfx] == [0.0]


def _pvp_timeline():
    tl = Timeline(director="pvp")
    tl.clips = [Clip(media_id="m", src_in=70, src_out=71, score=0.4, reason="Aperçu du moment fort, en accroche.", effects=["3d_bascule"]),
                Clip(media_id="m", src_in=260, src_out=264, score=1.0), Clip(media_id="m", src_in=110, src_out=112, score=0.7),
                Clip(media_id="m", src_in=70, src_out=71, score=0.4, speed=0.4, transition="flash")]
    tl.impacts = [ImpactItem(start=0.4, kind="secousse"), ImpactItem(start=1.5, kind="secousse"), ImpactItem(start=3.2, kind="secousse"),
                  ImpactItem(start=7.4, kind="flash")]
    # Comme `pvp.build` : le souffle part 0,25 s avant la coupe du moment fort (7,0 s), la carte ne dépasse pas de son plan.
    tl.sfx = [SfxItem(start=0.0, kind="boom"), SfxItem(start=6.75, kind="whoosh"), SfxItem(start=7.4, kind="boom")]
    tl.texts = [TextItem(start=7.4, duration=0.7, text="KILL", style="gros_texte")]
    return tl


def test_pvp_edit_is_not_reordered_nor_dressed_again():
    """Un edit PvP est calé coup par coup et ouvre sur son aperçu : le contrôle qualité ne déplace aucun plan (les
    secousses resteraient à l'ancienne place) et n'ajoute ni zoom ni bruitage sur le plan au meilleur score."""
    tl = _pvp_timeline()
    before = tl.model_dump()
    st = EditSettings(style="pvp", captions="aucun")
    rep = quality.plan_checks(tl, {}, st)
    assert not any(i.fix in ("accroche_ordre", "moment_fort") for i in rep.issues)
    out = quality.check_and_fix(tl, {}, st)
    assert [(c.src_in, c.speed) for c in out.clips] == [(c["src_in"], c["speed"]) for c in before["clips"]]
    assert [c.zoom for c in out.clips] == [1.0] * 4
    assert [i.start for i in out.impacts] == [0.4, 1.5, 3.2, 7.4]
    assert [(s.kind, s.start) for s in out.sfx] == [("boom", 0.0), ("whoosh", 6.75), ("boom", 7.4)]
    assert out.texts[0].duration == 0.7  # signalée comme courte à lire, pas rallongée sur autre chose
    assert out.quality.fixed == []
    # Le même montage, sans la marque PvP, serait réordonné : c'est bien la marque qui protège.
    autre = _pvp_timeline()
    autre.director = "local"
    assert any(i.fix == "accroche_ordre" for i in quality.plan_checks(autre, {}, EditSettings(style="short")).issues)


def test_hook_trims_silent_start():
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=10, score=0.8)])
    tl.texts = [TextItem(start=5, duration=2, text="Salut")]
    tr = {"m": Transcript(media_id="m", words=[Word(start=3.0 + 0.4 * k, end=3.3 + 0.4 * k, text="mot") for k in range(10)])}
    out = quality.check_and_fix(tl, tr)
    assert out.clips[0].src_in == pytest.approx(2.8)
    assert out.texts[0].start == pytest.approx(2.2)  # les éléments suivent la coupe
    assert not any(i.check == "accroche" for i in out.quality.issues)


def test_render_checks_measure_loudness_and_flashes(tmp_path):
    flashy = tmp_path / "flash.mp4"
    # Alternance noir/blanc 6 fois par seconde + son très faible.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=320x180:r=30:d=3,geq=lum='if(lt(mod(T*12,2),1),235,16)':cb=128:cr=128",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-af", "volume=0.01", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(flashy)], check=True)
    issues, m = quality.render_checks(flashy, Timeline())
    assert m["flashes_max_per_s"] > 3 and any(i.check == "flashs" and i.level == "erreur" for i in issues)
    assert m["lufs"] is not None and m["lufs"] < -30 and any(i.check == "son" for i in issues)


def test_music_peak_aligned_on_strong_moment(tmp_path):
    music = tmp_path / "musique.wav"
    # 120 BPM, calme jusqu'à 20 s puis fort : le pic doit être repéré vers 20 s.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='(0.08+0.7*gte(t,20))*sin(2*PI*110*t)*exp(-8*mod(t,0.5))':s=22050:d=40",
                    str(music)], check=True)
    prof = soundsheet.music_profile(music)
    assert prof and abs(prof["bpm"] - 120) < 3 and 18.5 <= prof["peak"] <= 21.5
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=8, score=0.4), Clip(media_id="m", src_in=10, src_out=20, score=0.9)])
    tl.music.track = str(music)
    assert soundsheet.align_music_peak(tl, prof)
    assert tl.music.start + 8.0 == pytest.approx(prof["peak"], abs=0.3)


def test_auto_edit_runs_quality_review_and_render_check(rush, tmp_path):
    pid = _project(rush, "Qualité")
    tl = c.get(f"/api/projects/{pid}").json()["timeline"]
    assert tl["quality"] and 0 <= tl["quality"]["score"] <= 100
    assert tl["review"]["reviewer"] == "local" and tl["review"]["history"]
    j = _wait(c.post(f"/api/projects/{pid}/quality", json={"fix": True}).json())
    assert j["status"] == "termine", j
    j = _wait(c.post(f"/api/projects/{pid}/review").json())
    assert j["status"] == "termine" and j["result"]["rounds"] >= 1
    assert "instant | son" in c.get(f"/api/projects/{pid}/soundsheet").text
    j = _wait(c.post(f"/api/projects/{pid}/export", json={"quality": "720p"}).json())
    assert j["status"] == "termine", j
    q = c.get(f"/api/projects/{pid}").json()["timeline"]["quality"]
    assert q["rendered"] and q["measured"]["lufs"] == pytest.approx(-14, abs=2.5)
    assert q["measured"]["flashes_max_per_s"] <= 3


def test_reviewer_loop_retouches_until_passing(rush, monkeypatch):
    pid = _project(rush, "Relecture")
    notes = []

    class FakeReviewer:
        def review(self, project, tl, transcripts, report, sheet, style):
            notes.append(sheet)
            score = 5.0 if len(notes) == 1 else 8.5
            return (review.Review(score=score, verdict="ok", reviewer="claude",
                                  remarks=[review.ReviewRemark(t=1.0, problem="accroche molle", fix="resserrer")]),
                    "Resserre l'ouverture.")

    class FakeDirector:
        name = "claude"
        calls = []

        def command(self, instruction, timeline, segments, settings, music_track=None):
            self.calls.append(instruction)
            out = timeline.model_copy(deep=True)
            out.title = "Retouché"
            return out

    d = FakeDirector()
    monkeypatch.setattr(pipeline.review_mod, "get_reviewer", lambda: FakeReviewer())
    monkeypatch.setattr(pipeline.dir_mod, "get_director", lambda: d)
    j = _wait(c.post(f"/api/projects/{pid}/review").json())
    assert j["status"] == "termine", j
    tl = storage.load_project(pid).timeline
    assert tl.review.history == [5.0, 8.5] and tl.review.rounds == 2 and tl.review.reviewer == "claude"
    assert tl.title == "Retouché" and "Resserre l'ouverture." in d.calls[0]
    assert notes and "instant | son" in notes[0]
