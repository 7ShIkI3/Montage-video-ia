"""Mises à jour de Monteur IA : prévenir, puis télécharger et installer sur un clic de l'utilisateur.

Rien n'est téléchargé ni installé sans clic. Au lancement, l'interface demande `GET /api/update` : l'application
interroge GitHub (flux « releases/latest » du dépôt public, sans jeton, sans aucune donnée de l'utilisateur) et
compare la version publiée à la sienne. Une release de Monteur IA contient :

- un tag « vX.Y.Z » ;
- un fichier « MonteurIA-Setup-X.Y.Z.exe » (l'installateur Inno Setup) ;
- un fichier « SHA256SUMS.txt » (lignes « <empreinte sha256 en hexa>  <nom du fichier> »).

Sécurité : l'installateur n'est jamais lancé sans que son empreinte SHA-256 ait été lue dans SHA256SUMS.txt de la
release, retrouvée sur le fichier reçu, puis revérifiée juste avant l'installation. Les adresses viennent du
réseau : le fichier doit se trouver sur github.com sous /7ShIkI3/Montage-video-ia/releases/download/, les
redirections ne mènent que vers GitHub, et aucun nom reçu ne sert à construire un chemin (le nom local est refait
à partir de trois entiers).

Réglages : `MONTEUR_UPDATE_URL` pointe vers un autre flux (essais ; http seulement vers 127.0.0.1 ou localhost),
`MONTEUR_UPDATE_CHECK=0` coupe tout, et le réglage `check_updates` coupe la vérification automatique.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config, downloads, lifecycle, settings, storage
from .jobs import ACTIVE, JobContext, manager
from .version import __version__

log = logging.getLogger(__name__)

REPO = "7ShIkI3/Montage-video-ia"
DEFAULT_FEED = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PATH = f"/{REPO}/releases/download/"
SUMS_NAME = "SHA256SUMS.txt"

TTL = 6 * 3600.0  # une vérification réussie reste valable 6 h (l'API anonyme de GitHub : 60 requêtes par heure)
ERROR_TTL = 300.0  # une panne n'est pas retentée avant 5 min, sauf clic sur « Vérifier »
FEED_TIMEOUT = 8.0
MAX_FEED_BYTES = 1_000_000
MAX_SUMS_BYTES = 64_000
MAX_NOTES = 4000
MAX_INSTALLER_BYTES = 4 * 1024 ** 3
DISK_MARGIN = 200 * 1024 ** 2  # place à garder libre en plus de l'installateur
EXIT_DELAY = 1.0  # secondes entre la réponse « ok » et la fermeture de l'application
UPDATE_PID = "_mise_a_jour"  # « projet » des tâches de mise à jour : elles n'attendent pas derrière un modèle Whisper

LOCAL_HOSTS = ("127.0.0.1", "localhost")
FEED_HOSTS = ("api.github.com", "github.com")
DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")

TAG_RE = re.compile(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
VERSION_RE = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
ASSET_RE = re.compile(r"MonteurIA-Setup-(\d{1,4})\.(\d{1,4})\.(\d{1,4})\.exe")
SHA_RE = re.compile(r"[0-9a-f]{64}")
SUMS_LINE = re.compile(r"([0-9a-fA-F]{64})[ \t]+\*?(.+?)[ \t]*")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

Version = tuple[int, int, int]


class UpdateError(Exception):
    """Une mise à jour ne peut pas se faire ; le message est en français, destiné à l'utilisateur."""

    def __init__(self, message: str, status: int = 409, transient: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.transient = transient  # panne passagère (réseau, 5xx, limite de GitHub) : à retenter vite, sans la garder 6 h


# --- Versions ----------------------------------------------------------------------------------

def parse_version(text: object) -> Version | None:
    """« 1.2.3 » ou « v1.2.3 » -> (1, 2, 3). Tout le reste (préversion « 1.2.3-beta », « 1.2 »…) -> None."""
    if not isinstance(text, str):
        return None
    m = VERSION_RE.fullmatch(text.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fmt_version(v: Version) -> str:
    return ".".join(str(n) for n in v)


def installer_name(v: Version) -> str:
    return f"MonteurIA-Setup-{fmt_version(v)}.exe"


def current_version() -> Version:
    return parse_version(__version__) or (0, 0, 0)


# --- Adresses autorisées -----------------------------------------------------------------------

def _scheme_ok(u: urllib.parse.ParseResult) -> bool:
    return u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in LOCAL_HOSTS)


def _custom_netloc() -> str | None:
    """Hôte (avec port) du flux d'essai MONTEUR_UPDATE_URL, accepté en plus de GitHub. None si la variable est vide."""
    raw = os.environ.get("MONTEUR_UPDATE_URL", "").strip()
    if not raw:
        return None
    u = urllib.parse.urlparse(raw)
    if not u.netloc or "@" in u.netloc or not _scheme_ok(u):
        raise UpdateError("MONTEUR_UPDATE_URL n'est pas une adresse acceptée (https, ou http vers 127.0.0.1 ou localhost).")
    return u.netloc.lower()


def _safe_custom() -> str | None:
    try:
        return _custom_netloc()
    except UpdateError:
        return None


def feed_url() -> str:
    return os.environ.get("MONTEUR_UPDATE_URL", "").strip() or DEFAULT_FEED


def asset_url_ok(url: object) -> bool:
    """Première adresse d'un fichier de release : github.com sous /7ShIkI3/Montage-video-ia/releases/download/
    (ou l'hôte du flux d'essai)."""
    if not isinstance(url, str) or len(url) > 2000:
        return False
    u = urllib.parse.urlparse(url)
    if not _scheme_ok(u) or "@" in u.netloc:
        return False
    if u.netloc.lower() == _safe_custom():
        return True
    path = urllib.parse.unquote(u.path)
    return (u.scheme == "https" and u.netloc.lower() == "github.com" and path.startswith(RELEASE_PATH)
            and ".." not in path.split("/") and not u.fragment)


def redirect_ok(url: str, hosts: tuple[str, ...]) -> bool:
    """Une redirection ne mène que vers l'un des `hosts` (en https), ou vers l'hôte du flux d'essai."""
    u = urllib.parse.urlparse(url)
    if not _scheme_ok(u) or "@" in u.netloc:
        return False
    host = u.netloc.lower()
    return host == _safe_custom() or (u.scheme == "https" and host in hosts)


# --- Lecture du flux ---------------------------------------------------------------------------

def _fetch(url: str, limit: int, hosts: tuple[str, ...]) -> bytes:
    """Lit une réponse de GitHub (taille plafonnée à `limit`). Lève UpdateError, en français, sur toute panne."""
    req = urllib.request.Request(url, headers={"User-Agent": f"MonteurIA/{__version__}", "Accept": "application/json"})
    try:
        with downloads._open(req, FEED_TIMEOUT, lambda target: redirect_ok(target, hosts)) as r:
            if int(r.headers.get("Content-Length") or 0) > limit:
                raise UpdateError("La réponse de GitHub est trop grosse : elle est ignorée.")
            raw = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("Aucune version publiée n'a été trouvée sur GitHub.") from None
        if e.code in (403, 429):
            raise UpdateError("GitHub limite les vérifications pour le moment : réessaie dans une heure.", transient=True) from None
        raise UpdateError(f"GitHub a répondu une erreur ({e.code}).", transient=e.code >= 500) from None
    except UpdateError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as e:  # URLError, délai dépassé, coupure
        reason = getattr(e, "reason", e)
        if "redirection refusée" in str(reason):
            raise UpdateError("GitHub a renvoyé vers une adresse non autorisée : vérification annulée.") from None
        raise UpdateError("GitHub est injoignable (vérifie ta connexion Internet).", transient=True) from None
    if len(raw) > limit:
        raise UpdateError("La réponse de GitHub est trop grosse : elle est ignorée.")
    return raw


def parse_sums(text: str, filename: str) -> str | None:
    """Empreinte de `filename` dans un SHA256SUMS.txt. None si absente ou ambiguë (deux empreintes différentes)."""
    found: set[str] = set()
    for line in text.splitlines():
        m = SUMS_LINE.fullmatch(line.strip())
        if m and m.group(2) == filename:
            found.add(m.group(1).lower())
    return found.pop() if len(found) == 1 else None


def _clean_notes(body: object) -> str:
    if not isinstance(body, str):
        return ""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    return _plain(text).strip()[:MAX_NOTES]


def _plain(text: str) -> str:
    """Markdown des notes -> texte brut lisible : ni **gras**, ni `code`, ni # titres ; les puces « - » deviennent « • »."""
    text = re.sub(r"\[([^\]\n]+)\]\([^)\n]*\)", r"\1", text)  # [texte](lien) -> texte
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = text.replace("`", "")
    text = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", text, flags=re.M)
    return re.sub(r"^([ \t]*)[-*+][ \t]+", r"\1• ", text, flags=re.M)


def _page_url(data: dict, tag: str) -> str | None:
    custom = _safe_custom()
    if custom:
        u = urllib.parse.urlparse(str(data.get("html_url") or ""))
        return str(data["html_url"]) if _scheme_ok(u) and u.netloc.lower() == custom else None
    return f"https://github.com/{REPO}/releases/tag/{tag}"


def parse_release(data: object) -> dict:
    """Réponse de l'API (« release ») -> informations sûres et JSON. Lève UpdateError si le tag est inattendu."""
    if not isinstance(data, dict):
        raise UpdateError("La réponse de GitHub est illisible.")
    empty = {"latest": None, "notes": "", "published_at": None, "size": None, "page_url": None,
             "installer_url": None, "sha256": None, "problem": None}
    if data.get("draft") or data.get("prerelease"):
        return empty
    tag = data.get("tag_name")
    m = TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    if not m:
        raise UpdateError("La dernière version publiée a un nom inattendu : l'application ne la propose pas.")
    version: Version = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    name = installer_name(version)  # le seul nom d'installateur accepté : refait à partir des trois entiers du tag
    published = data.get("published_at")
    rel = dict(empty, latest=fmt_version(version), notes=_clean_notes(data.get("body")),
               published_at=published if isinstance(published, str) and DATE_RE.fullmatch(published) else None,
               page_url=_page_url(data, f"v{fmt_version(version)}"))
    installer = sums_url = None
    digest = None
    for a in data.get("assets") if isinstance(data.get("assets"), list) else []:
        if not isinstance(a, dict):
            continue
        if a.get("name") == name and installer is None:
            size = a.get("size")
            if (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_INSTALLER_BYTES
                    and asset_url_ok(a.get("browser_download_url"))):
                installer = a
                d = str(a.get("digest") or "").lower()
                digest = d[7:] if d.startswith("sha256:") and SHA_RE.fullmatch(d[7:]) else None
        elif a.get("name") == SUMS_NAME and sums_url is None and asset_url_ok(a.get("browser_download_url")):
            sums_url = a["browser_download_url"]
    if installer is None:
        return rel  # release sans installateur (ou fichier douteux) : on prévient, on n'installe pas
    rel.update(size=installer["size"], installer_url=installer["browser_download_url"])
    if version <= current_version():
        return rel  # déjà à jour : inutile de chercher l'empreinte
    if sums_url is None:
        rel["problem"] = f"La version {rel['latest']} n'a pas de fichier {SUMS_NAME} : elle ne peut pas être installée automatiquement."
        return rel
    try:
        text = _fetch(sums_url, MAX_SUMS_BYTES, DOWNLOAD_HOSTS).decode("utf-8", errors="replace")
    except UpdateError as e:
        if e.transient:
            raise  # panne passagère : _check la traite comme une erreur (retentée dans 5 min), pas comme un résultat gardé 6 h
        rel["problem"] = f"{SUMS_NAME} n'a pas pu être lu : {e.message}"
        return rel
    sha = parse_sums(text, name)
    if sha is None:
        rel["problem"] = f"{SUMS_NAME} ne donne pas d'empreinte claire pour {name} : mise à jour automatique refusée."
    elif digest is not None and digest != sha:
        rel["problem"] = "L'empreinte annoncée par GitHub ne correspond pas à celle de la version : mise à jour refusée."
    else:
        rel["sha256"] = sha
    return rel


# --- Cache : mémoire + petit fichier dans le dossier de données ---------------------------------

_lock = threading.RLock()
_mem: dict | None = None  # {"at": dernier essai, "ok_at": dernier succès, "release": dict | None, "error": str | None}


def _now() -> float:
    return time.time()


def _cache_path() -> Path:
    return config.DATA_DIR / "update_cache.json"


def _load_disk() -> dict | None:
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        if (raw.get("app_version") == __version__ and raw.get("feed") == feed_url() and isinstance(raw.get("release"), dict) and isinstance(raw.get("ok_at"), (int, float))):
            return {"at": float(raw["ok_at"]), "ok_at": float(raw["ok_at"]), "release": raw["release"], "error": None}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _save_disk(c: dict) -> None:
    try:
        storage._write_json(_cache_path(), json.dumps({"app_version": __version__, "feed": feed_url(), "ok_at": c["ok_at"], "release": c["release"]}))
    except OSError:
        log.warning("Cache des mises à jour non écrit", exc_info=True)


def reset_cache() -> None:
    """Oublie le résultat gardé (mémoire et fichier). Pour les tests."""
    global _mem
    with _lock:
        _mem = None
        _cache_path().unlink(missing_ok=True)


def _check(force: bool) -> tuple[dict | None, float | None, str | None]:
    """(release, date du dernier succès, erreur). Interroge GitHub seulement si le résultat gardé est périmé."""
    global _mem
    with _lock:
        now = _now()
        c = _mem if _mem is not None else (None if force else _load_disk())
        if c is not None and not force and now - c["at"] < (ERROR_TTL if c["error"] else TTL):
            _mem = c
            return c["release"], c["ok_at"], c["error"]
        try:
            _custom_netloc()  # variable d'essai invalide : on le dit avant toute requête
            rel = parse_release(json.loads(_fetch(feed_url(), MAX_FEED_BYTES, FEED_HOSTS).decode("utf-8", errors="replace")))
            _mem = {"at": now, "ok_at": now, "release": rel, "error": None}
            _save_disk(_mem)
        except (UpdateError, ValueError) as e:  # ValueError : JSON illisible
            msg = e.message if isinstance(e, UpdateError) else "La réponse de GitHub est illisible."
            prev = c or {}
            _mem = {"at": now, "ok_at": prev.get("ok_at"), "release": prev.get("release"), "error": msg}
        return _mem["release"], _mem["ok_at"], _mem["error"]


# --- Le dossier des installateurs ----------------------------------------------------------------

def updates_dir() -> Path:
    return config.DATA_DIR / "updates"


def installer_path(v: Version) -> Path:
    return updates_dir() / installer_name(v)


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def sha256_file(path: Path, ctx: JobContext | None = None, lo: float = 0.0, hi: float = 1.0, label: str = "") -> str:
    h = hashlib.sha256()
    total = max(path.stat().st_size, 1)
    done, last = 0, 0.0
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
            done += len(chunk)
            if ctx and time.time() - last > 0.3:
                last = time.time()
                ctx.step(label, lo + (hi - lo) * done / total)
    return h.hexdigest()


def _downloaded(rel: dict | None) -> bool:
    """L'installateur de cette version est là, à la bonne taille, et son empreinte vérifiée a été notée."""
    v = parse_version(rel.get("latest")) if rel else None
    if not rel or not v or not rel.get("sha256"):
        return False
    p = installer_path(v)
    try:
        return p.is_file() and p.stat().st_size == rel.get("size") and _sidecar(p).read_text(encoding="utf-8").strip() == rel["sha256"]
    except OSError:
        return False


def cleanup_old() -> list[str]:
    """Au démarrage : supprime les installateurs des versions inférieures ou égales à la version courante, et les
    téléchargements interrompus (.part, .download). Renvoie les noms supprimés."""
    removed: list[str] = []
    d = updates_dir()
    if not d.is_dir():
        return removed
    cur = current_version()
    for f in d.iterdir():
        if not f.is_file():
            continue
        base = f.name[:-len(".sha256")] if f.name.endswith(".sha256") else f.name
        m = ASSET_RE.fullmatch(base)
        stale = f.name.endswith((".part", ".download")) or (m is not None and (int(m[1]), int(m[2]), int(m[3])) <= cur)
        if stale:
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                log.warning("Fichier de mise à jour non supprimé : %s", f.name)
    return removed


# --- État pour l'interface -----------------------------------------------------------------------

def _can_self_install() -> bool:
    """Vrai seulement dans l'application Windows installée (exécutable PyInstaller)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _env_off() -> bool:
    return os.environ.get("MONTEUR_UPDATE_CHECK", "").strip() == "0"


def status(force: bool = False) -> dict:
    """Contrat de GET /api/update. Ne lève jamais : une panne réseau remplit `error`."""
    try:
        return _status(force)
    except Exception:  # un bogue ici ne doit pas empêcher l'application de démarrer
        log.exception("Vérification des mises à jour impossible")
        return {"current": __version__, "latest": None, "available": False, "notes": "", "published_at": None,
                "size": None, "page_url": None, "installable": False, "downloaded": False, "checked_at": None,
                "enabled": True, "error": "La vérification des mises à jour a échoué."}


def _status(force: bool) -> dict:
    enabled = bool(settings.public().get("check_updates", True)) and not _env_off()
    rel = ok_at = err = None
    if _env_off():
        err = "Les mises à jour sont coupées (MONTEUR_UPDATE_CHECK=0)." if force else None
    elif enabled or force:
        rel, ok_at, err = _check(force)
    latest = parse_version(rel.get("latest")) if rel else None
    available = bool(latest and latest > current_version())
    complete = bool(rel and asset_url_ok(rel.get("installer_url")) and rel.get("sha256") and rel.get("size"))
    problem = rel.get("problem") if rel and available else None
    return {
        "current": __version__,
        "latest": rel.get("latest") if rel else None,
        "available": available,
        "notes": rel.get("notes", "") if rel else "",
        "published_at": rel.get("published_at") if rel else None,
        "size": rel.get("size") if rel else None,
        "page_url": rel.get("page_url") if rel else None,
        "installable": complete and _can_self_install(),
        "downloaded": available and _downloaded(rel),
        "checked_at": _iso(ok_at),
        "enabled": enabled,
        "error": err or problem,
    }


def _need_release() -> dict:
    """La release à installer, ou UpdateError (409) qui dit pourquoi ce n'est pas possible."""
    if _env_off():
        raise UpdateError("Les mises à jour sont coupées (MONTEUR_UPDATE_CHECK=0).")
    rel, _, err = _check(False)
    latest = parse_version(rel.get("latest")) if rel else None
    if rel is None or latest is None:
        raise UpdateError(err or "Aucune mise à jour à installer.")
    if latest <= current_version():
        raise UpdateError("L'application est déjà à jour.")
    if not _can_self_install():
        raise UpdateError("La mise à jour automatique ne marche que dans l'application Windows installée. "
                          "Télécharge l'installateur sur la page de la version.")
    if not (rel.get("installer_url") and rel.get("sha256") and rel.get("size")):
        raise UpdateError(rel.get("problem") or "Cette version n'a pas d'installateur vérifiable : télécharge-la à la main sur sa page.")
    if not asset_url_ok(rel["installer_url"]):  # un cache disque ne doit jamais faire télécharger ailleurs que chez GitHub
        raise UpdateError("L'adresse de l'installateur n'est pas acceptée : vérifie les mises à jour de nouveau.")
    return rel


def require_downloadable() -> None:
    """Vérifié par la route avant de lancer la tâche : 409 immédiat plutôt qu'une tâche qui échoue."""
    _need_release()


# --- Téléchargement (tâche) ----------------------------------------------------------------------

_download_lock = threading.Lock()


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} Go"


