"""Outils de livraison : desktop/release.py (publication GitHub) et desktop/build.py (version, ressource de l'exe).

Aucun test ne touche GitHub : un faux serveur local joue l'API et le téléversement, et git/jeton sont simulés.
"""
import hashlib
import importlib.util
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


release = _load("release_tool", "desktop/release.py")
build = _load("build_tool", "desktop/build.py")
TOKEN = "jeton-secret-d-essai"
COMMIT = "a" * 40
INSTALLER = b"faux-installateur" * 2000
CHANGELOG = """# Journal des changements

## 1.1.0 - 2026-11-12

- Mises à jour : l'application prévient et met à jour sur un clic.
- Autre puce.

## 1.0.0 - 2026-11-04

- Première version.
"""


# --- Un faux GitHub ----------------------------------------------------------------------------

class FakeGitHub:
    def __init__(self):
        self.published: dict | None = None  # release publiée existante (réponse de /releases/tags/<tag>)
        self.drafts: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.auth: set[str] = set()
        self.uploads: dict[str, bytes] = {}
        self.upload_headers: dict[str, dict] = {}
        self.created: dict | None = None
        self.patched: dict | None = None
        self.deleted: list[str] = []
        self.shrink = False
        self.assets: list[dict] = []
        self.tag_ref: dict | None = None  # réponse de /git/ref/tags/<tag> (None : le tag n'existe pas)
        self.tag_objects: dict[str, dict] = {}  # objets « tag annoté » de /git/tags/<sha>
        self.compare: str | None = "identical"  # statut de /compare/<commit>...main (None : commit inconnu)
        self.digests: str | None = None  # "ok" ou "faux" : GitHub joint une empreinte aux fichiers reçus
        self.patches: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                data = b""
                while len(data) < n:
                    chunk = self.rfile.read(min(65536, n - len(data)))
                    if not chunk:
                        break
                    data += chunk
                return data

            def _note(self):
                outer.requests.append((self.command, self.path))
                outer.auth.add(self.headers.get("Authorization", ""))

            def do_GET(self):
                self._note()
                p = self.path
                if p.startswith("/repos/o/r/releases/tags/"):
                    return self._json(200, outer.published) if outer.published else self._json(404, {"message": "Not Found"})
                if p.startswith("/repos/o/r/git/ref/tags/"):
                    return self._json(200, outer.tag_ref) if outer.tag_ref else self._json(404, {"message": "Not Found"})
                if p.startswith("/repos/o/r/releases?"):
                    return self._json(200, outer.drafts)
                if p.startswith("/repos/o/r/compare/") and p.endswith("...main"):
                    return self._json(200, {"status": outer.compare}) if outer.compare else self._json(404, {"message": "Not Found"})
                m = re.fullmatch(r"/repos/o/r/git/tags/(\w+)", p)
                if m:
                    obj = outer.tag_objects.get(m.group(1))
                    return self._json(200, obj) if obj else self._json(404, {"message": "Not Found"})
                m = re.fullmatch(r"/repos/o/r/releases/(\d+)", p)
                if m:
                    return self._json(200, {"id": int(m.group(1)), "assets": outer.assets})
                self._json(404, {"message": "Not Found"})

            def do_POST(self):
                self._note()
                data = self._body()
                if self.path == "/repos/o/r/releases":
                    outer.created = json.loads(data)
                    return self._json(201, {"id": 7, "draft": True, "html_url": "http://exemple.test/release/7",
                                            "upload_url": f"http://127.0.0.1:{outer.port}/uploads/7/assets{{?name,label}}"})
                m = re.fullmatch(r"/uploads/7/assets\?name=(.+)", self.path)
                if m:
                    name = m.group(1)
                    outer.uploads[name] = data
                    outer.upload_headers[name] = dict(self.headers)
                    stored = data[:-1] if outer.shrink and name.endswith(".exe") else data
                    asset = {"name": name, "size": len(stored), "state": "uploaded"}
                    if outer.digests:
                        asset["digest"] = "sha256:" + (hashlib.sha256(stored).hexdigest() if outer.digests == "ok" else "0" * 64)
                    outer.assets.append(asset)
                    return self._json(201, {"name": name})
                self._json(404, {"message": "Not Found"})

            def do_PATCH(self):
                self._note()
                outer.patched = json.loads(self._body())
                outer.patches.append(outer.patched)
                self._json(200, {"id": 7, "html_url": "http://exemple.test/release/7"})

            def do_DELETE(self):
                self._note()
                outer.deleted.append(self.path)
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def gh():
    g = FakeGitHub()
    yield g
    g.httpd.shutdown()
    g.httpd.server_close()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Un faux dépôt : version.py, CHANGELOG.md et l'installateur de la 1.1.0 ; git et le jeton sont simulés."""
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "version.py").write_text('"""v."""\n__version__ = "1.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    inst = tmp_path / "dist" / "installer"
    inst.mkdir(parents=True)
    (inst / "MonteurIA-Setup-1.1.0.exe").write_bytes(INSTALLER)
    monkeypatch.setattr(release, "origin_main", lambda root=None: (COMMIT, "1.1.0"))
    monkeypatch.setattr(release, "get_token", lambda root=None: TOKEN)
    return tmp_path


def run(repo, *argv, gh=None):
    extra = ["--api", gh.url, "--repo", "o/r"] if gh else []
    return release.main([*argv, *extra], root=repo)


# --- Contrôles locaux --------------------------------------------------------------------------

def test_dry_run_checks_locally_and_sends_nothing(repo, gh, capsys):
    code = run(repo, "--dry-run", gh=gh)
    out = capsys.readouterr().out
    assert code == 0
    assert "Version 1.1.0 (tag v1.1.0)" in out and hashlib.sha256(INSTALLER).hexdigest() in out
    assert "Tout est prêt" in out and "2 ligne(s)" in out and COMMIT[:10] in out
    assert gh.requests == []  # aucun appel au réseau
    assert not (repo / "dist" / "installer" / "SHA256SUMS.txt").exists()  # et aucun fichier écrit
    assert TOKEN not in out


def test_dry_run_fails_without_installer(repo, capsys):
    (repo / "dist" / "installer" / "MonteurIA-Setup-1.1.0.exe").unlink()
    assert run(repo, "--dry-run") == 1
    assert "introuvable" in capsys.readouterr().out


def test_dry_run_fails_without_changelog_section(repo, capsys):
    (repo / "CHANGELOG.md").write_text("## 1.0.0 - 2026-11-04\n\n- x\n", encoding="utf-8")
    assert run(repo, "--dry-run") == 1
    assert "## 1.1.0" in capsys.readouterr().out
    (repo / "CHANGELOG.md").write_text("## 1.1.0 - 2026-11-12\n\n## 1.0.0 - x\n- y\n", encoding="utf-8")
    assert run(repo, "--dry-run") == 1


def test_dry_run_fails_like_the_real_run_when_main_has_another_version(repo, gh, monkeypatch, capsys):
    monkeypatch.setattr(release, "origin_main", lambda root=None: (COMMIT, "1.0.0"))
    assert run(repo, "--dry-run") == 1  # le contrôle à blanc n'annonce pas un succès que la publication refuse
    out = capsys.readouterr().out
    assert "origin/main a la version 1.0.0" in out and "Tout est prêt" not in out
    assert run(repo, gh=gh) == 1 and gh.created is None


def test_version_and_changelog_parsing(repo):
    assert release.read_version(repo) == "1.1.0"
    assert release.read_version(ROOT) == build.read_version()
    notes = release.changelog_notes("1.1.0", repo)
    assert notes.startswith("- Mises à jour") and "Première version" not in notes
    assert "Première version" in release.changelog_notes("1.0.0", repo)
    with pytest.raises(release.ReleaseError):
        release.changelog_notes("9.9.9", repo)


def test_installer_over_2_gib_is_refused(repo, monkeypatch, capsys):
    monkeypatch.setattr(release, "MAX_ASSET", 10)
    assert run(repo, "--dry-run") == 1
    assert "2 Go" in capsys.readouterr().out


# --- Publication contre le faux serveur --------------------------------------------------------

def test_full_publication(repo, gh, capsys):
    assert run(repo, gh=gh) == 0
    out = capsys.readouterr()
    sha = hashlib.sha256(INSTALLER).hexdigest()
    # La release est créée en brouillon sur le commit de origin/main, avec les notes du CHANGELOG.
    assert gh.created["draft"] is True and gh.created["tag_name"] == "v1.1.0" and gh.created["target_commitish"] == COMMIT
    assert gh.created["body"].startswith("- Mises à jour") and gh.created["name"] == "Monteur IA 1.1.0"
    # Les deux fichiers sont téléversés tels quels, avec leur Content-Length.
    assert gh.uploads["MonteurIA-Setup-1.1.0.exe"] == INSTALLER
    assert gh.uploads["SHA256SUMS.txt"] == f"{sha}  MonteurIA-Setup-1.1.0.exe\n".encode()
    assert gh.upload_headers["MonteurIA-Setup-1.1.0.exe"]["Content-Length"] == str(len(INSTALLER))
    assert (repo / "dist" / "installer" / "SHA256SUMS.txt").read_text(encoding="utf-8") == f"{sha}  MonteurIA-Setup-1.1.0.exe\n"
    # Puis elle est publiée, en dernière version.
    assert gh.patched == {"draft": False, "make_latest": "true"}
    order = [r for r in gh.requests if r[0] in ("POST", "PATCH")]
    assert order[0][1] == "/repos/o/r/releases" and order[-1][0] == "PATCH"
    # Le jeton part dans l'en-tête, jamais dans la sortie.
    assert gh.auth == {f"Bearer {TOKEN}"}
    assert TOKEN not in out.out + out.err and "Release publiée" in out.out


def test_draft_option_leaves_the_release_unpublished(repo, gh, capsys):
    assert run(repo, "--draft", gh=gh) == 0
    assert gh.patched is None and gh.created["draft"] is True
    assert "brouillon" in capsys.readouterr().out


def test_refuses_when_the_tag_already_has_a_published_release(repo, gh, capsys):
    gh.published = {"id": 3, "draft": False, "tag_name": "v1.1.0", "html_url": "http://exemple.test/release/3"}
    assert run(repo, gh=gh) == 1
    assert "existe déjà" in capsys.readouterr().out
    assert not any(m == "POST" for m, _ in gh.requests) and gh.uploads == {}


def test_refuses_an_existing_tag_on_another_commit(repo, gh, capsys):
    gh.tag_ref = {"ref": "refs/tags/v1.1.0", "object": {"type": "commit", "sha": "f" * 40}}
    assert run(repo, gh=gh) == 1
    err = capsys.readouterr().err
    assert "v1.1.0 existe déjà" in err and "autre commit" in err
    assert gh.created is None and gh.uploads == {} and gh.patched is None


def test_accepts_an_existing_tag_on_the_same_commit(repo, gh):
    gh.tag_ref = {"ref": "refs/tags/v1.1.0", "object": {"type": "commit", "sha": COMMIT}}
    assert run(repo, gh=gh) == 0 and gh.patched is not None


def test_resumes_a_leftover_draft(repo, gh):
    gh.drafts = [{"id": 7, "draft": True, "tag_name": "v1.1.0", "html_url": "x", "upload_url": f"{gh.url}/uploads/7/assets{{?name,label}}",
                  "assets": [{"id": 55, "name": "SHA256SUMS.txt"}, {"id": 56, "name": "autre.zip"}]}]
    assert run(repo, gh=gh) == 0
    assert gh.created is None  # pas de seconde release
    assert gh.deleted == ["/repos/o/r/releases/assets/55"]  # seul l'ancien fichier du même nom est remplacé
    assert set(gh.uploads) == {"MonteurIA-Setup-1.1.0.exe", "SHA256SUMS.txt"}
    # Le brouillon, qui peut dater d'un autre commit, est réaligné sur le code de l'installateur, puis publié.
    assert gh.patches[0]["target_commitish"] == COMMIT and gh.patches[0]["draft"] is True and gh.patches[0]["body"].startswith("- Mises à jour")
    assert gh.patches[-1] == {"draft": False, "make_latest": "true"}


def test_accepts_an_annotated_tag_on_the_same_commit(repo, gh):
    gh.tag_ref = {"ref": "refs/tags/v1.1.0", "object": {"type": "tag", "sha": "b" * 40}}
    gh.tag_objects["b" * 40] = {"tag": "v1.1.0", "object": {"type": "commit", "sha": COMMIT}}
    assert run(repo, gh=gh) == 0 and gh.patched is not None
    gh.tag_objects["b" * 40] = {"tag": "v1.1.0", "object": {"type": "commit", "sha": "f" * 40}}
    gh.patched = None
    assert run(repo, gh=gh) == 1 and gh.patched is None


def test_github_digest_is_checked_when_given(repo, gh, capsys):
    gh.digests = "ok"
    assert run(repo, gh=gh) == 0
    assert "empreintes SHA-256 confirmées par GitHub" in capsys.readouterr().out


def test_a_file_received_with_another_digest_blocks_the_publication(repo, gh, capsys):
    gh.digests = "faux"
    assert run(repo, gh=gh) == 1
    assert gh.patched is None  # jamais publiée
    assert "autre empreinte" in capsys.readouterr().err


def test_a_file_received_smaller_than_sent_blocks_the_publication(repo, gh, capsys):
    gh.shrink = True
    assert run(repo, gh=gh) == 1
    assert gh.patched is None  # jamais publiée
    assert "brouillon" in capsys.readouterr().err


def test_upload_address_must_be_the_expected_host(repo):
    g = release.GitHub(release.API, TOKEN)  # vraie adresse de l'API : seul uploads.github.com est accepté
    inst = repo / "dist" / "installer" / "MonteurIA-Setup-1.1.0.exe"
    for bad in ("https://evil.example/repos/o/r/releases/1/assets{?name,label}", "http://uploads.github.com/x{?name}", ""):
        with pytest.raises(release.ReleaseError, match="adresse de téléversement"):
            g.upload({"upload_url": bad}, inst, "x.exe")


def test_api_address_must_be_https_or_local():
    for bad in ("http://api.github.com", "http://exemple.test", "ftp://127.0.0.1"):
        with pytest.raises(release.ReleaseError):
            release.GitHub(bad, TOKEN)
    release.GitHub("http://127.0.0.1:9", TOKEN)


def test_token_comes_from_git_credential_and_is_never_echoed(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"], seen["env"] = cmd, kw["input"], kw["env"]
        return type("P", (), {"stdout": "protocol=https\nhost=github.com\nusername=moi\npassword=" + TOKEN + "\n", "stderr": "", "returncode": 0})()
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.get_token() == TOKEN
    assert seen["cmd"] == ["git", "credential", "fill"] and "host=github.com" in seen["input"]
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "", "stderr": "secret", "returncode": 1})())
    with pytest.raises(release.ReleaseError) as e:
        release.get_token()
    assert "secret" not in str(e.value)


