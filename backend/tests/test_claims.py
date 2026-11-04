"""Vérification des affirmations des textes et du titre."""
from app.ai import claims, quality
from app.models import Clip, TextItem, Timeline, Transcript, Word


def test_overclaims_and_soften():
    assert claims.overclaims("Mon avis sur ce casque")  == []
    assert claims.overclaims("Le meilleur moment du live") == []  # un label, pas une promesse
    found = claims.overclaims("Résultat garanti en une minute !!")
    assert {w for _, w in found} >= {"promesse de résultat", "promesse de délai"}
    assert claims.soften("Résultat garanti en une minute !!") == "Résultat rapidement!"
    assert claims.soften("Le meilleur tuto du monde") == "Un des meilleurs tuto que je connaisse"
    assert claims.soften("Ce que personne ne vous dit") == "Ce que peu de gens vous dit"
    assert claims.soften("Un résultat INCROYABLE") == "Un résultat surprenant"


def test_numbers_must_be_said():
    spoken = "j'ai gagné trois mille euros en 2 mois, et douze clients"
    assert claims.unsupported_numbers("2 mois pour réussir", spoken) == []
    assert claims.unsupported_numbers("12 clients", spoken) == []
    assert claims.unsupported_numbers("10 000 € par mois", spoken) == ["10 000 €"]
    assert claims.unsupported_numbers("Mon bilan 2025", spoken) == []  # une année
    assert claims.unsupported_numbers("3e partie", spoken) == []  # un rang


def test_quality_flags_and_softens_claims():
    tr = {"m": Transcript(media_id="m", words=[Word(start=0.2 + 0.4 * k, end=0.5 + 0.4 * k, text=w)
                                               for k, w in enumerate("voici ma méthode pour progresser vite".split())])}
    tl = Timeline(clips=[Clip(media_id="m", src_in=0, src_out=6, score=0.8)], title="La méthode garantie à 100 %")
    tl.texts = [TextItem(start=1.0, duration=2.5, text="Le meilleur conseil", style="legende"),
                TextItem(start=3.5, duration=2.5, text="+500 abonnés", style="legende")]
    rep = quality.plan_checks(tl, tr)
    aff = [i for i in rep.issues if i.check == "affirmation"]
    assert len(aff) == 3 and sum(i.fix == "affirmation_adoucir" for i in aff) == 2
    fixed = quality.check_and_fix(tl, tr)
    assert fixed.title == "La méthode"
    assert fixed.texts[0].text == "Un des meilleurs conseil"
    assert fixed.texts[1].text == "+500 abonnés"  # chiffre signalé, pas modifié
    assert any(i.check == "affirmation" and "500" in i.message for i in fixed.quality.issues)
