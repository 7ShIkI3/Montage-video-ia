"""Identité de marque tirée d'un logo ou d'un site : couleurs, police, ton, appliqués au style de la vidéo.

- Logo : les couleurs dominantes de l'image (fond transparent, blanc et noir écartés), la plus vive devient la
  couleur d'accent (mot prononcé des sous-titres, gros textes), la plus présente la couleur principale (bandeaux).
- Site : couleur de thème, couleurs les plus utilisées dans ses styles, police déclarée, et ton de ses textes
  (Claude si une clé est là, sinon des indices simples : tutoiement, points d'exclamation).
La police n'est utilisée que si elle est installée sur la machine ; sinon on garde la police par défaut.
"""
from __future__ import annotations

import colorsys
import ipaddress
import json
import logging
import re
import socket
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
from html import unescape
from pathlib import Path

import numpy as np

from .. import config
from ..models import Brand

log = logging.getLogger(__name__)

GENERIC_FONTS = {"sans-serif", "serif", "monospace", "system-ui", "cursive", "fantasy", "inherit", "initial",
                 "-apple-system", "blinkmacsystemfont", "segoe ui", "ui-sans-serif", "ui-serif", "var", "emoji",
                 "apple color emoji", "segoe ui emoji", "noto color emoji", "helvetica neue", "arial", "helvetica"}
MAX_BYTES = 2_000_000


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(max(0, min(255, round(v)))) for v in rgb))


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _sat_val(rgb) -> tuple[float, float]:
    _, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
    return s, v


def _chromatic(rgb) -> bool:
    s, v = _sat_val(rgb)
    return s > 0.18 and 0.18 < v


def text_on(rgb) -> str:
    """Couleur de texte lisible sur ce fond (blanc ou presque noir)."""
    r, g, b = rgb
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#111111" if lum > 150 else "#FFFFFF"


# ---------------------------------------------------------------- logo