def test_origin_main_reads_the_version_at_that_commit(monkeypatch):
    answers = {("rev-parse", "origin/main"): COMMIT, ("show", "origin/main:backend/app/version.py"): '__version__ = "1.1.0"\n'}
    monkeypatch.setattr(release, "git", lambda root, *a: answers[a])
    assert release.origin_main(ROOT) == (COMMIT, "1.1.0")


# --- Sur un runner GitHub Actions (--ci) -------------------------------------------------------

CI_TOKEN = "jeton-du-workflow"
CI_COMMIT = "c" * 40


@pytest.fixture
def ci(repo, monkeypatch, tmp_path):
    """Un runner : jeton, dépôt et commit dans l'environnement ; ni `git credential` ni origin/main ne sont consultés."""
    def forbidden(*a, **k):
        raise AssertionError("--ci ne doit lire ni le gestionnaire d'identifiants ni origin/main")
    monkeypatch.setattr(release, "get_token", forbidden)
    monkeypatch.setattr(release, "origin_main", forbidden)
    summary = tmp_path / "resume.md"
    for k, v in (("GITHUB_TOKEN", CI_TOKEN), ("GITHUB_REPOSITORY", "o/r"), ("GITHUB_SHA", CI_COMMIT), ("GITHUB_STEP_SUMMARY", str(summary))):
        monkeypatch.setenv(k, v)
    return summary