def download_task(ctx: JobContext) -> str:
    """Tâche « mise_a_jour » : télécharge l'installateur, vérifie sa taille et son empreinte, puis le range sous son
    vrai nom. Un fichier dont l'empreinte est fausse est supprimé. Renvoie la version téléchargée."""
    if not _download_lock.acquire(blocking=False):
        raise UpdateError("Un téléchargement de mise à jour est déjà en cours.")
    try:
        rel = _need_release()
        version = parse_version(rel["latest"])
        assert version is not None
        final = installer_path(version)
        if _downloaded(rel):
            ctx.step("Mise à jour déjà téléchargée", 1.0)
            return rel["latest"]
        size, expected = int(rel["size"]), rel["sha256"]
        final.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(final.parent).free
        if free < size + DISK_MARGIN:
            raise UpdateError(f"Pas assez de place sur le disque : il faut environ {_gb(size + DISK_MARGIN)} libres, "
                              f"il en reste {_gb(free)}.")
        temp = final.with_name(final.name + ".download")  # le vrai nom n'existe qu'une fois le fichier vérifié
        for old in (temp, temp.with_name(temp.name + ".part"), final, _sidecar(final)):
            old.unlink(missing_ok=True)
        try:
            ctx.step(f"Téléchargement de la version {rel['latest']}…", 0.0)
            try:
                downloads._http_download(rel["installer_url"], temp, ctx, f"Téléchargement de la version {rel['latest']}",
                                         0.0, 0.9, max_bytes=size, expected_size=size,
                                         allow_redirect=lambda target: redirect_ok(target, DOWNLOAD_HOSTS))
            except (OSError, ValueError, http.client.HTTPException) as e:
                reason = str(getattr(e, "reason", e))
                if "redirection refusée" in reason:
                    raise UpdateError("Le téléchargement a été refusé : GitHub a renvoyé vers une adresse non autorisée.") from None
                raise UpdateError(f"Le téléchargement a échoué : {reason}") from None
            if temp.stat().st_size != size:
                raise UpdateError("Le fichier reçu n'a pas la taille annoncée : il a été supprimé.")
            actual = sha256_file(temp, ctx, 0.9, 0.99, "Vérification de l'empreinte du fichier")
            if actual != expected:
                raise UpdateError("L'empreinte du fichier téléchargé ne correspond pas à celle publiée : il a été supprimé "
                                  "et ne sera pas installé. Réessaie plus tard.")
            os.replace(temp, final)
            _sidecar(final).write_text(expected, encoding="utf-8")
        finally:
            temp.unlink(missing_ok=True)
            temp.with_name(temp.name + ".part").unlink(missing_ok=True)
        ctx.step("Mise à jour téléchargée et vérifiée", 1.0)
        return rel["latest"]
    finally:
        _download_lock.release()


