# Monteur IA : règles du projet

Logiciel de montage vidéo assisté par IA. Backend FastAPI (`backend/app`), interface React + TypeScript + Vite +
Zustand (`frontend/src`), application de bureau pywebview + PyInstaller et installateur Inno Setup (`desktop/`).

## Vérifier (règle n°1)

| Ce qui a changé | Commande |
|---|---|
| Backend | `backend/.venv/Scripts/python.exe -m pytest -q` depuis `backend/` (5 à 6 minutes, toute la suite) |
| Interface | dans `frontend/` : `npx tsc -p tsconfig.json --noEmit`, `npm test`, `npm run check`, `npm run build` |
| À l'écran | `preview_start` avec `monteur-dev` (port 8010, données d'essai), à 1440×900 et à 1280×720 |
| Application | `cd frontend && npx vite build`, puis `python desktop/build.py --ffmpeg <dossier> --installer` |

- Un montage se juge sur un vrai rush et sur des images, pas seulement sur des tests.
- L'application installée écoute sur le port 8000 avec les vraies données (`Documents\Monteur IA`) : ne pas la
  fermer ni la remplacer sans accord, ne jamais supprimer ces données. Les essais se font sur 8010.

## Interface : la méthode

Toute modification de `frontend/src` suit `docs/design/DESIGN.md` (identité, jetons, mise en page, interdits).

1. Avant de dessiner un écran ou un composant nouveau : des références réelles (agent `reference-researcher`,
   ou les captures fournies par Raphaël).
2. Pendant : seulement des jetons de `:root`, un seul bouton plein par zone, texte à 11 px ou plus, aucune
   fonctionnalité, `data-testid`, `id` ou raccourci perdu.
3. Après : regarder l'écran aux deux tailles, puis faire relire par l'agent `ui-critic` et corriger ce qu'il
   confirme. Ne pas dire « c'est fait » sur du code d'interface qui n'a pas été vu à l'écran.

Une règle de design qui gêne se change d'abord dans `DESIGN.md`, avec la raison, puis dans le code.

## IA et appels payants

- Les appels à Claude passent par `backend/app/ai` (modèle `claude-opus-5-5`, sorties structurées). Le skill
  `claude-api` fait référence avant de toucher à ces appels.
- Aucun appel payant en boucle ou en lot sans accord : quelques appels unitaires pour vérifier, signalés.
  Idem pour Krea (génération d'images) : quelques essais à la fois.
- Les clés restent en variable d'environnement ou dans les réglages de l'app, jamais dans le code ni les journaux.

## Git

Jamais de commit sur `main` : une branche, une PR, des messages conventionnels en français. Ce dépôt n'a pas
`gh` : les PR passent par l'API GitHub avec le jeton du gestionnaire d'identifiants Git, sans jamais l'afficher.

## Publier une version

1. `backend/app/version.py` (`__version__`) donne le numéro à l'application : le changer, puis compléter `CHANGELOG.md`.
   Mettre la même version dans `frontend/package.json`, `frontend/package-lock.json` (deux fois) et le `#define AppVersion`
   de `desktop/installer.iss` : `npm run check` et pytest échouent s'ils diffèrent.
2. Fusionner la branche dans `main` par une PR (jamais de commit sur `main`).
3. Laisser GitHub Actions construire et déposer l'installateur (`.github/workflows/release.yml`) : l'envoyer depuis le
   poste de Raphaël prend des heures (1,3 Go, connexion lente) et ne se reprend pas. Deux déclencheurs :
   - à la main, onglet Actions, « Publier une version », sur `main`, « brouillon » coché : release `vX.Y.Z` en
     brouillon, avec `MonteurIA-Setup-X.Y.Z.exe`, `SHA256SUMS.txt` et les notes de `CHANGELOG.md` ; on l'essaie, puis
     on la publie sur GitHub ;
   - `git tag vX.Y.Z origin/main && git push origin vX.Y.Z` : même construction, release publiée à la fin.
4. Voie de secours depuis le poste : `git fetch origin`, `cd frontend && npm ci && npm run build`,
   `python desktop/build.py --ffmpeg <dossier> --installer`, puis `python desktop/release.py --dry-run` (contrôles sans
   rien envoyer) et `python desktop/release.py` (`--draft` : brouillon). `release.py` vise `origin/main` et refuse si la
   version de `main` n'est pas celle du fichier.

Le workflow publie avec `desktop/release.py --ci` : garder les contrôles dans ce script (testé par
`backend/tests/test_release.py`), pas dans le YAML. Un fichier de workflow ne se lance à la main qu'une fois présent
dans `main` ; pour l'essayer avant la fusion, lui ajouter le temps de l'essai un déclencheur `push` sur sa branche :
hors de `main`, il ne produit qu'un brouillon marqué « ne pas publier ».

Publier une release est public : toujours demander l'accord de Raphaël avant (lancer le workflow sans « brouillon » ou
pousser un tag `v*`, c'est publier). Les applications installées interrogent `releases/latest` du dépôt : ne jamais
renommer l'installateur ni supprimer `SHA256SUMS.txt`.
