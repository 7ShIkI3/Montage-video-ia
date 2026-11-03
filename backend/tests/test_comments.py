"""Commentaires de montage : ancrage sur les rushes et interprète local."""
from app.ai import comments as cm
from app.models import Clip, Comment, EditSettings, Timeline


def _tl() -> Timeline:
    return Timeline(clips=[Clip(media_id="m", src_in=0, src_out=4, score=0.5),
                           Clip(media_id="m", src_in=6, src_out=10, score=0.5),
                           Clip(media_id="m", src_in=12, src_out=16, score=0.5)])


def _apply(tl, text, src_t):
    c = Comment(media_id="m", src_t=src_t, text=text)
    tl.comments.append(c)
    cm.apply_all(tl, [], EditSettings(), {"m": 20.0})
    return c


def test_locate_follows_source_after_reorder():
    tl = _tl()
    c = Comment(media_id="m", src_t=7.0, text="x")
    assert cm.locate(tl, c) == (1, 5.0)
    tl.clips.reverse()
    assert cm.locate(tl, c) == (1, 5.0)
    tl.clips[0].speed = 2.0  # le plan 12-16 dure maintenant 2 s
    assert cm.locate(tl, c) == (1, 3.0)
    assert cm.locate(tl, Comment(media_id="m", src_t=5.0, text="x")) is None  # passage coupé


def test_remove_and_status():
    tl = _tl()
    c = _apply(tl, "Coupe ce passage, il ne sert à rien", 7.0)
    assert c.status == "applique" and "retiré" in c.reply
    assert [x.src_in for x in tl.clips] == [0, 12] and tl.rejected[0].src_in == 6


def test_local_edits():
    tl = _tl()
    assert _apply(tl, "ralenti ici", 13).status == "applique"
    assert tl.clips[2].speed == 0.5
    _apply(tl, "mets un texte « Incroyable ! » en gros", 2.0)
    assert tl.texts[-1].text == "Incroyable !" and tl.texts[-1].style == "gros_texte"
    _apply(tl, "un whoosh ici", 6.5)
    assert tl.sfx[-1].kind == "whoosh" and abs(tl.sfx[-1].start - 4.4) < 0.01
    _apply(tl, "zoom + noir et blanc", 8)
    assert tl.clips[1].zoom == 1.2 and "noir_et_blanc" in tl.clips[1].effects
    _apply(tl, "commence par ce plan", 14)
    assert tl.clips[0].src_in == 12
    _apply(tl, "coupe ici", 2.0)
    assert any(x.src_out == 2.0 for x in tl.clips) and any(x.src_in == 2.0 for x in tl.clips)
    _apply(tl, "trop long, raccourcis", 7)
    assert next(x for x in tl.clips if x.src_in == 6).src_out < 10


def test_not_understood():
    tl = _tl()
    c = _apply(tl, "hmm je sais pas", 1.0)
    assert c.status == "incompris" and c.reply


def test_quoted_text_is_content_and_added_after_reorder():
    tl = _tl()
    a = Comment(media_id="m", src_t=1.0, text="Ajoute un texte « Trop fort ! » en gros")
    b = Comment(media_id="m", src_t=13.0, text="Mets ce plan au début")
    tl.comments = [a, b]
    cm.apply_all(tl, [], EditSettings(), {"m": 20.0})
    assert all(x.volume == 1.0 for x in tl.clips)  # « trop fort » entre guillemets n'est pas une consigne
    assert tl.clips[0].src_in == 12
    assert abs(tl.texts[-1].start - (4.0 + 1.0 - 0.1)) < 0.01  # posé après le déplacement du plan
