"""Publie une version de Monteur IA sur GitHub (release, installateur, empreinte SHA-256).

    python desktop/build.py --ffmpeg CHEMIN --installer      # d'abord : construit dist/installer/MonteurIA-Setup-<version>.exe
    python desktop/release.py --dry-run                       # contrôles locaux seulement, rien n'est envoyé
    python desktop/release.py                                 # crée la release, téléverse les fichiers, la publie
    python desktop/release.py --draft                         # idem, mais la laisse en brouillon (à publier à la main)
    python desktop/release.py --ci [--draft]                  # sur GitHub Actions (.github/workflows/release.yml)

Ce que fait le script, dans l'ordre :
  1. lit la version dans backend/app/version.py et vérifie que l'installateur de cette version existe ;
  2. calcule son SHA-256 et écrit dist/installer/SHA256SUMS.txt (« <empreinte>  <nom du fichier> ») ;
  3. prend les notes dans la section de cette version de CHANGELOG.md (« ## 1.1.0 - AAAA-MM-JJ » puis des puces) ;
  4. crée la release « vX.Y.Z » en BROUILLON sur le commit de origin/main, téléverse les deux fichiers en flux
     (l'installateur de 1,3 Go n'est jamais chargé en mémoire), vérifie leur taille côté GitHub ;
  5. la publie (sauf avec --draft). L'application installée la verra au prochain lancement et proposera la mise à jour.

Le jeton GitHub est lu par `git credential fill` (le même que pour `git push`) : il n'est ni affiché ni écrit.
Le script refuse si une release publiée existe déjà pour ce tag. Il ne touche qu'à l'API GitHub du dépôt.

Avec --ci (workflow GitHub Actions, qui construit l'installateur sur un runner pour ne pas téléverser 1,3 Go depuis un
poste) : le jeton, le dépôt et le commit viennent du workflow (GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA). Le commit
visé est celui que le runner a construit ; s'il n'est pas dans main, seule une release en brouillon est permise, et son
titre dit de ne pas la publier.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "7ShIkI3/Montage-video-ia"
API = "https://api.github.com"
UPLOADS_HOST = "uploads.github.com"
SUMS_NAME = "SHA256SUMS.txt"
MAX_ASSET = 2 * 1024 ** 3 - 1  # limite de GitHub par fichier de release
LOCAL_HOSTS = ("127.0.0.1", "localhost")
CHUNK = 1024 * 1024


class ReleaseError(Exception):
    """Un contrôle échoue ou GitHub refuse : le message, en français, dit quoi faire."""


# --- Contrôles locaux --------------------------------------------------------------------------

def read_version(root: Path = ROOT) -> str:
    text = (root / "backend" / "app" / "version.py").read_text(encoding="utf-8")
    m = re.search(r"""^__version__\s*=\s*["'](\d+\.\d+\.\d+)["']\s*$""", text, re.MULTILINE)
    if not m:
        raise ReleaseError('backend/app/version.py : __version__ = "X.Y.Z" introuvable.')
    return m.group(1)


def installer_path(version: str, root: Path = ROOT) -> Path:
    return root / "dist" / "installer" / f"MonteurIA-Setup-{version}.exe"


def sha256_file(path: Path, show=lambda done, total: None) -> str:
    h = hashlib.sha256()
    total, done = path.stat().st_size, 0
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            done += len(chunk)
            show(done, total)
    return h.hexdigest()


def changelog_notes(version: str, root: Path = ROOT) -> str:
    """Corps de la section « ## <version> - date » de CHANGELOG.md (jusqu'à la section suivante)."""
    path = root / "CHANGELOG.md"
    if not path.is_file():
        raise ReleaseError("CHANGELOG.md est introuvable à la racine du dépôt.")
    lines = path.read_text(encoding="utf-8").splitlines()
    head = re.compile(r"##\s+v?(\d+\.\d+\.\d+)\b")
    start = next((i for i, ln in enumerate(lines) if (m := head.match(ln)) and m.group(1) == version), None)
    if start is None:
        raise ReleaseError(f"CHANGELOG.md n'a pas de section « ## {version} - AAAA-MM-JJ ».")
    body: list[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    notes = "\n".join(body).strip()
    if not notes:
        raise ReleaseError(f"La section {version} de CHANGELOG.md est vide.")
    return notes


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if p.returncode:
        raise ReleaseError(f"git {' '.join(args)} a échoué : {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout.strip()


def origin_main(root: Path = ROOT) -> tuple[str, str | None]:
    """(commit de origin/main, version écrite dans backend/app/version.py à ce commit)."""
    commit = git(root, "rev-parse", "origin/main")
    try:
        text = git(root, "show", "origin/main:backend/app/version.py")
    except ReleaseError:
        return commit, None
    m = re.search(r"""^__version__\s*=\s*["'](\d+\.\d+\.\d+)["']""", text, re.MULTILINE)
    return commit, m.group(1) if m else None


def get_token(root: Path = ROOT) -> str:
    """Jeton GitHub du gestionnaire d'identifiants de Git (comme `git push`). Jamais affiché."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        p = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", cwd=root, env=env,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise ReleaseError("Impossible de lancer `git credential fill`.") from None
    for line in p.stdout.splitlines():
        if line.startswith("password=") and len(line) > len("password="):
            return line[len("password="):]
    raise ReleaseError("Aucun jeton GitHub dans le gestionnaire d'identifiants de Git : fais un `git push` une fois pour l'enregistrer.")


def ci_context(env=os.environ) -> tuple[str, str, str]:
    """Sur un runner GitHub Actions : (jeton, dépôt, commit construit), donnés par le workflow. Jamais affichés."""
    token, repo, commit = (str(env.get(k) or "").strip() for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA"))
    if not token:
        raise ReleaseError("--ci : GITHUB_TOKEN est vide (l'étape du workflow doit le passer dans `env`).")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError("--ci : GITHUB_SHA est absent, ce n'est pas un runner GitHub Actions.")
    return token, repo, commit


def ci_summary(lines: list[str], env=os.environ) -> None:
    """Résumé affiché sur la page du run (fichier donné par GITHUB_STEP_SUMMARY) ; sans effet ailleurs."""
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# --- API GitHub --------------------------------------------------------------------------------

class GitHub:
    def __init__(self, api: str, token: str, repo: str = REPO):
        u = urllib.parse.urlparse(api)
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in LOCAL_HOSTS):
            raise ReleaseError("L'adresse de l'API doit être en https (http seulement vers 127.0.0.1 ou localhost).")
        self.api, self.base, self.repo, self._token = api.rstrip("/"), u, repo, token
        self.default_api = u.netloc.lower() == urllib.parse.urlparse(API).netloc

    def _conn(self, u: urllib.parse.ParseResult) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        return cls(u.netloc, timeout=120)

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "MonteurIA-release", **(extra or {})}

    def call(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201, 204),
             missing_ok: bool = False) -> dict | None:
        conn = self._conn(self.base)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            conn.request(method, self.base.path.rstrip("/") + path, body=data,
                         headers=self._headers({"Content-Type": "application/json"} if data else None))
            r = conn.getresponse()
            raw = r.read()
        except (OSError, http.client.HTTPException) as e:
            raise ReleaseError(f"GitHub est injoignable ({e.__class__.__name__}).") from None
        finally:
            conn.close()
        if r.status == 404 and missing_ok:
            return None
        if r.status not in ok:
            msg = ""
            try:
                msg = str(json.loads(raw).get("message", ""))[:200]
            except (ValueError, AttributeError):
                pass
            hint = " Le jeton n'a sans doute pas le droit d'écrire dans ce dépôt." if r.status in (401, 403, 404) else ""
            raise ReleaseError(f"GitHub a refusé {method} {path} ({r.status} {msg}).{hint}")
        return json.loads(raw) if raw else {}

    def find_release(self, tag: str) -> dict | None:
        """La release de ce tag : publiée (recherche par tag) ou, à défaut, un brouillon (absent de la recherche par tag)."""
        found = self.call("GET", f"/repos/{self.repo}/releases/tags/{tag}", missing_ok=True)
        if found:
            return found
        for r in self.call("GET", f"/repos/{self.repo}/releases?per_page=50") or []:
            if isinstance(r, dict) and r.get("tag_name") == tag and r.get("draft"):
                return r
        return None

    def in_main(self, commit: str) -> bool:
        """Vrai si `commit` fait partie de l'historique de main. GitHub compare lui-même : pas besoin de l'historique
        en local (un runner n'a que le commit construit). « ahead » : main est en avance sur ce commit, donc le contient."""
        cmp = self.call("GET", f"/repos/{self.repo}/compare/{commit}...main", missing_ok=True)
        return bool(cmp) and cmp.get("status") in ("ahead", "identical")

    def upload(self, release: dict, path: Path, name: str, show=lambda done, total: None) -> dict:
        """Téléverse `path` comme fichier de la release, en flux : jamais plus d'un morceau en mémoire."""
        template = str(release.get("upload_url") or "")
        u = urllib.parse.urlparse(re.sub(r"\{\?[^}]*\}$", "", template))
        allowed = UPLOADS_HOST if self.default_api else self.base.netloc.lower()
        if u.netloc.lower() != allowed or (u.scheme != "https" and u.hostname not in LOCAL_HOSTS):
            raise ReleaseError("GitHub a donné une adresse de téléversement inattendue : arrêt.")
        size = path.stat().st_size
        conn = self._conn(u)
        try:
            conn.putrequest("POST", f"{u.path}?{urllib.parse.urlencode({'name': name})}")
            for k, v in self._headers({"Content-Type": "application/octet-stream", "Content-Length": str(size)}).items():
                conn.putheader(k, v)
            conn.endheaders()
            done = 0
            with path.open("rb") as f:
                while chunk := f.read(CHUNK):
                    conn.send(chunk)
                    done += len(chunk)
                    show(done, size)
            r = conn.getresponse()
            raw = r.read()
        except (OSError, http.client.HTTPException) as e:
            raise ReleaseError(f"Le téléversement de {name} a été interrompu ({e.__class__.__name__}).") from None
        finally:
            conn.close()
        if r.status != 201:
            raise ReleaseError(f"GitHub a refusé le fichier {name} ({r.status}).")
        return json.loads(raw)


def progress(label: str):
    state = {"t": 0.0}

    def show(done: int, total: int) -> None:
        now = time.time()
        if done >= total or now - state["t"] > 0.5:
            state["t"] = now
            print(f"\r  {label} : {done / 1e6:.0f} / {total / 1e6:.0f} Mo ({100 * done // max(total, 1)} %)", end="", flush=True)
            if done >= total:
                print()
    return show


# --- Déroulé -----------------------------------------------------------------------------------

def run(args: argparse.Namespace, root: Path = ROOT) -> int:
    version = read_version(root)
    tag, name = f"v{version}", f"MonteurIA-Setup-{version}.exe"
    print(f"Version {version} (tag {tag}){'  [essai à blanc : rien ne sera envoyé]' if args.dry_run else ''}")

    problems: list[str] = []
    installer = installer_path(version, root)
    sha = notes = commit = None
    if not installer.is_file():
        problems.append(f"{installer} est introuvable : construis-le avec `python desktop/build.py --ffmpeg CHEMIN --installer`.")
    elif installer.stat().st_size > MAX_ASSET:
        problems.append(f"{name} dépasse la limite de 2 Go d'un fichier de release GitHub.")
    else:
        print(f"Installateur : {installer} ({installer.stat().st_size / 1e6:.0f} Mo)")
        sha = sha256_file(installer, progress("Empreinte SHA-256"))
        print(f"  SHA-256 : {sha}")
    try:
        notes = changelog_notes(version, root)
        print(f"Notes : {len(notes.splitlines())} ligne(s) prises dans CHANGELOG.md")
    except ReleaseError as e:
        problems.append(str(e))
    warnings: list[str] = []
    token = None
    try:
        if args.ci:
            # Le runner a construit l'installateur à partir de ce commit : la version lue plus haut est la sienne.
            token, ci_repo, commit = ci_context()
            args.repo = args.repo or ci_repo
            print(f"Commit visé : celui que le workflow a construit = {commit[:10]}")
        else:
            commit, remote_version = origin_main(root)
            print(f"Commit visé : origin/main = {commit[:10]}")
            if remote_version != version:
                warnings.append(f"origin/main a la version {remote_version or 'inconnue'}, pas {version} : fusionne d'abord la branche de la version dans main "
                                f"(et `git fetch origin`), sinon le tag {tag} pointera sur un code d'une autre version.")
    except ReleaseError as e:
        problems.append(str(e))
    args.repo = args.repo or REPO
    problems += warnings  # un tag sur le mauvais code est une erreur, à blanc comme en vrai
    if args.dry_run:
        if problems:
            print("\nContrôles en échec :")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"\nTout est prêt. Sans --dry-run, le script ferait :\n"
              f"  1. écrire {installer.parent / SUMS_NAME} ({sha[:12]}…  {name})\n"
              f"  2. créer la release {tag} en brouillon sur {commit[:10]} (dépôt {args.repo})\n"
              f"  3. téléverser {name} et {SUMS_NAME}, puis vérifier leurs tailles\n"
              f"  4. {'laisser la release en brouillon (--draft)' if args.draft else 'publier la release'}")
        return 0
    if problems:
        print("\nImpossible de publier :")
        for p in problems:
            print(f"  - {p}")
        return 1
    assert sha and notes and commit

    sums = installer.parent / SUMS_NAME
    sums.write_text(f"{sha}  {name}\n", encoding="utf-8", newline="\n")
    gh = GitHub(args.api, token or get_token(root), args.repo)
    title = f"Monteur IA {version}"
    if args.ci and not gh.in_main(commit):
        # Publier poserait le tag sur un commit que main n'a pas : la version publique ne correspondrait à aucun code de main.
        if not args.draft:
            raise ReleaseError(f"Le commit construit ({commit[:10]}) n'est pas dans main : hors de main, seule une release "
                               "en brouillon est permise. Fusionne d'abord, puis relance depuis main. Rien n'a été publié.")
        title += " (essai hors de main, ne pas publier)"
        print(f"Le commit {commit[:10]} n'est pas dans main : brouillon d'essai, à ne pas publier.")
    existing = gh.find_release(tag)
    if existing and not existing.get("draft"):
        print(f"Une release publiée existe déjà pour {tag} ({existing.get('html_url')}) : rien n'est modifié.")
        return 1
    # Un tag déjà posé (release supprimée à la main, tag resté) : GitHub ignorerait `target_commitish` et publierait sur
    # l'ancien commit, alors que l'installateur vient du code actuel.
    ref = gh.call("GET", f"/repos/{args.repo}/git/ref/tags/{tag}", missing_ok=True)
    if ref:
        obj = ref.get("object") or {}
        if obj.get("type") == "tag":  # tag annoté (`git tag -a`) : la référence désigne l'objet tag, qui désigne le commit
            obj = (gh.call("GET", f"/repos/{args.repo}/git/tags/{obj.get('sha')}", missing_ok=True) or {}).get("object") or {}
        if obj.get("type") != "commit" or obj.get("sha") != commit:
            raise ReleaseError(f"Le tag {tag} existe déjà sur GitHub, sur un autre commit que celui visé ({commit[:10]}) : "
                               "supprime ce tag à la main si c'est une erreur, puis relance. Rien n'a été publié.")
    wanted = {"tag_name": tag, "target_commitish": commit, "name": title, "body": notes, "draft": True, "prerelease": False}
    if existing:  # brouillon d'un essai précédent : on le reprend et on remplace ses fichiers
        release = existing
        for a in existing.get("assets") or []:
            if a.get("name") in (name, SUMS_NAME):
                gh.call("DELETE", f"/repos/{args.repo}/releases/assets/{a['id']}")
        # Le brouillon peut dater d'un autre commit : on le réaligne sur le code de cet installateur (commit, titre, notes).
        gh.call("PATCH", f"/repos/{args.repo}/releases/{release['id']}", wanted)
        print(f"Brouillon existant repris (release {release['id']}), réaligné sur {commit[:10]}.")
    else:
        release = gh.call("POST", f"/repos/{args.repo}/releases", wanted)
        print(f"Release {tag} créée en brouillon (id {release['id']}).")
    try:
        gh.upload(release, sums, SUMS_NAME)
        gh.upload(release, installer, name, progress(f"Envoi de {name}"))
        check = gh.call("GET", f"/repos/{args.repo}/releases/{release['id']}")
        received = {a.get("name"): a for a in check.get("assets") or []}
        confirmed = 0
        for local, label, local_sha in ((installer, name, sha), (sums, SUMS_NAME, hashlib.sha256(sums.read_bytes()).hexdigest())):
            got = received.get(label)
            if not got or got.get("size") != local.stat().st_size or got.get("state") not in (None, "uploaded"):
                raise ReleaseError(f"GitHub n'a pas bien reçu {label} (taille {got.get('size') if got else 'absente'}).")
            if got.get("digest"):  # empreinte que GitHub calcule sur le fichier reçu (« sha256:… »), quand l'API la donne
                if got["digest"] != f"sha256:{local_sha}":
                    raise ReleaseError(f"GitHub a reçu {label} avec une autre empreinte SHA-256 que le fichier local.")
                confirmed += 1
        print("Fichiers reçus par GitHub, tailles vérifiées" + (", empreintes SHA-256 confirmées par GitHub." if confirmed == 2 else "."))
    except ReleaseError as e:
        print(f"{e}\nLa release {tag} reste en brouillon : relance le script pour la reprendre.", file=sys.stderr)
        return 1
    facts = [f"- Fichier : `{name}` ({installer.stat().st_size / 1e6:.0f} Mo)", f"- SHA-256 : `{sha}`", f"- Commit : `{commit}`"]
    if args.draft:
        print(f"Laissée en brouillon : {release.get('html_url')}")
        if args.ci:
            ci_summary([f"### {title} : release {tag} en brouillon", *facts, f"- À relire, puis à publier à la main : {release.get('html_url')}"])
        return 0
    done = gh.call("PATCH", f"/repos/{args.repo}/releases/{release['id']}", {"draft": False, "make_latest": "true"})
    print(f"Release publiée : {done.get('html_url')}")
    if args.ci:
        ci_summary([f"### {title} : release {tag} publiée", *facts, f"- {done.get('html_url')}"])
    return 0


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description="Publie une version de Monteur IA sur GitHub.")
    ap.add_argument("--dry-run", action="store_true", help="contrôles locaux seulement : rien n'est envoyé ni écrit")
    ap.add_argument("--draft", action="store_true", help="laisse la release en brouillon au lieu de la publier")
    ap.add_argument("--ci", action="store_true", help="sur GitHub Actions : jeton, dépôt et commit donnés par le workflow")
    ap.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--api", default=os.environ.get("MONTEUR_RELEASE_API", API), help=argparse.SUPPRESS)  # faux serveur d'essai
    args = ap.parse_args(argv)
    try:
        return run(args, root)
    except ReleaseError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