def test_ci_draft_targets_the_built_commit_with_the_workflow_token(repo, gh, ci, capsys):
    assert release.main(["--ci", "--draft", "--api", gh.url], root=repo) == 0  # le dépôt vient de GITHUB_REPOSITORY
    out = capsys.readouterr()
    assert gh.created["target_commitish"] == CI_COMMIT and gh.created["name"] == "Monteur IA 1.1.0" and gh.created["draft"] is True
    assert gh.patched is None and set(gh.uploads) == {"MonteurIA-Setup-1.1.0.exe", "SHA256SUMS.txt"}
    assert gh.auth == {f"Bearer {CI_TOKEN}"} and CI_TOKEN not in out.out + out.err
    text = ci.read_text(encoding="utf-8")
    assert "en brouillon" in text and hashlib.sha256(INSTALLER).hexdigest() in text and CI_COMMIT in text


def test_ci_publishes_a_commit_of_main(repo, gh, ci):
    gh.compare = "ahead"  # main est en avance sur le commit construit : il le contient
    gh.tag_ref = {"ref": "refs/tags/v1.1.0", "object": {"type": "commit", "sha": CI_COMMIT}}  # le tag poussé qui a lancé le workflow
    assert run(repo, "--ci", gh=gh) == 0
    assert gh.created["target_commitish"] == CI_COMMIT and gh.patched == {"draft": False, "make_latest": "true"}
    assert "publiée" in ci.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["behind", "diverged", None])
