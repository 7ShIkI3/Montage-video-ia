"""Traduction des sous-titres par Claude, ligne par ligne, en gardant le minutage de chaque ligne."""
from __future__ import annotations

import json

from .. import config

LANGUAGES = {
    "fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand", "it": "italien",
    "pt": "portugais", "nl": "néerlandais", "pl": "polonais", "tr": "turc", "ru": "russe",
    "ar": "arabe", "hi": "hindi", "ja": "japonais", "ko": "coréen", "zh": "chinois",
}

SYSTEM = (
    "Tu traduis des sous-titres de vidéo. Chaque élément de la liste est une ligne affichée seule à l'écran, "
    "quelques secondes. Traduis chaque ligne séparément, dans le même ordre, sans en fusionner ni en ajouter. "
    "Garde le ton parlé, les noms propres et les chiffres. Reste aussi court que l'original : la ligne doit se "
    "lire dans le même temps. Ne traduis pas ce qui est déjà dans la langue cible."
)

BATCH = 80


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
        "required": ["lines"],
        "additionalProperties": False,
    }


def translate_lines(texts: list[str], lang: str) -> dict[str, str]:
    """Traduit des lignes (texte -> traduction). Lève ValueError si Claude n'est pas configuré."""
    if lang not in LANGUAGES:
        raise ValueError(f"Langue inconnue : {lang}")
    if not texts:
        return {}
    if not config.claude_available():
        raise ValueError("La traduction des sous-titres utilise Claude : ajoute ta clé ANTHROPIC_API_KEY "
                         "dans backend/.env puis relance l'app.")
    import anthropic

    client = anthropic.Anthropic()
    out: dict[str, str] = {}
    for k in range(0, len(texts), BATCH):
        part = texts[k:k + BATCH]
        msg = (f"Langue cible : {LANGUAGES[lang]}. Réponds avec la liste `lines` de {len(part)} traductions, "
               f"dans le même ordre.\n\n" + json.dumps(part, ensure_ascii=False))
        r = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(len(part))}},
            messages=[{"role": "user", "content": msg}],
        )
        if r.stop_reason == "refusal":
            raise RuntimeError("Le modèle a refusé de traduire ces sous-titres.")
        text = next(b.text for b in r.content if b.type == "text")
        got = json.loads(text).get("lines", [])
        for src, dst in zip(part, got):
            if isinstance(dst, str) and dst.strip():
                out[src] = dst.strip()
    return out
