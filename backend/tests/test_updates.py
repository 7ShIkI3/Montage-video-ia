"""Mises à jour : vérification du flux GitHub, téléchargement vérifié, installation sur clic, nettoyage.

Aucun test ne touche le vrai réseau : un petit serveur HTTP local (127.0.0.1) joue GitHub, et le flux d'essai
MONTEUR_UPDATE_URL pointe vers lui.
"""
import hashlib
import importlib.util
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, downloads, lifecycle, settings, updates
from app.jobs import ACTIVE, Job, JobContext, manager
from app.main import app
from app.version import __version__ as REAL_VERSION

c = TestClient(app, base_url="http://127.0.0.1:8000")  # le vrai nom d'hôte de l'application
CURRENT = "1.1.0"
ROOT = Path(__file__).resolve().parents[2]


# --- Un faux GitHub ----------------------------------------------------------------------------

class Feed:
    """Sert /feed (la « release »), /files/<nom> (les fichiers) et des redirections, et note chaque requête."""

    def __init__(self):
        self.release: dict | None = None
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.hits: list[str] = []
        self.raw_feed: bytes | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path in outer.redirects:
                    self.send_response(302)
                    self.send_header("Location", outer.redirects[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/feed" and outer.raw_feed is not None:
                    body, ctype = outer.raw_feed, "application/json"
                elif self.path == "/feed" and outer.release is not None:
                    body, ctype = json.dumps(outer.release).encode(), "application/json"
                elif self.path in outer.files:
                    body, ctype = outer.files[self.path], "application/octet-stream"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/feed"

    def asset_url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/files/{name}"

    def feed_hits(self) -> int:
        return self.hits.count("/feed")

    def publish(self, version="1.2.0", payload=b"installateur-de-test" * 500, sums=True, bad_sum=False, size=None,
                digest=None, body="Nouveautés\n- une puce", tag=None):
        name = f"MonteurIA-Setup-{version}.exe"
        sha = hashlib.sha256(payload).hexdigest()
        self.files["/files/" + name] = payload
        asset = {"name": name, "size": len(payload) if size is None else size, "browser_download_url": self.asset_url(name)}
        if digest:
            asset["digest"] = digest
        assets = [asset]
        if sums:
            text = f"{'0' * 64 if bad_sum else sha}  {name}\n".encode()
            self.files["/files/SHA256SUMS.txt"] = text
            assets.append({"name": "SHA256SUMS.txt", "size": len(text), "browser_download_url": self.asset_url("SHA256SUMS.txt")})
        self.release = {"tag_name": tag or f"v{version}", "draft": False, "prerelease": False, "body": body,
                        "published_at": "2026-11-12T10:00:00Z", "html_url": f"http://127.0.0.1:{self.port}/release",
                        "assets": assets}
        return payload, sha


@pytest.fixture
def feed():
    f = Feed()
    yield f
    f.httpd.shutdown()
    f.httpd.server_close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Dossier de données jetable, version courante fixe, aucun flux ni réglage hérité d'un autre test."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(updates, "__version__", CURRENT)
    monkeypatch.delenv("MONTEUR_UPDATE_URL", raising=False)
    monkeypatch.delenv("MONTEUR_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updates, "_installing", False)
    monkeypatch.setattr(updates, "_mem", None)
    settings.apply({"check_updates": True})
    yield
    settings.apply({"check_updates": True})
    lifecycle.set_exit_callback(None)


@pytest.fixture
def as_installed_exe(monkeypatch):
    monkeypatch.setattr(updates, "_can_self_install", lambda: True)


def use(monkeypatch, feed: Feed) -> None:
    monkeypatch.setenv("MONTEUR_UPDATE_URL", feed.url)


def wait(job, timeout=30.0):
    end = time.time() + timeout
    while job.status in ACTIVE and time.time() < end:
        time.sleep(0.05)
    return job


# --- Versions ----------------------------------------------------------------------------------

def test_versions_are_compared_as_integers_not_text():
    p = updates.parse_version
    assert p("1.0.0") < p("1.0.9") < p("1.0.10") < p("1.1.0") < p("1.10.0") < p("2.0.0")
    assert p("v1.2.3") == (1, 2, 3)
    assert "1.0.10" < "1.0.9"  # le piège que la comparaison en entiers évite


@pytest.mark.parametrize("text", ["1.2", "1.2.3-beta", "1.2.3.4", "latest", "", "v", "1.2.x", None, 12, "1.2.3\n; rm"])
def test_unreadable_versions_are_ignored(text):
    assert updates.parse_version(text) is None


def test_version_is_single_sourced():
    """backend/app/version.py est la seule source : API, installateur et outils de construction la suivent."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", REAL_VERSION)
    assert app.version == REAL_VERSION
    assert c.get("/api/health").json()["version"] == REAL_VERSION
    assert re.search(rf'^\s*#define AppVersion "{re.escape(REAL_VERSION)}"', (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8-sig"), re.M)
    assert downloads.USER_AGENT.startswith(f"MonteurIA/{REAL_VERSION}")
    spec = importlib.util.spec_from_file_location("build_tool", ROOT / "desktop" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.VERSION == REAL_VERSION == build.read_version()


# --- Lecture du flux ---------------------------------------------------------------------------

def test_newer_release_is_announced_with_its_details(monkeypatch, feed, as_installed_exe):
    payload, sha = feed.publish(body="Notes de version\r\n- une puce")
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["current"] == CURRENT and r["latest"] == "1.2.0" and r["available"] is True
    assert r["notes"] == "Notes de version\n• une puce" and r["published_at"] == "2026-11-12T10:00:00Z"
    assert r["size"] == len(payload) and r["page_url"].endswith("/release")
    assert r["installable"] is True and r["downloaded"] is False and r["enabled"] is True
    assert r["error"] is None and r["checked_at"].endswith("+00:00")
    # Le flux est lu avec le User-Agent annoncé.
    assert set(r) == {"current", "latest", "available", "notes", "published_at", "size", "page_url", "installable",
                      "downloaded", "checked_at", "enabled", "error"}


def test_same_or_older_version_is_not_an_update(monkeypatch, feed):
    use(monkeypatch, feed)
    for v in ("1.1.0", "1.0.9"):
        feed.publish(version=v)
        updates.reset_cache()
        r = c.get("/api/update?force=1").json()
        assert r["latest"] == v and r["available"] is False and r["error"] is None


def test_installable_only_inside_the_installed_exe(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    assert updates._can_self_install() is False
    assert c.get("/api/update").json()["installable"] is False  # pas l'exe installé
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # exe PyInstaller
    assert updates._can_self_install() is (sys.platform == "win32")  # et seulement sous Windows
    monkeypatch.setattr(updates.sys, "platform", "win32")
    assert updates._can_self_install() is True
    updates.reset_cache()
    assert c.get("/api/update").json()["installable"] is True


def test_release_without_files_warns_but_is_not_installable(monkeypatch, feed, as_installed_exe):
    feed.release = {"tag_name": "v1.3.0", "draft": False, "prerelease": False, "body": "x", "assets": []}
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["available"] is True and r["latest"] == "1.3.0"
    assert r["installable"] is False and r["downloaded"] is False and r["size"] is None
    assert c.post("/api/update/download").status_code == 409


def test_release_without_sums_file_is_not_installable(monkeypatch, feed, as_installed_exe):
    feed.publish(sums=False)
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["available"] is True and r["installable"] is False
    assert "SHA256SUMS.txt" in r["error"]
    r = c.post("/api/update/download")
    assert r.status_code == 409 and "SHA256SUMS.txt" in r.json()["detail"]


@pytest.mark.parametrize("tag", ["latest", "1.2.0", "v1.2", "v1.2.0-beta", "v1.2.0\n", "../v1.2.0", "v1.2.0/../../x"])
def test_invalid_tag_is_ignored_with_a_clear_error(monkeypatch, feed, tag):
    feed.publish(tag=tag)
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["latest"] is None and r["available"] is False and r["installable"] is False
    assert "inattendu" in r["error"]


def test_draft_and_prerelease_are_ignored(monkeypatch, feed):
    use(monkeypatch, feed)
    for flag in ("draft", "prerelease"):
        feed.publish()
        feed.release[flag] = True
        updates.reset_cache()
        r = c.get("/api/update").json()
        assert r["latest"] is None and r["available"] is False and r["error"] is None


@pytest.mark.parametrize("bad", ["..\\x.exe", "../MonteurIA-Setup-1.2.0.exe", "MonteurIA-Setup-1.2.0.exe\\..\\x.exe",
                                 "C:\\Windows\\MonteurIA-Setup-1.2.0.exe", "MonteurIA-Setup-1.9.9.exe", "MonteurIA-Setup-01.2.0.exe",
                                 "monteuria-setup-1.2.0.exe", "MonteurIA-Setup-1.2.0.exe.bat", "x.exe"])
def test_booby_trapped_installer_name_is_not_an_installer(monkeypatch, feed, as_installed_exe, bad):
    payload, sha = feed.publish()
    feed.release["assets"][0]["name"] = bad
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["available"] is True and r["installable"] is False and r["size"] is None
    assert c.post("/api/update/download").status_code == 409
    assert not updates.updates_dir().exists() or not any(updates.updates_dir().iterdir())


def test_trapped_name_never_reaches_the_disk_even_with_the_right_tag():
    """Le nom du fichier local est refait à partir des trois entiers du tag, jamais copié du réseau."""
    rel = updates.parse_release({"tag_name": "v1.2.0", "assets": [
        {"name": "..\\evil.exe", "size": 10, "browser_download_url": f"https://github.com{updates.RELEASE_PATH}v1.2.0/..%5Cevil.exe"}]})
    assert rel["installer_url"] is None
    assert updates.installer_path((1, 2, 0)).name == "MonteurIA-Setup-1.2.0.exe"


GOOD = f"https://github.com{updates.RELEASE_PATH}v1.2.0/MonteurIA-Setup-1.2.0.exe"


@pytest.mark.parametrize("url,ok", [
    (GOOD, True),
    ("http://github.com" + updates.RELEASE_PATH + "v1.2.0/x.exe", False),                       # pas https
    ("https://evil.example" + updates.RELEASE_PATH + "v1.2.0/x.exe", False),                    # autre hôte
    ("https://github.com.evil.example" + updates.RELEASE_PATH + "v1.2.0/x.exe", False),         # hôte qui commence par github.com
    ("https://github.com@evil.example" + updates.RELEASE_PATH + "x.exe", False),                # identifiants dans l'adresse
    ("https://github.com/autre/depot/releases/download/v1.2.0/x.exe", False),                  # autre dépôt
    ("https://github.com/7ShIkI3/Montage-video-ia/archive/main.zip", False),                  # pas un fichier de release
    ("https://github.com" + updates.RELEASE_PATH + "../../../x.exe", False),                    # remontée de dossier
    ("https://github.com" + updates.RELEASE_PATH + "%2e%2e/x.exe", False),
    ("https://objects.githubusercontent.com/x", False),                                          # seulement après une redirection
    ("file:///C:/Windows/x.exe", False), ("ftp://github.com" + updates.RELEASE_PATH + "x.exe", False),
    (None, False), (12, False), ("", False),
])
def test_asset_url_must_be_on_github_release_path(url, ok):
    assert updates.asset_url_ok(url) is ok


def test_url_outside_github_makes_the_release_not_installable():
    evil = "https://evil.example/7ShIkI3/Montage-video-ia/releases/download/v1.2.0/MonteurIA-Setup-1.2.0.exe"
    rel = updates.parse_release({"tag_name": "v1.2.0", "assets": [
        {"name": "MonteurIA-Setup-1.2.0.exe", "size": 10, "browser_download_url": evil}]})
    assert rel["latest"] == "1.2.0" and rel["installer_url"] is None and rel["sha256"] is None


def test_redirects_only_go_to_github_hosts(monkeypatch):
    ok = lambda u: updates.redirect_ok(u, updates.DOWNLOAD_HOSTS)
    assert ok("https://github.com/x") and ok("https://objects.githubusercontent.com/x")
    assert ok("https://release-assets.githubusercontent.com/x")
    assert not ok("https://evil.example/x") and not ok("http://github.com/x")
    assert not ok("https://githubusercontent.com/x") and not ok("https://api.github.com/x")
    assert not ok("https://objects.githubusercontent.com.evil.example/x") and not ok("https://github.com@evil.example/x")
    assert not ok("http://127.0.0.1:9/x")  # sans MONTEUR_UPDATE_URL, le local n'est pas accepté
    monkeypatch.setenv("MONTEUR_UPDATE_URL", "http://127.0.0.1:9/feed")
    assert ok("http://127.0.0.1:9/x") and not ok("http://127.0.0.1:10/x")


def test_custom_feed_must_be_https_or_local(monkeypatch):
    for bad in ("http://example.com/feed", "ftp://127.0.0.1/feed", "https://user:pw@example.com/feed", "feed"):
        monkeypatch.setenv("MONTEUR_UPDATE_URL", bad)
        updates.reset_cache()
        r = c.get("/api/update?force=1")
        assert r.status_code == 200 and "MONTEUR_UPDATE_URL" in r.json()["error"], bad


def test_sums_file_parsing():
    h1, h2 = "a" * 64, "b" * 64
    n = "MonteurIA-Setup-1.2.0.exe"
    assert updates.parse_sums(f"{h1}  {n}\n", n) == h1
    assert updates.parse_sums(f"{h2}  autre.zip\n{h1.upper()} *{n}\r\n", n) == h1  # majuscules, marque binaire, CRLF
    assert updates.parse_sums(f"{h2}  autre.zip\n", n) is None
    assert updates.parse_sums(f"{h1}  {n}\n{h2}  {n}\n", n) is None  # deux empreintes pour un fichier : refus
    assert updates.parse_sums(f"{h1}  x/{n}\n{h1[:-1]}  {n}\n", n) is None
    assert updates.parse_sums("", n) is None


def test_digest_disagreement_refuses_the_update(monkeypatch, feed, as_installed_exe):
    feed.publish(digest="sha256:" + "c" * 64)
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["available"] is True and r["installable"] is False and "ne correspond pas" in r["error"]
    assert c.post("/api/update/download").status_code == 409


def test_matching_digest_is_accepted(monkeypatch, feed, as_installed_exe):
    payload, sha = feed.publish(digest="sha256:" + hashlib.sha256(b"installateur-de-test" * 500).hexdigest())
    use(monkeypatch, feed)
    assert c.get("/api/update").json()["installable"] is True


def test_notes_are_plain_text_and_capped(monkeypatch, feed):
    feed.publish(body="a\x00b\x1b[31m" + "x" * 9000)
    use(monkeypatch, feed)
    notes = c.get("/api/update").json()["notes"]
    assert len(notes) == 4000 and "\x00" not in notes and "\x1b" not in notes


def test_oversized_feed_is_refused(monkeypatch, feed):
    feed.publish(body="x" * (updates.MAX_FEED_BYTES + 10))
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["latest"] is None and "trop grosse" in r["error"]


def test_installer_larger_than_4_gib_is_refused(monkeypatch, feed, as_installed_exe):
    feed.publish(size=updates.MAX_INSTALLER_BYTES + 1)
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["available"] is True and r["installable"] is False


def test_network_failure_gives_an_error_not_an_http_error(monkeypatch):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # libéré aussitôt : personne n'écoute
    monkeypatch.setenv("MONTEUR_UPDATE_URL", f"http://127.0.0.1:{port}/feed")
    r = c.get("/api/update")
    assert r.status_code == 200
    body = r.json()
    assert body["latest"] is None and body["available"] is False and "injoignable" in body["error"]


def test_feed_404_and_garbage_are_reported(monkeypatch, feed):
    use(monkeypatch, feed)
    assert "Aucune version" in c.get("/api/update").json()["error"]  # /feed absent -> 404
    feed.raw_feed = b"pas du json"
    r = c.get("/api/update?force=1").json()
    assert r["available"] is False and "illisible" in r["error"]
    feed.raw_feed = b"[1, 2]"
    assert "illisible" in c.get("/api/update?force=1").json()["error"]


def test_a_failure_keeps_the_previous_result(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    assert c.get("/api/update").json()["latest"] == "1.2.0"
    feed.release = None  # le flux tombe en 404
    r = c.get("/api/update?force=1").json()
    assert r["latest"] == "1.2.0" and r["error"] and r["available"] is True


# --- Cache et réglage --------------------------------------------------------------------------

def test_result_is_kept_six_hours_and_force_rechecks(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    clock = [1_000_000.0]
    monkeypatch.setattr(updates, "_now", lambda: clock[0])
    c.get("/api/update")
    c.get("/api/update")
    assert feed.feed_hits() == 1
    clock[0] += 5 * 3600
    c.get("/api/update")
    assert feed.feed_hits() == 1
    c.get("/api/update?force=1")
    assert feed.feed_hits() == 2
    clock[0] += 6 * 3600 + 1
    c.get("/api/update")
    assert feed.feed_hits() == 3


def test_result_survives_a_restart_through_the_small_file(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    c.get("/api/update")
    assert (config.DATA_DIR / "update_cache.json").is_file()
    monkeypatch.setattr(updates, "_mem", None)  # comme après un redémarrage
    r = c.get("/api/update").json()
    assert feed.feed_hits() == 1 and r["latest"] == "1.2.0" and r["checked_at"]
    # Un cache écrit par une autre version de l'application n'est pas réutilisé.
    monkeypatch.setattr(updates, "_mem", None)
    monkeypatch.setattr(updates, "__version__", "1.0.0")
    c.get("/api/update")
    assert feed.feed_hits() == 2


def test_a_failure_is_not_retried_for_five_minutes(monkeypatch, feed):
    use(monkeypatch, feed)
    clock = [1_000_000.0]
    monkeypatch.setattr(updates, "_now", lambda: clock[0])
    c.get("/api/update")
    c.get("/api/update")
    assert feed.feed_hits() == 1
    clock[0] += 301
    c.get("/api/update")
    assert feed.feed_hits() == 2


def test_check_updates_setting_cuts_the_automatic_check(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    assert c.get("/api/settings").json()["check_updates"] is True
    assert c.put("/api/settings", json={"check_updates": False}).json()["check_updates"] is False
    assert "check_updates" in settings.SETTINGS_PATH.read_text(encoding="utf-8")
    r = c.get("/api/update").json()
    assert r["enabled"] is False and r["latest"] is None and r["available"] is False and feed.feed_hits() == 0
    # Un clic sur « Vérifier » reste possible.
    r = c.get("/api/update?force=1").json()
    assert r["enabled"] is False and r["latest"] == "1.2.0" and feed.feed_hits() == 1
    assert c.put("/api/settings", json={"check_updates": True}).json()["check_updates"] is True


def test_env_variable_cuts_everything(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    monkeypatch.setenv("MONTEUR_UPDATE_CHECK", "0")
    for q in ("", "?force=1"):
        r = c.get("/api/update" + q).json()
        assert r["enabled"] is False and r["latest"] is None and r["available"] is False
    assert feed.feed_hits() == 0
    assert c.post("/api/update/download").status_code == 409


# --- Téléchargement ----------------------------------------------------------------------------

def test_download_succeeds_and_is_verified(monkeypatch, feed, as_installed_exe):
    payload, sha = feed.publish()
    use(monkeypatch, feed)
    r = c.post("/api/update/download")
    assert r.status_code == 200 and r.json()["kind"] == "mise_a_jour"
    job = wait(manager.get(r.json()["id"]))
    assert job.status == "termine" and job.result == "1.2.0" and job.progress == 1.0
    final = updates.installer_path((1, 2, 0))
    assert final.read_bytes() == payload
    assert final.with_name(final.name + ".sha256").read_text(encoding="utf-8") == sha
    assert sorted(p.name for p in updates.updates_dir().iterdir()) == [final.name, final.name + ".sha256"]
    assert c.get("/api/update").json()["downloaded"] is True
    # Une deuxième demande ne retélécharge rien.
    hits = len(feed.hits)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "termine" and len(feed.hits) == hits
    assert manager.jobs[job.id].timeout_s == 3600


def test_wrong_hash_deletes_the_file_and_says_so(monkeypatch, feed, as_installed_exe):
    feed.publish(bad_sum=True)
    use(monkeypatch, feed)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "erreur" and "empreinte" in job.error and "supprimé" in job.error
    assert list(updates.updates_dir().iterdir()) == []  # ni .part, ni .download, ni fichier final
    assert c.get("/api/update").json()["downloaded"] is False


def test_announced_size_must_match_the_received_size(monkeypatch, feed, as_installed_exe):
    feed.publish(size=10)
    use(monkeypatch, feed)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "erreur" and "taille" in job.error
    assert list(updates.updates_dir().iterdir()) == []


def test_redirect_to_a_forbidden_host_is_refused(monkeypatch, feed, as_installed_exe):
    payload, sha = feed.publish()
    name = "MonteurIA-Setup-1.2.0.exe"
    feed.redirects["/files/" + name] = "https://evil.example/MonteurIA-Setup-1.2.0.exe"
    use(monkeypatch, feed)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "erreur" and "adresse non autorisée" in job.error
    assert list(updates.updates_dir().iterdir()) == []


def test_redirect_to_an_allowed_place_is_followed(monkeypatch, feed, as_installed_exe):
    payload, sha = feed.publish()
    name = "MonteurIA-Setup-1.2.0.exe"
    feed.files["/cdn/objet"] = payload
    feed.redirects["/files/" + name] = f"http://127.0.0.1:{feed.port}/cdn/objet"
    use(monkeypatch, feed)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "termine"
    assert updates.installer_path((1, 2, 0)).read_bytes() == payload


def test_not_enough_disk_space_is_said_before_downloading(monkeypatch, feed, as_installed_exe):
    feed.publish()
    use(monkeypatch, feed)
    monkeypatch.setattr(updates.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000, "total": 2000, "used": 1000})())
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "erreur" and "place" in job.error
    assert not any("/files/Monteur" in h for h in feed.hits)


def test_download_can_be_cancelled(monkeypatch, feed, as_installed_exe):
    feed.publish(payload=b"x" * 5_000_000)
    use(monkeypatch, feed)
    job = manager.get(c.post("/api/update/download").json()["id"])
    job.cancel_requested = True
    job._cancel.set()
    wait(job)
    assert job.status == "annulee"
    assert list(updates.updates_dir().iterdir()) == []


def test_download_refused_outside_the_installed_exe(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    r = c.post("/api/update/download")
    assert r.status_code == 409 and "installée" in r.json()["detail"]


def test_download_refused_when_already_up_to_date(monkeypatch, feed, as_installed_exe):
    feed.publish(version="1.1.0")
    use(monkeypatch, feed)
    r = c.post("/api/update/download")
    assert r.status_code == 409 and "à jour" in r.json()["detail"]


def test_http_download_guard_does_not_change_other_uses(monkeypatch, feed, tmp_path):
    """Sans `allow_redirect`, le téléchargement habituel (musiques, modèles) suit toujours les redirections."""
    feed.files["/cdn/a"] = b"abc"
    feed.redirects["/start"] = f"http://127.0.0.1:{feed.port}/cdn/a"
    base = f"http://127.0.0.1:{feed.port}"
    ctx = JobContext(Job(project_id="t", kind="telechargement"))
    out = downloads._http_download(base + "/start", tmp_path / "a.bin", ctx, "t")
    assert out.read_bytes() == b"abc"
    with pytest.raises(Exception, match="redirection refusée"):
        downloads._http_download(base + "/start", tmp_path / "b.bin", ctx, "t", allow_redirect=lambda u: False)
    assert not (tmp_path / "b.bin").exists() and not (tmp_path / "b.bin.part").exists()
    with pytest.raises(ValueError, match="incomplet|taille"):
        downloads._http_download(base + "/cdn/a", tmp_path / "c.bin", ctx, "t", expected_size=99)


# --- Installation ------------------------------------------------------------------------------

def downloaded_update(monkeypatch, feed):
    payload, sha = feed.publish()
    use(monkeypatch, feed)
    job = wait(manager.get(c.post("/api/update/download").json()["id"]))
    assert job.status == "termine", job.error
    return payload, updates.installer_path((1, 2, 0))


def test_install_refused_outside_the_installed_exe(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    r = c.post("/api/update/install")
    assert r.status_code == 409 and "installée" in r.json()["detail"]


def test_install_refused_while_a_task_is_running(monkeypatch, feed, as_installed_exe):
    downloaded_update(monkeypatch, feed)
    popen = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: popen.append(a))
    busy = Job(project_id="p_essai", kind="export", status="en_cours")
    manager.jobs[busy.id] = busy
    try:
        r = c.post("/api/update/install")
    finally:
        manager.jobs.pop(busy.id, None)
    assert r.status_code == 409 and "tâche est en cours" in r.json()["detail"] and popen == []


def test_install_refused_when_nothing_is_downloaded(monkeypatch, feed, as_installed_exe):
    feed.publish()
    use(monkeypatch, feed)
    lifecycle.set_exit_callback(lambda: None)
    r = c.post("/api/update/install")
    assert r.status_code == 409 and "pas encore téléchargé" in r.json()["detail"]


def test_install_refused_when_the_file_was_tampered_with(monkeypatch, feed, as_installed_exe):
    payload, path = downloaded_update(monkeypatch, feed)
    popen = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: popen.append(a))
    lifecycle.set_exit_callback(lambda: None)
    path.write_bytes(b"Z" + payload[1:])  # même taille, contenu changé
    r = c.post("/api/update/install")
    assert r.status_code == 409 and "abîmé" in r.json()["detail"]
    assert popen == [] and not path.exists()
    assert c.get("/api/update").json()["downloaded"] is False


def test_install_refused_without_a_launcher_to_close_the_app(monkeypatch, feed, as_installed_exe):
    downloaded_update(monkeypatch, feed)
    popen = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: popen.append(a))
    r = c.post("/api/update/install")
    assert r.status_code == 409 and popen == []


def test_install_runs_the_installer_silently_then_closes_the_app(monkeypatch, feed, as_installed_exe):
    payload, path = downloaded_update(monkeypatch, feed)
    calls = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    closed = threading.Event()
    lifecycle.set_exit_callback(closed.set)
    monkeypatch.setattr(updates, "EXIT_DELAY", 0.05)
    r = c.post("/api/update/install")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert closed.wait(5), "l'application n'a pas été fermée"
    (cmd, kw), = calls
    assert cmd == [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART", "/RELAUNCH=1"]
    assert kw["close_fds"] is True and kw["stdin"] == kw["stdout"] == kw["stderr"] == updates.subprocess.DEVNULL
    if updates.os.name == "nt":  # détaché : l'installateur survit à l'application qui le lance
        assert kw["creationflags"] & 0x8 and kw["creationflags"] & 0x200
    # Une deuxième demande ne relance pas un deuxième installateur.
    assert c.post("/api/update/install").status_code == 409 and len(calls) == 1


def test_install_failure_to_start_keeps_the_app_open(monkeypatch, feed, as_installed_exe):
    downloaded_update(monkeypatch, feed)

    def boom(*a, **k):
        raise OSError("antivirus")
    monkeypatch.setattr(updates.subprocess, "Popen", boom)
    closed = threading.Event()
    lifecycle.set_exit_callback(closed.set)
    monkeypatch.setattr(updates, "EXIT_DELAY", 0.0)
    r = c.post("/api/update/install")
    assert r.status_code == 409 and "Impossible de lancer" in r.json()["detail"]
    assert not closed.wait(0.3)


def test_exit_callback_is_called_through_lifecycle():
    assert lifecycle.request_exit() is False  # aucun lanceur : rien ne se ferme
    done = threading.Event()
    lifecycle.set_exit_callback(done.set)
    assert lifecycle.can_exit() and lifecycle.request_exit(0.05) is True
    assert done.wait(2)
    lifecycle.set_exit_callback(lambda: 1 / 0)
    assert lifecycle.request_exit() is True  # un rappel qui plante ne fait pas planter la requête


# --- Nettoyage au démarrage --------------------------------------------------------------------

def test_cleanup_removes_old_installers_and_orphan_parts():
    d = updates.updates_dir()
    d.mkdir(parents=True)
    names = ["MonteurIA-Setup-1.0.0.exe", "MonteurIA-Setup-1.0.0.exe.sha256", "MonteurIA-Setup-1.1.0.exe",
             "MonteurIA-Setup-1.1.0.exe.sha256", "MonteurIA-Setup-1.2.0.exe", "MonteurIA-Setup-1.2.0.exe.sha256",
             "MonteurIA-Setup-1.3.0.exe.download", "MonteurIA-Setup-1.3.0.exe.download.part", "MonteurIA-Setup-1.4.0.exe.part",
             "MonteurIA-Setup-1.0.9.exe", "notes.txt"]
    for n in names:
        (d / n).write_bytes(b"x")
    (d / "dossier").mkdir()
    removed = updates.cleanup_old()
    assert sorted(p.name for p in d.iterdir()) == ["MonteurIA-Setup-1.2.0.exe", "MonteurIA-Setup-1.2.0.exe.sha256", "dossier", "notes.txt"]
    assert "MonteurIA-Setup-1.0.0.exe" in removed and "MonteurIA-Setup-1.3.0.exe.download.part" in removed


def test_cleanup_without_the_folder_does_nothing():
    assert updates.cleanup_old() == []


def test_cleanup_runs_when_the_server_starts():
    d = updates.updates_dir()
    d.mkdir(parents=True)
    (d / "MonteurIA-Setup-1.0.0.exe").write_bytes(b"x")
    (d / "MonteurIA-Setup-1.5.0.exe.part").write_bytes(b"x")
    with TestClient(app):  # le « lifespan » s'exécute
        pass
    assert list(d.iterdir()) == []


# --- Défauts relevés à la relecture ------------------------------------------------------------

def test_download_and_install_refuse_a_foreign_page_or_host(monkeypatch, feed, as_installed_exe):
    """Le CORS est ouvert : sans contrôle, une page web quelconque déclencherait le téléchargement et l'installation."""
    downloaded_update(monkeypatch, feed)
    popen = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: popen.append(a))
    lifecycle.set_exit_callback(lambda: None)
    for route in ("/api/update/download", "/api/update/install"):
        r = c.post(route, headers={"Origin": "https://page-etrangere.example"})
        assert r.status_code == 403, route
        r = c.post(route, headers={"Host": "page-etrangere.example:8000", "Origin": "http://page-etrangere.example:8000"})  # rebinding
        assert r.status_code == 403, route
        r = c.post(route, headers={"Host": "page-etrangere.example:8000"})
        assert r.status_code == 403, route
        assert c.post(route, headers={"Origin": "null"}).status_code == 403, route
    assert popen == []
    # La page de l'application elle-même (même origine, ou sans en-tête Origin) passe.
    assert c.post("/api/update/install", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert len(popen) == 1


def test_localhost_names_are_accepted(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    for host in ("localhost:5173", "127.0.0.1:8000", "[::1]:8000"):
        r = c.post("/api/update/download", headers={"Host": host, "Origin": f"http://{host}"})
        assert r.status_code == 409, host  # laissé passer, puis refusé pour une autre raison (pas l'exe installé)


def test_transient_failure_reading_the_sums_is_retried_soon_and_not_cached_for_hours(monkeypatch, feed, as_installed_exe):
    feed.publish()
    use(monkeypatch, feed)
    real_fetch = updates._fetch

    def flaky(url, limit, hosts):
        if url.endswith("SHA256SUMS.txt"):
            raise updates.UpdateError("GitHub est injoignable.", transient=True)
        return real_fetch(url, limit, hosts)
    monkeypatch.setattr(updates, "_fetch", flaky)
    r = c.get("/api/update").json()
    assert r["installable"] is False and r["error"]
    assert not (config.DATA_DIR / "update_cache.json").exists()  # un échec passager n'est pas écrit sur le disque
    # La panne est finie : après ERROR_TTL (5 min) et non après 6 h, tout redevient installable.
    monkeypatch.setattr(updates, "_fetch", real_fetch)
    t0 = time.time()
    monkeypatch.setattr(updates, "_now", lambda: t0 + updates.ERROR_TTL + 1)
    r = c.get("/api/update").json()
    assert r["installable"] is True and r["error"] is None


def test_missing_sums_file_on_the_server_stays_a_lasting_problem(monkeypatch, feed, as_installed_exe):
    feed.publish()
    del feed.files["/files/SHA256SUMS.txt"]  # annoncé dans la release, mais 404 : une absence réelle
    use(monkeypatch, feed)
    r = c.get("/api/update").json()
    assert r["installable"] is False and "SHA256SUMS.txt" in r["error"]
    assert (config.DATA_DIR / "update_cache.json").exists()


def test_disk_cache_is_ignored_when_the_feed_changed(monkeypatch, feed):
    feed.publish()
    use(monkeypatch, feed)
    assert c.get("/api/update").json()["latest"] == "1.2.0"
    assert updates._load_disk() is not None
    monkeypatch.setenv("MONTEUR_UPDATE_URL", f"http://127.0.0.1:{feed.port}/autre-flux")
    assert updates._load_disk() is None
    monkeypatch.delenv("MONTEUR_UPDATE_URL")  # flux officiel : le cache d'un flux d'essai n'est pas relu
    assert updates._load_disk() is None


def test_installer_url_read_back_from_a_cache_is_revalidated(monkeypatch, feed, as_installed_exe):
    feed.publish()
    use(monkeypatch, feed)
    rel = updates.parse_release(feed.release)
    rel["installer_url"] = "http://127.0.0.1:9/hors-github/MonteurIA-Setup-1.2.0.exe"
    monkeypatch.delenv("MONTEUR_UPDATE_URL")  # plus de flux d'essai : cette adresse n'a rien à faire là
    monkeypatch.setattr(updates, "_mem", {"at": time.time(), "ok_at": time.time(), "release": rel, "error": None})
    assert c.get("/api/update").json()["installable"] is False
    r = c.post("/api/update/download")
    assert r.status_code == 409 and "adresse" in r.json()["detail"]


def test_install_rechecks_for_a_task_started_during_the_hash(monkeypatch, feed, as_installed_exe):
    downloaded_update(monkeypatch, feed)
    popen = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: popen.append(a))
    lifecycle.set_exit_callback(lambda: None)
    real_hash = updates.sha256_file
    busy = Job(project_id="p_essai", kind="export", status="en_cours")
    held = []

    def hash_then_export(path, *a, **k):
        free = []

        def try_lock():  # un autre fil (ex. GET /api/update) peut-il prendre le verrou pendant le calcul ?
            free.append(updates._lock.acquire(timeout=2))
            if free[0]:
                updates._lock.release()
        t = threading.Thread(target=try_lock)
        t.start(); t.join()
        held.append(free[0])
        manager.jobs[busy.id] = busy  # un export démarre pendant le calcul
        return real_hash(path, *a, **k)
    monkeypatch.setattr(updates, "sha256_file", hash_then_export)
    try:
        r = c.post("/api/update/install")
    finally:
        manager.jobs.pop(busy.id, None)
    assert held == [True], "le calcul d'empreinte ne doit pas se faire sous le verrou"
    assert r.status_code == 409 and "tâche est en cours" in r.json()["detail"] and popen == []
    # Rien n'est resté « en cours d'installation » : une nouvelle demande, une fois l'export fini, aboutit.
    monkeypatch.setattr(updates, "sha256_file", real_hash)
    assert c.post("/api/update/install").status_code == 200 and len(popen) == 1


def test_unreadable_installer_gives_a_french_409_not_a_500(monkeypatch, feed, as_installed_exe):
    downloaded_update(monkeypatch, feed)
    lifecycle.set_exit_callback(lambda: None)

    def locked(*a, **k):
        raise PermissionError("verrouillé par l'antivirus")
    monkeypatch.setattr(updates, "sha256_file", locked)
    r = c.post("/api/update/install")
    assert r.status_code == 409 and "ne peut pas être lu" in r.json()["detail"]
    assert updates._installing is False


def test_release_notes_markdown_becomes_plain_text():
    body = chr(10).join([
        '## Nouveautés',
        '',
        '- **Installateur Windows** : un fichier `MonteurIA-Setup-<version>.exe`',
        '* autre puce',
        '  - sous-puce',
        '[la page](https://example.org/x)',
    ])
    out = updates._clean_notes(body)
    assert "**" not in out and "`" not in out and "#" not in out and "](" not in out
    assert out.splitlines()[0] == "Nouveautés"
    assert "• Installateur Windows : un fichier MonteurIA-Setup-<version>.exe" in out
    assert "• autre puce" in out and "  • sous-puce" in out and out.endswith("la page")