def start_download():
    require_downloadable()
    return manager.submit(UPDATE_PID, "mise_a_jour", download_task)


# --- Installation --------------------------------------------------------------------------------

_installing = False


def installer_command(path: Path) -> list[str]:
    """Silencieux, sans annulation, sans redémarrage de Windows ; /RELAUNCH=1 relance l'application après coup."""
    return [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART", "/RELAUNCH=1"]


def _detached_kwargs() -> dict:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True, "cwd": str(updates_dir())}
    if os.name == "nt":  # l'installateur doit survivre à l'application qui le lance
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kw["start_new_session"] = True
    return kw


def _refuse_if_busy() -> None:
    if [j for j in list(manager.jobs.values()) if j.status in ACTIVE]:
        raise UpdateError("Une tâche est en cours (montage, export, téléchargement…) : attends sa fin ou annule-la, "
                          "puis relance la mise à jour. L'application va se fermer.")


def install() -> dict:
    """Lance l'installateur téléchargé puis demande la fermeture de l'application. Lève UpdateError (409) si c'est
    impossible : pas l'exe installé, rien de téléchargé, empreinte fausse, tâche en cours."""
    global _installing
    with _lock:
        if _installing:
            raise UpdateError("L'installation est déjà lancée.")
        if not _can_self_install():
            raise UpdateError("L'installation automatique ne marche que dans l'application Windows installée.")
        _refuse_if_busy()
        rel = _need_release()
        version = parse_version(rel["latest"])
        assert version is not None
        path = installer_path(version)
        if not path.is_file():
            raise UpdateError("L'installateur n'est pas encore téléchargé : télécharge d'abord la mise à jour.")
        _installing = True  # posé avant le calcul d'empreinte (plusieurs secondes) : le verrou, lui, est rendu
    try:
        # Le hachage se fait hors du verrou : l'interface (GET /api/update) ne se fige pas pendant ce temps.
        try:
            good = path.stat().st_size == rel["size"] and sha256_file(path) == rel["sha256"]
        except OSError as e:
            raise UpdateError(f"L'installateur téléchargé ne peut pas être lu ({e.__class__.__name__}) : "
                              "attends un instant (antivirus ?), puis réessaie.") from None
        if not good:
            path.unlink(missing_ok=True)
            _sidecar(path).unlink(missing_ok=True)
            raise UpdateError("L'installateur téléchargé est abîmé (empreinte ou taille fausse) : il a été supprimé. "
                              "Télécharge la mise à jour de nouveau.")
        if not lifecycle.can_exit():
            raise UpdateError("Cette fenêtre ne peut pas se fermer toute seule : ferme l'application, puis lance l'installateur à la main "
                              f"({path}).")
        _refuse_if_busy()  # une tâche a pu démarrer pendant le calcul : on la regarde juste avant de lancer
        try:
            subprocess.Popen(installer_command(path), **_detached_kwargs())
        except OSError as e:
            raise UpdateError(f"Impossible de lancer l'installateur : {e}") from None
    except BaseException:
        _installing = False
        raise
    log.info("Mise à jour vers %s : installateur lancé (%s), fermeture de l'application", rel["latest"], path.name)
    lifecycle.request_exit(EXIT_DELAY)
    return {"ok": True}
