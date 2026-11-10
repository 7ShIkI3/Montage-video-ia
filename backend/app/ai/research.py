"""Recherche d'informations sur le web pour préparer un montage : faits, chiffres, règles, contexte.

Avec une clé Claude, le modèle cherche lui-même (outil de recherche web d'Anthropic) et répond en citant ses
sources. Sans clé, on interroge DuckDuckGo (version HTML, sans clé) et on rend les premiers résultats avec leur
extrait : moins synthétique, mais utile. Les notes sont gardées dans le projet et données au réalisateur
(montage automatique, consignes) pour qu'il s'appuie dessus.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request

from .. import config

log = logging.getLogger(__name__)

RESEARCH_SYSTEM = (
    "Tu es l'assistant documentaire d'un monteur vidéo. Il prépare une vidéo et a besoin de faits fiables. "
    "Cherche sur le web, puis réponds en français, en 5 à 12 phrases courtes, factuelles, datées et chiffrées "
    "quand c'est possible. Cite tes sources. Termine par 2 ou 3 idées utilisables dans la vidéo (chiffre choc, "
    "fait surprenant, formulation d'accroche). Si tu ne trouves pas, dis-le au lieu d'inventer. Écris tout en "
    "français : reformule ce que disent les sources, sans recopier de fragments dans une autre langue.\n"
    "Tu reçois aussi le dossier du projet : ce que l'app a mesuré dans ses rushes (coups au son, combos, écrans "
    "rouges, phrases horodatées, montage actuel). Quand la question porte sur ses rushes, réponds avec ces "
    "horodatages (en disant « probable » pour ce que l'app ne mesure pas, le dossier le précise) au lieu de dire "
    "que tu ne peux pas voir la vidéo ; le chat « Réalisateur » peut ensuite appliquer les changements."
)
DDG_URL = "https://html.duckduckgo.com/html/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
MAX_SOURCES = 10


def research(question: str, context: str = "", ctx=None) -> dict:
    """{"answer": str, "sources": [{"title", "url"}], "by": "claude" | "local"}."""
    q = question.strip()
    if not q:
        raise ValueError("Question vide")
    if config.claude_available():
        try:
            if ctx:
                ctx.step("Claude cherche sur le web", 0.2)
            return _claude(q, context)
        except Exception as e:
            log.warning("Recherche Claude impossible, repli DuckDuckGo : %s", e)
    if ctx:
        ctx.step("Recherche DuckDuckGo", 0.3)
    return _local(q)


def _claude(question: str, context: str) -> dict:
    import anthropic
    from .chat import web_search_tool
    client = anthropic.Anthropic()
    user = (f"DOSSIER DU PROJET :\n{context.strip()}\n\n" if context.strip() else "") + f"Question : {question}"
    r = client.beta.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=4000, system=RESEARCH_SYSTEM,
        tools=[web_search_tool(5)],
        messages=[{"role": "user", "content": user}],
    )
    return parse_claude_response(r)


def parse_claude_response(r) -> dict:
    """Texte et sources d'une réponse avec recherche web : citations d'abord, puis pages consultées."""
    text_parts: list[str] = []
    cited: list[dict] = []
    seen_pages: list[dict] = []
    for b in r.content:
        kind = getattr(b, "type", "")
        if kind == "text":
            text_parts.append(getattr(b, "text", "") or "")
            for c in getattr(b, "citations", None) or []:
                if getattr(c, "type", "") == "web_search_result_location":
                    cited.append({"title": getattr(c, "title", "") or "", "url": getattr(c, "url", "") or ""})
        elif kind == "web_search_tool_result":
            content = getattr(b, "content", None) or []
            for item in content if isinstance(content, list) else []:
                if getattr(item, "type", "") == "web_search_result":
                    seen_pages.append({"title": getattr(item, "title", "") or "", "url": getattr(item, "url", "") or ""})
    if getattr(r, "stop_reason", "") == "refusal":
        raise RuntimeError("Le modèle a refusé la demande.")
    sources: list[dict] = []
    urls: set[str] = set()
    for s in cited + seen_pages:
        if s["url"] and s["url"] not in urls:
            urls.add(s["url"])
            sources.append(s)
    answer = "".join(text_parts).strip()
    if not answer:
        raise RuntimeError("Réponse vide")
    return {"answer": answer, "sources": sources[:MAX_SOURCES], "by": "claude"}


# --- Repli sans clé : DuckDuckGo HTML ---------------------------------------------------------------

_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return html.unescape(_TAGS.sub("", s)).strip()


def _real_url(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    u = urllib.parse.urlparse(href)
    if "duckduckgo.com" in u.netloc and u.path.startswith("/l/"):
        target = urllib.parse.parse_qs(u.query).get("uddg", [""])[0]
        return target or href
    return href


def parse_ddg(page: str, limit: int = 6) -> list[dict]:
    links = _RESULT.findall(page)
    snippets = [_clean(s) for s in _SNIPPET.findall(page)]
    out = []
    for i, (href, title) in enumerate(links[:limit]):
        url = _real_url(href)
        if not url.startswith("http"):
            continue
        out.append({"title": _clean(title), "url": url, "snippet": snippets[i] if i < len(snippets) else ""})
    return out


def ddg_search(query: str, limit: int = 6) -> list[dict]:
    req = urllib.request.Request(DDG_URL + "?" + urllib.parse.urlencode({"q": query}),
                                 headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6"})
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode("utf-8", errors="replace")
    return parse_ddg(page, limit)


def _local(question: str) -> dict:
    results = ddg_search(question)
    if not results:
        return {"answer": "Rien trouvé sur DuckDuckGo pour cette question. Reformule, ou ajoute une clé Claude dans "
                          "les réglages pour une recherche avec synthèse.", "sources": [], "by": "local"}
    lines = [f"- {r['title']} : {r['snippet']}" if r["snippet"] else f"- {r['title']}" for r in results]
    answer = ("Résultats DuckDuckGo (sans synthèse : ajoute une clé Claude dans les réglages pour une réponse "
              "rédigée et sourcée) :\n" + "\n".join(lines))
    return {"answer": answer, "sources": [{"title": r["title"], "url": r["url"]} for r in results], "by": "local"}


def notes_as_instructions(notes: list, max_notes: int = 4, max_chars: int = 1800) -> str:
    """Les notes de recherche du projet, en consigne pour le réalisateur (les plus récentes d'abord)."""
    if not notes:
        return ""
    out = ["Notes de recherche (faits trouvés sur le web, à utiliser sans les déformer) :"]
    used = 0
    for n in list(notes)[-max_notes:][::-1]:
        line = f"- {n.question.strip()} → {n.answer.strip()}"
        if used + len(line) > max_chars:
            line = line[: max(0, max_chars - used)].rstrip() + "…"
        out.append(line)
        used += len(line)
        if used >= max_chars:
            break
    return "\n".join(out)