def logo_colors(path: str | Path, k: int = 5) -> list[tuple[str, float]]:
    """Couleurs dominantes d'un logo : (couleur, part des pixels), de la plus présente à la moins présente."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    im.thumbnail((128, 128))
    a = np.asarray(im).reshape(-1, 4).astype(np.float32)
    px = a[a[:, 3] > 128][:, :3]
    if not len(px):
        return []
    keep = ~((px.min(axis=1) > 235) | (px.max(axis=1) < 25))  # fond blanc ou noir écarté
    if keep.sum() >= 20:
        px = px[keep]
    k = min(k, len(np.unique(px.astype(np.uint8), axis=0)))
    rng = np.random.default_rng(0)
    cent = px[rng.choice(len(px), size=k, replace=False)]
    for _ in range(12):
        lab = np.argmin(((px[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2), axis=1)
        cent = np.array([px[lab == j].mean(axis=0) if (lab == j).any() else cent[j] for j in range(k)])
    counts = np.bincount(lab, minlength=k) / len(px)
    out = sorted(((_hex(cent[j]), float(counts[j])) for j in range(k) if counts[j] > 0.02), key=lambda x: -x[1])
    merged: list[tuple[str, float]] = []
    for h, share in out:  # fusionne les teintes quasi identiques
        rgb = np.array(_rgb(h))
        twin = next((i for i, (m, _) in enumerate(merged) if np.abs(np.array(_rgb(m)) - rgb).sum() < 40), None)
        if twin is None:
            merged.append((h, share))
        else:
            merged[twin] = (merged[twin][0], merged[twin][1] + share)
    return merged


def _pick(colors: list[tuple[str, float]]) -> tuple[str, str]:
    """(principale, accent) : la teinte la plus présente, et la plus vive."""
    chrom = [(h, s) for h, s in colors if _chromatic(_rgb(h))] or colors
    primary = chrom[0][0]
    accent = max(chrom, key=lambda x: _sat_val(_rgb(x[0]))[0] * _sat_val(_rgb(x[0]))[1] * (0.4 + x[1]))[0]
    if accent == primary and len(chrom) > 1:
        alt = max(chrom[1:], key=lambda x: _sat_val(_rgb(x[0]))[0] * _sat_val(_rgb(x[0]))[1])[0]
        if _sat_val(_rgb(alt))[0] > 0.35:
            accent = alt
    return primary, accent


def from_logo(path: str | Path, name: str = "") -> Brand:
    colors = logo_colors(path)
    if not colors:
        raise ValueError("Aucune couleur lisible dans ce logo.")
    primary, accent = _pick(colors)
    return Brand(name=name or Path(path).stem, colors=[h for h, _ in colors[:4]], primary=primary, accent=accent,
                 text=text_on(_rgb(primary)), source=f"logo:{Path(path).name}")


# ---------------------------------------------------------------- site

def _check_public(url: str) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Adresse de site invalide (http ou https).")
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Ce site pointe vers une adresse locale : refusé.")


def _get(url: str, timeout: float = 10.0) -> str:
    _check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": "MonteurIA/1.0 (identite de marque)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (adresse vérifiée ci-dessus)
        _check_public(r.geturl())  # après redirection aussi
        data = r.read(MAX_BYTES)
        enc = r.headers.get_content_charset() or "utf-8"
    return data.decode(enc, errors="ignore")


HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGBF = re.compile(r"rgba?\(\s*(\d{1,3})[ ,]+(\d{1,3})[ ,]+(\d{1,3})")
FONT = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)


def _css_colors(css: str) -> Counter:
    c: Counter = Counter()
    for m in HEX.finditer(css):
        c[_hex(_rgb(m.group(0)))] += 1
    for m in RGBF.finditer(css):
        c[_hex(tuple(int(x) for x in m.groups()))] += 1
    return c


def _fonts(css: str, html: str) -> list[str]:
    out: list[str] = []
    for fam in re.findall(r"fonts\.googleapis\.com/css2?\?family=([^&\"':]+)", html):
        out.append(urllib.parse.unquote_plus(fam).split(":")[0])
    for m in FONT.finditer(css):
        for f in m.group(1).split(","):
            f = f.strip().strip("'\"").strip()
            if f and f.lower() not in GENERIC_FONTS and not f.lower().startswith("var("):
                out.append(f)
    return list(dict.fromkeys(out))


def _visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()


def tone_of(text: str) -> str:
    """Ton d'un texte, à partir d'indices simples."""
    t = f" {text.lower()} "
    tu = len(re.findall(r"\b(tu|ton|ta|tes|toi)\b", t))
    vous = len(re.findall(r"\b(vous|votre|vos)\b", t))
    excl = text.count("!") / max(1, len(text) / 400)
    parts = ["décontracté, tutoie" if tu > vous else "poli, vouvoie" if vous else "neutre"]
    parts.append("énergique" if excl > 1.2 else "sobre")
    return ", ".join(parts)


TONE_SCHEMA = {"type": "object", "properties": {"ton": {"type": "string"}, "nom": {"type": "string"}},
               "required": ["ton", "nom"], "additionalProperties": False}


def _claude_tone(text: str) -> dict | None:
    if not config.claude_available():
        return None
    try:
        import anthropic
        r = anthropic.Anthropic().beta.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=400,
            system="Décris en 6 mots au plus le ton de cette marque (en français), et donne son nom.",
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": TONE_SCHEMA}},
            messages=[{"role": "user", "content": text[:6000]}])
        if r.stop_reason not in ("refusal", "max_tokens"):
            return json.loads(next(b.text for b in r.content if b.type == "text"))
    except Exception as e:
        log.warning("Ton de marque par Claude indisponible : %s", e)
    return None


def from_html(html: str, css: str = "", url: str = "") -> Brand:
    """Identité tirée du code d'une page (et de ses feuilles de style)."""
    styles = " ".join(re.findall(r"(?is)<style[^>]*>(.*?)</style>", html)) + " " + \
        " ".join(re.findall(r"(?i)style=\"([^\"]+)\"", html)) + " " + css
    counts = _css_colors(styles)
    theme = re.search(r"(?i)<meta[^>]+name=[\"']theme-color[\"'][^>]+content=[\"']([^\"']+)", html)
    if theme and HEX.match(theme.group(1).strip()):
        counts[_hex(_rgb(theme.group(1).strip()))] += 1000  # la couleur de thème déclarée passe en tête
    colors = [(h, n) for h, n in counts.most_common(30) if _chromatic(_rgb(h))][:6]
    total = float(sum(n for _, n in colors) or 1)
    shares = [(h, n / total) for h, n in colors]
    if not shares:
        raise ValueError("Aucune couleur de marque trouvée sur ce site.")
    primary, accent = _pick(shares)
    fonts = _fonts(styles, html)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    site = re.search(r"(?i)<meta[^>]+property=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']+)", html)
    name = unescape((site or title).group(1)).strip()[:60] if (site or title) else urllib.parse.urlparse(url).hostname or ""
    text = _visible_text(html)
    ai = _claude_tone(f"{name}\n{text[:4000]}")
    font = fonts[0] if fonts else None
    return Brand(name=(ai or {}).get("nom") or name, colors=[h for h, _ in shares[:4]], primary=primary, accent=accent,
                 text=text_on(_rgb(primary)), font=font, font_installed=bool(font and font_installed(font)),
                 tone=(ai or {}).get("ton") or tone_of(text), source=f"site:{url}" if url else "site")


def from_site(url: str) -> Brand:
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    html = _get(url)
    css = ""
    for href in re.findall(r"(?i)<link[^>]+rel=[\"']stylesheet[\"'][^>]+href=[\"']([^\"']+)", html)[:3]:
        try:
            css += _get(urllib.parse.urljoin(url, unescape(href)))[:MAX_BYTES // 2]
        except Exception as e:
            log.info("Feuille de style ignorée (%s) : %s", href, e)
    return from_html(html, css, url)


# ---------------------------------------------------------------- police

def font_installed(family: str) -> bool:
    """La police est-elle installée (fontconfig) ? Sinon le rendu garde la police par défaut."""
    try:
        p = subprocess.run(["fc-match", "-f", "%{family}", family], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    return p.returncode == 0 and family.lower() in p.stdout.lower()


def ass_color(h: str, alpha: int = 0) -> str:
    """#RRGGBB -> couleur ASS &HAABBGGRR."""
    r, g, b = _rgb(h)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
