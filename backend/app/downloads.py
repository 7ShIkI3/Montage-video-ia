"""Ce que l'application peut aller chercher sur Internet, à la demande de l'utilisateur :

- une vidéo ou un son depuis un lien (YouTube, Vimeo, TikTok, un fichier direct…) avec yt-dlp, pour servir de
  rush, de vidéo de référence (« monte comme ça ») ou de musique ;
- des musiques libres de droits (Creative Commons) sur ccMixter, avec leur licence gardée à côté du fichier ;
- des modèles Whisper (transcription) et des voix Piper (voix de synthèse naturelles), pour ne pas attendre le
  premier usage.

Chaque fonction longue reçoit le `JobContext` de la tâche : avancement affiché et annulation possible.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import config
from .jobs import JobContext
from .version import __version__

log = logging.getLogger(__name__)

CCMIXTER_API = "https://ccmixter.org/api/query"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
PIPER_CATALOG = PIPER_BASE + "voices.json"
USER_AGENT = f"MonteurIA/{__version__} (+https://github.com/7ShIkI3/Montage-video-ia)"
# Tailles approximatives des modèles Whisper (Mo), pour prévenir avant de télécharger.
WHISPER_SIZES: list[tuple[str, int]] = [("tiny", 75), ("base", 145), ("small", 480), ("medium", 1500),
                                        ("large-v3-turbo", 1600), ("large-v3", 3100)]
MAX_MUSIC_BYTES = 200 * 1024 * 1024


def _http_json(url: str, timeout: float = 20.0):
    # ccMixter recopie toute la réponse JSON dans un en-tête « X-JSON » (80 Ko pour 25 morceaux) : au-delà de
    # la limite de 64 Ko par ligne d'en-tête de http.client, la lecture échoue. On relève cette limite.
    import http.client
    if http.client._MAXLINE < 4_000_000:  # type: ignore[attr-defined]
        http.client._MAXLINE = 4_000_000  # type: ignore[attr-defined]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # ccMixter renvoie parfois des octets hors UTF-8 dans les descriptions : on ne casse pas pour ça.
    return json.loads(raw.decode("utf-8", errors="replace"))


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Ne suit une redirection que si `allow(url)` la permet : un lien digne de confiance ne doit pas pouvoir
    renvoyer le téléchargement vers un autre site."""

    def __init__(self, allow: Callable[[str], bool]):
        self._allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not self._allow(target):
            raise urllib.error.URLError(f"redirection refusée vers {urllib.parse.urlparse(target).netloc or target}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req: urllib.request.Request, timeout: float, allow_redirect: Callable[[str], bool] | None):
    """urlopen, avec un contrôle des redirections quand `allow_redirect` est donné (sinon comportement habituel)."""
    if allow_redirect is None:
        return urllib.request.urlopen(req, timeout=timeout)
    handlers: list = [_GuardedRedirect(allow_redirect)]
    if (urllib.parse.urlparse(req.full_url).hostname or "") in ("127.0.0.1", "localhost"):
        handlers.append(urllib.request.ProxyHandler({}))  # un essai en local ne passe pas par le proxy du système
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def _http_download(url: str, dst: Path, ctx: JobContext | None, label: str, lo: float = 0.0, hi: float = 1.0,
                   max_bytes: int | None = None, allow_redirect: Callable[[str], bool] | None = None,
                   expected_size: int | None = None) -> Path:
    """Télécharge `url` dans `dst` (fichier temporaire puis renommage), avec avancement et annulation.

    `allow_redirect(url)` : si donné, une redirection vers une adresse refusée interrompt le téléchargement.
    `expected_size` : si donné, la taille annoncée et la taille reçue doivent lui être égales."""
    # ccMixter (et d'autres) refusent un téléchargement sans Referer : on donne l'origine du lien.
    u = urllib.parse.urlparse(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": f"{u.scheme}://{u.netloc}/"})
    tmp = dst.with_suffix(dst.suffix + ".part")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open(req, 60, allow_redirect) as r, tmp.open("wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            if expected_size is not None and total and total != expected_size:
                raise ValueError("La taille annoncée par le serveur ne correspond pas à celle de la version publiée")
            if max_bytes and total > max_bytes:
                raise ValueError(f"Fichier trop gros ({total // 1_000_000} Mo)")
            done = 0
            last = 0.0
            while chunk := r.read(1024 * 256):
                out.write(chunk)
                done += len(chunk)
                if max_bytes and done > max_bytes:
                    raise ValueError("Fichier trop gros")
                if ctx and time.time() - last > 0.3:
                    last = time.time()
                    frac = done / total if total else 0.0
                    ctx.step(f"{label} : {done / 1e6:.1f}{f' / {total / 1e6:.0f}' if total else ''} Mo", lo + (hi - lo) * frac)
        if expected_size is not None and done != expected_size:
            raise ValueError(f"Téléchargement incomplet ({done} octets reçus sur {expected_size})")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def _safe_stem(name: str, limit: int = 80) -> str:
    s = "".join(ch for ch in name if ch.isalnum() or ch in " ._-()'&,").strip(" .")
    return (s or "telechargement")[:limit]


# --- Vidéo / son depuis un lien (yt-dlp) ----------------------------------------------------------

def _ffmpeg_dir() -> str | None:
    from .media import ffmpeg
    exe = shutil.which(ffmpeg.FFMPEG) or ffmpeg.FFMPEG
    p = Path(exe)
    return str(p.parent) if p.is_absolute() and p.parent.is_dir() else None


def fetch_url(url: str, dst_dir: Path, ctx: JobContext | None = None, audio: bool = False, max_height: int = 1080) -> Path:
    """Télécharge la vidéo (MP4, ≤ max_height) ou le son (MP3) d'un lien dans `dst_dir`. Renvoie le fichier."""
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover - dépendance déclarée
        raise RuntimeError("yt-dlp n'est pas installé") from e
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise ValueError("Lien invalide : il doit commencer par http:// ou https://")
    dst_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in dst_dir.iterdir()}
    last = [0.0]

    def hook(d: dict) -> None:
        if ctx is None:
            return
        ctx.check()
        if d.get("status") == "downloading" and time.time() - last[0] > 0.3:
            last[0] = time.time()
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            frac = got / total if total else 0.0
            ctx.step(f"Téléchargement : {got / 1e6:.1f}{f' / {total / 1e6:.0f}' if total else ''} Mo", 0.05 + 0.85 * frac)
        elif d.get("status") == "finished":
            ctx.step("Téléchargé, assemblage du fichier", 0.92)

    opts: dict = {
        "outtmpl": str(dst_dir / "%(title).80B.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True, "progress_hooks": [hook],
        "windowsfilenames": True, "restrictfilenames": False, "overwrites": True,
        "retries": 3, "socket_timeout": 30,
    }
    ff = _ffmpeg_dir()
    if ff:
        opts["ffmpeg_location"] = ff
    if audio:
        opts["format"] = "ba/b"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        opts["format"] = (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}][ext=mp4]"
                          f"/bv*[height<={max_height}]+ba/b")
        opts["merge_output_format"] = "mp4"
    if ctx:
        ctx.step("Lecture du lien", 0.02)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    new = sorted((p for p in dst_dir.iterdir() if p not in before and p.is_file() and not p.name.endswith(".part")),
                 key=lambda p: p.stat().st_mtime)
    if not new:
        raise RuntimeError("Le téléchargement n'a produit aucun fichier")
    out = new[-1]
    title = (info or {}).get("title") if isinstance(info, dict) else None
    if title:
        wanted = out.with_name(_safe_stem(title) + out.suffix)
        # Sous Windows, Path compare sans la casse et un nom qui n'en diffère que par elle « existe déjà » :
        # on compare les chaînes et on accepte le renommage quand c'est le même fichier.
        if wanted.name != out.name and (not wanted.exists() or wanted.samefile(out)):
            out.rename(wanted)
            out = wanted
    if ctx:
        ctx.step(f"Prêt : {out.name}", 0.95)
    return out


# --- Musiques libres (ccMixter) -------------------------------------------------------------------

def _parse_ccmixter(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        files = [f for f in r.get("files", []) if (f.get("file_format_info") or {}).get("media-type") == "audio"
                 and f.get("download_url")]
        if not files:
            continue
        f = files[0]
        info = f.get("file_format_info") or {}
        extra = r.get("upload_extra") or {}
        out.append({
            "id": str(r.get("upload_id")),
            "title": r.get("upload_name") or "Sans titre",
            "artist": r.get("user_real_name") or r.get("user_name") or "",
            "license": r.get("license_name") or "",
            "license_url": r.get("license_url") or "",
            "page": r.get("file_page_url") or "",
            "download_url": f["download_url"],
            "duration": info.get("ps") or "",
            "bpm": extra.get("bpm") or None,
            "tags": [t for t in (extra.get("usertags") or "").split(",") if t],
            "size": (f.get("file_filesize") or "").strip(" ()"),
            "filename": f.get("file_name") or "",
        })
    return out


def search_music(query: str = "", limit: int = 20, instrumental: bool = True) -> list[dict]:
    """Musiques Creative Commons sur ccMixter (sans clé). `query` : mots-clés (ambiance, genre) ou vide."""
    params = {"f": "json", "limit": str(max(1, min(30, limit))), "sort": "rank"}  # la vue par défaut inclut licence et fichiers
    tags = ["instrumental"] if instrumental else []
    q = query.strip()
    if q:
        params["search"] = q
        params["search_type"] = "any"  # « match » exige la phrase exacte et ne trouve presque rien
    if tags:
        params["tags"] = ",".join(tags)
    rows = _http_json(CCMIXTER_API + "?" + urllib.parse.urlencode(params))
    return _parse_ccmixter(rows if isinstance(rows, list) else [])


def download_music(item: dict, ctx: JobContext | None = None) -> Path:
    """Enregistre un morceau ccMixter dans la bibliothèque, avec sa licence dans un fichier .json à côté."""
    url = str(item.get("download_url") or "")
    if not url.startswith("http"):
        raise ValueError("Lien de téléchargement manquant")
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".mp3"
    stem = _safe_stem(f"{item.get('artist') or 'ccMixter'} - {item.get('title') or 'morceau'}")
    dst = config.MUSIC_DIR / f"{stem}{ext}"
    i = 1
    while dst.exists():
        dst = config.MUSIC_DIR / f"{stem} ({i}){ext}"
        i += 1
    _http_download(url, dst, ctx, "Musique", 0.05, 0.95, MAX_MUSIC_BYTES)
    meta = {k: item.get(k) for k in ("id", "title", "artist", "license", "license_url", "page", "bpm", "tags")}
    meta["source"] = "ccMixter"
    meta["downloaded_at"] = time.time()
    dst.with_suffix(dst.suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if ctx:
        ctx.step(f"Ajouté à la bibliothèque : {dst.name}", 1.0)
    return dst


def music_from_url(url: str, ctx: JobContext | None = None) -> Path:
    """Son d'un lien (YouTube…) en MP3 dans la bibliothèque. L'utilisateur reste responsable des droits."""
    out = fetch_url(url, config.MUSIC_DIR, ctx, audio=True)
    meta = {"source": "lien", "url": url.strip(), "downloaded_at": time.time(),
            "license": "inconnue : vérifie les droits avant de publier"}
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def library_info(name: str) -> dict | None:
    """Licence et origine d'un morceau de la bibliothèque, si on les a."""
    p = config.MUSIC_DIR / (name + ".json")
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Modèles Whisper ------------------------------------------------------------------------------

def _whisper_cached(size: str) -> bool:
    try:
        from faster_whisper.utils import download_model
        download_model(size, local_files_only=True)
        return True
    except Exception:
        return False


def whisper_models() -> list[dict]:
    return [{"size": s, "approx_mb": mb, "downloaded": _whisper_cached(s), "current": s == config.WHISPER_MODEL}
            for s, mb in WHISPER_SIZES]


def download_whisper(size: str, ctx: JobContext | None = None) -> str:
    if size not in {s for s, _ in WHISPER_SIZES}:
        raise ValueError(f"Modèle inconnu : {size}")
    from faster_whisper.utils import download_model
    if ctx:
        ctx.step(f"Téléchargement de Whisper {size} (Hugging Face)…", 0.05)
    path = download_model(size)
    if ctx:
        ctx.step(f"Whisper {size} prêt", 1.0)
    return str(path)


# --- Voix Piper -----------------------------------------------------------------------------------

def _catalog_path() -> Path:
    return config.DATA_DIR / "cache" / "piper-voices.json"


def piper_catalog(lang: str | None = None, refresh: bool = False) -> list[dict]:
    """Voix disponibles sur Hugging Face (catalogue gardé 24 h), filtrées par famille de langue (fr, en…)."""
    p = _catalog_path()
    data = None
    if not refresh and p.is_file() and time.time() - p.stat().st_mtime < 86400:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if data is None:
        data = _http_json(PIPER_CATALOG, timeout=30)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    return parse_piper_catalog(data, lang)


def parse_piper_catalog(data: dict, lang: str | None) -> list[dict]:
    installed = {v.name for v in installed_piper_voices()}
    out = []
    for key, v in data.items():
        fam = (v.get("language") or {}).get("family", "")
        if lang and fam != lang.lower():
            continue
        files = v.get("files") or {}
        names = list(files.keys()) if isinstance(files, dict) else list(files)
        onnx = next((f for f in names if f.endswith(".onnx")), None)
        cfg = next((f for f in names if f.endswith(".onnx.json")), None)
        if not onnx or not cfg:
            continue
        size = (files.get(onnx) or {}).get("size_bytes") if isinstance(files, dict) else None
        out.append({"key": key, "name": v.get("name"), "quality": v.get("quality"), "lang": fam,
                    "lang_name": (v.get("language") or {}).get("name_english"), "region": (v.get("language") or {}).get("country_english"),
                    "speakers": v.get("num_speakers", 1), "onnx": onnx, "config": cfg,
                    "approx_mb": round(size / 1e6) if size else None, "installed": f"{key}.onnx" in installed})
    order = {"x_low": 0, "low": 1, "medium": 2, "high": 3}
    out.sort(key=lambda x: (x["lang"], x["name"] or "", order.get(x["quality"] or "", 9)))
    return out


def installed_piper_voices() -> list[Path]:
    from .ai import tts
    d = tts.voices_dir()
    return sorted(d.glob("*.onnx")) if d.is_dir() else []


def download_piper(key: str, ctx: JobContext | None = None) -> Path:
    voices = {v["key"]: v for v in piper_catalog(None)}
    v = voices.get(key)
    if not v:
        raise ValueError(f"Voix inconnue : {key}")
    from .ai import tts
    d = tts.voices_dir()
    d.mkdir(parents=True, exist_ok=True)
    onnx = d / f"{key}.onnx"
    _http_download(PIPER_BASE + v["config"], onnx.with_suffix(".onnx.json"), ctx, "Configuration", 0.0, 0.05)
    _http_download(PIPER_BASE + v["onnx"], onnx, ctx, f"Voix {v['name']}", 0.05, 0.98)
    if ctx:
        ctx.step(f"Voix {key} installée", 1.0)
    return onnx


def piper_runtime() -> bool:
    from .ai import tts
    return tts.piper_available()