def test_ci_never_publishes_a_commit_outside_main(repo, gh, ci, capsys, status):
    gh.compare = status
    assert run(repo, "--ci", gh=gh) == 1
    assert "n'est pas dans main" in capsys.readouterr().err
    assert gh.created is None and gh.uploads == {} and gh.patches == []
    # En brouillon, c'est permis, et le titre dit de ne pas publier.
    assert run(repo, "--ci", "--draft", gh=gh) == 0
    assert gh.created["name"] == "Monteur IA 1.1.0 (essai hors de main, ne pas publier)" and gh.patched is None


def test_ci_needs_the_workflow_environment(repo, gh, ci, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert run(repo, "--ci", gh=gh) == 1
    assert "GITHUB_TOKEN est vide" in capsys.readouterr().out and gh.requests == []
    monkeypatch.setenv("GITHUB_TOKEN", CI_TOKEN)
    monkeypatch.delenv("GITHUB_SHA")
    assert run(repo, "--ci", gh=gh) == 1 and gh.requests == []


# --- build.py ----------------------------------------------------------------------------------

def test_build_reads_the_version_without_importing_the_app(tmp_path):
    f = tmp_path / "version.py"
    f.write_text('"""x"""\n__version__ = "12.3.45"\n', encoding="utf-8")
    assert build.read_version(f) == "12.3.45"
    f.write_text('__version__ = "1.2"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        build.read_version(f)


def test_exe_version_resource_is_valid_pyinstaller_syntax(tmp_path):
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    f = tmp_path / "version.txt"
    f.write_text(build.version_resource("1.1.0"), encoding="utf-8")
    info = versioninfo.load_version_info_from_text_file(str(f))
    assert (info.ffi.fileVersionMS, info.ffi.fileVersionLS) == (0x00010001, 0)  # 1.1 puis 0.0
    assert (info.ffi.productVersionMS, info.ffi.productVersionLS) == (0x00010001, 0)
    strings = {s.name: s.val for t in info.kids[0].kids for s in t.kids}
    assert strings["FileVersion"] == strings["ProductVersion"] == "1.1.0"


def test_installer_script_relaunches_only_on_request():
    """Le .iss reste en UTF-8 avec BOM, et l'entrée [Run] n'est plus sautée en silencieux : ShouldLaunch décide."""
    raw = (ROOT / "desktop" / "installer.iss").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    run_line = next(ln for ln in text.splitlines() if ln.startswith('Filename: "{app}\\{#AppExe}"'))
    assert "skipifsilent" not in run_line and "Check: ShouldLaunch" in run_line and "postinstall" in run_line
    assert "function ShouldLaunch" in text and "(not WizardSilent) or" in text and "{param:RELAUNCH|0}" in text
