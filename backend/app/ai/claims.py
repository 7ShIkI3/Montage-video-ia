"""Vérifier les affirmations des textes à l'écran et du titre, et adoucir celles qui exagèrent.

Deux contrôles, sans appel réseau :
- les tournures qui promettent trop (« le meilleur », « garanti », « jamais vu », « 100 % ») sont repérées et
  peuvent être adoucies automatiquement ;
- un chiffre affiché qui n'est jamais dit dans la vidéo est signalé : il faut pouvoir le justifier.
Le relecteur Claude, quand il est là, juge en plus le fond (voir review.py).
"""
from __future__ import annotations

import re
import unicodedata

# (motif, remplacement, raison). Le remplacement garde le sens en retirant la promesse.
RULES: list[tuple[str, str, str]] = [
    (r"\b(le|la) (meilleur|meilleure) (\w+) (du monde|de tous les temps|au monde)\b", r"\1 \2 \3 que je connaisse",
     "superlatif absolu"),
    (r"\b(du monde|de tous les temps|au monde)\b", "", "superlatif absolu"),
    (r"\ble meilleur\b(?! (?:moment|passage|extrait|plan)s?\b)", "un des meilleurs", "superlatif"),
    (r"\bla meilleure\b(?! (?:partie|scène|séquence)s?\b)", "une des meilleures", "superlatif"),
    (r"\bles meilleur(e)?s\b", "de très bon\1s", "superlatif"),
    (r"\bgaranti(e|s|es)?\b", "", "promesse de résultat"),
    (r"\bà coup sûr\b", "souvent", "promesse de résultat"),
    (r"\bsans effort\b", "plus facilement", "promesse de résultat"),
    (r"\ben (une|1) minute\b", "rapidement", "promesse de délai"),
    (r"\bà 100 ?%", "", "certitude totale"),
    (r"\b100 ?%", "vraiment", "certitude totale"),
    (r"\bjamais vu\b", "rarement vu", "affirmation invérifiable"),
    (r"\bpersonne ne (\w+)", r"peu de gens \1", "généralisation"),
    (r"\btout le monde\b", "beaucoup de monde", "généralisation"),
    (r"\b(choquant|hallucinant|incroyable|ahurissant)\b", "surprenant", "exagération"),
    (r"\b(secret|secrets) que (\w+) (cache|cachent)\b", r"astuce peu connue", "promesse de révélation"),
    (r"\b(the best|best ever)\b", "one of the best", "superlative"),
    (r"\bguaranteed\b", "", "promise"),
    (r"\b(insane|unbelievable|shocking)\b", "surprising", "exaggeration"),
    (r"\bnobody\b", "few people", "generalisation"),
    (r"\beveryone\b", "many people", "generalisation"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r, why) for p, r, why in RULES]
NUMBER = re.compile(r"(?<![\w])(\d[\d  .,]*\d|\d)\s*(%|€|\$|k€|k|x|fois|jours?|heures?|minutes?|ans?|euros?)?",
                    re.IGNORECASE)


def _plain(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def overclaims(text: str) -> list[tuple[str, str]]:
    """Tournures qui exagèrent : (extrait, raison)."""
    out = []
    for rx, _, why in _COMPILED:
        for m in rx.finditer(text):
            out.append((m.group(0), why))
    # Un même extrait ne compte qu'une fois (la règle la plus précise d'abord).
    seen, uniq = set(), []
    for frag, why in out:
        k = frag.lower()
        if not any(k in s or s in k for s in seen):
            seen.add(k)
            uniq.append((frag, why))
    return uniq


def soften(text: str) -> str:
    """Version adoucie : la promesse est retirée, le sens reste."""
    out = text
    for rx, rep, _ in _COMPILED:
        out = rx.sub(rep, out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;:")
    out = re.sub(r"\s+([!?.,])", r"\1", out)
    out = re.sub(r"!{2,}", "!", out)
    return out[:1].upper() + out[1:] if out else text


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def unsupported_numbers(text: str, spoken: str) -> list[str]:
    """Chiffres affichés qui ne sont jamais dits dans la vidéo (ni en chiffres dans la transcription)."""
    said = {_digits(m.group(1)) for m in NUMBER.finditer(spoken)}
    said |= {str(n) for n in range(0, 21) if _NUM_WORDS.get(n) and re.search(rf"\b{_NUM_WORDS[n]}\b", _plain(spoken))}
    out = []
    for m in NUMBER.finditer(text):
        d = _digits(m.group(1))
        if d == "100" and (m.group(2) or "") == "%":
            continue  # « 100 % » est une exagération, déjà signalée comme telle
        if d and len(d) <= 9 and d not in said and not _is_date_or_rank(text, m):
            out.append(m.group(0).strip())
    return out


_NUM_WORDS = {0: "zero", 1: "un", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit",
              9: "neuf", 10: "dix", 11: "onze", 12: "douze", 13: "treize", 14: "quatorze", 15: "quinze",
              16: "seize", 17: "dix-sept", 18: "dix-huit", 19: "dix-neuf", 20: "vingt"}


def _is_date_or_rank(text: str, m: re.Match) -> bool:
    d = _digits(m.group(1))
    if len(d) == 4 and d.startswith(("19", "20")) and not m.group(2):
        return True  # une année
    after = text[m.end():m.end() + 3].lower()
    return after.startswith(("e ", "er", "ème", "eme", "st", "nd", "rd", "th"))  # « 1er », « 3e partie »
