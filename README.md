# Monteur IA

Un logiciel de montage vidéo où **l'IA fait le montage** : tu importes tes rushes, tu cliques sur « Monter avec
l'IA », et tu obtiens une vidéo montée, sous-titrée, avec musique et volume réglé. Tout reste retouchable à la
souris ou en français (« fais une version de 30 secondes »). L'analyse et le rendu se font sur ton ordinateur ;
seul le réalisateur peut appeler Claude (clé facultative), et l'application fonctionne aussi sans.

## Télécharger et installer (Windows)

1. Ouvre la [dernière version](https://github.com/7ShIkI3/Montage-video-ia/releases/latest) et télécharge
   `MonteurIA-Setup-<version>.exe` (environ 1,3 Go).
2. Double-clique dessus. Il faut Windows 10 ou 11 en 64 bits. Aucun droit d'administrateur n'est demandé :
   l'application s'installe dans `%LOCALAPPDATA%\Programs\Monteur IA`, avec un raccourci dans le menu Démarrer et,
   sur le Bureau (case cochée par défaut, tu peux la décocher).
3. **Windows SmartScreen** peut afficher « Windows a protégé votre ordinateur », parce que l'installateur n'est pas
   signé. Clique sur « Informations complémentaires », puis sur « Exécuter quand même ».
4. Facultatif : vérifie que le fichier est bien celui publié. Dans PowerShell, à côté du fichier :
   `Get-FileHash .\MonteurIA-Setup-<version>.exe -Algorithm SHA256`, puis compare avec la ligne du fichier
   `SHA256SUMS.txt` de la même page.

FFmpeg est embarqué. Les bibliothèques NVIDIA (2 Go, pour transcrire sur la carte graphique) ne sont copiées que si
l'installateur trouve une carte NVIDIA ; sinon la transcription se fait sur le processeur.

Pour désinstaller : « Applications installées » de Windows. Tes projets ne sont pas supprimés.

## Mettre à jour

Au lancement, l'application demande à GitHub s'il existe une version plus récente. Elle n'envoie rien de toi
(seulement son nom et son numéro de version) et ne télécharge rien toute seule.

- Si une version plus récente existe, un bandeau discret apparaît sur l'accueil (et une pastille dans la barre d'état
  de l'éditeur). « Voir les nouveautés » ouvre les notes de version. « Mettre à jour » télécharge alors l'installateur
  (barre de progression, annulable) et l'application vérifie son empreinte SHA-256. « Installer et relancer » ferme
  l'application, remplace les fichiers et la rouvre. Chaque étape demande ton clic.
- « Ignorer cette version » masque l'annonce pour cette version seulement ; « Plus tard » la laisse en attente.
- La mise à jour depuis l'application n'existe que dans l'application installée. Lancée depuis les sources, l'application dit
  seulement qu'une version existe.
- Pour vérifier à la demande, ou couper la vérification au lancement : Réglages (engrenage), section À propos. Le
  variable d'environnement `MONTEUR_UPDATE_CHECK=0` coupe aussi toute vérification (pour les essais).
- Tu peux toujours mettre à jour à la main en lançant le nouvel installateur par-dessus l'ancien.

**Où sont tes données** : dans `Documents\Monteur IA` (projets, exports, réglages, journal `monteur.log`, voix et
modèles téléchargés). Tes projets et tes réglages ne sont jamais touchés, ni par une mise à jour ni par la
désinstallation. Pour sauvegarder ton travail, copie ce dossier, sauf son sous-dossier `updates` : c'est un dossier
temporaire où l'installateur d'une mise à jour (environ 1,3 Go) attend d'être lancé, puis il est effacé au démarrage
suivant.

Au premier montage, l'application télécharge le modèle de transcription Whisper « small » (environ 480 Mo, depuis
Hugging Face) ; au premier détourage, un petit modèle de segmentation (250 Ko, depuis storage.googleapis.com). Le
reste du traitement se fait sur ton ordinateur.

## Premiers pas

1. Sur l'accueil, crée un projet, ou clique sur **Essayer sans vidéo** : l'application monte, sous tes yeux, un clip
   de combat PvP en voxels qu'elle a généré elle-même.
2. Importe tes rushes (module **Médias**, section Importer, ou glisser-déposer). Un lien YouTube, Vimeo ou un fichier
   direct marche aussi.
3. Choisis un style (YouTube, Short, Vlog, Podcast, Edit PvP) et une durée dans le module **IA**, puis clique sur
   **Monter avec l'IA**.
4. Retouche : dans la timeline, avec le chat du réalisateur (onglet **Chat**) ou en tapant une consigne.
5. Contrôle l'onglet **Qualité**, puis exporte depuis l'onglet **Export**.

## Clé Claude et appels payants

Sans clé, tout marche avec un réalisateur local : il comprend les consignes courantes (durée, format, sous-titres,
musique, zooms, « enlève "mot" ») et un relecteur local note le résultat.

Avec une clé API Anthropic, le réalisateur est **Claude** : il comprend le sens du discours, choisit les meilleurs
passages, écrit un titre et applique des consignes libres. Colle la clé dans Réglages (engrenage en haut à droite),
section Claude ; elle reste dans `settings.json`, sur ton ordinateur. Chaque appel est facturé par Anthropic sur ton
compte. Sont payants : le montage par Claude, le relecteur, le chat du réalisateur, le jury de l'edit PvP,
l'analyse visuelle des rushes (bouton « Regarder », jamais lancé tout seul), la recherche web, les traductions et le
doublage. Le modèle et l'effort se règlent au même endroit.

## Ce que fait l'IA

1. **Analyse** chaque rush (silences, pics sonores, mouvement, changements de plan) et **transcrit** la parole au
   mot près (faster-whisper, langue détectée toute seule).
2. **Découpe** en phrases et moments sans jamais couper un mot, repère les « euh » et les prises répétées (garde la
   dernière), **note** chaque moment.
3. **Réalise** : choix et ordre des plans selon le style et la durée cible, accroche en ouverture pour les shorts,
   zooms d'emphase. Chaque plan gardé ou écarté est justifié (panneau « Pourquoi », passages écartés réintégrables).
4. **Habille** : sous-titres animés mot par mot, musique qui baisse sous la voix, volume normalisé à -14 LUFS avec
   un limiteur de crête, projecteur sur ce que la voix désigne, identité de marque tirée d'un logo ou d'un site.
5. **Recadre** sur le visage pour passer du 16:9 au 9:16 ou au 1:1.
6. **Exporte** en 16:9, 9:16 ou 1:1 (MP4, WebM, MOV ou GIF) avec l'encodeur matériel de la machine (NVENC,
   QuickSync, AMF, VideoToolbox) ou x264, plus un `.srt`, ou en **XML** pour Premiere Pro, DaVinci Resolve ou
   Final Cut Pro. Elle prépare aussi le kit de publication : titres, description avec chapitres YouTube, hashtags,
   miniature.
7. **Moteur 3D** (logiciel) : transitions cube, retournement, porte ; textes en relief ; piste **Impacts** (secousse,
   zoom choc, flash, étincelles, onde de choc).

Autour du montage :

- **Trois directions créatives** : l'IA regarde les rushes et propose trois façons de raconter la vidéo, une
  recommandée. **Monter comme une vidéo de référence** : elle copie le rythme d'un montage que tu aimes.
- **Réalisation en direct** : pendant le montage, le lecteur affiche les étapes du réalisateur. À la fin, son
  **Pitch** présente le montage (bouton dans l'onglet Plan quand aucun plan n'est sélectionné).
- **Contrôle qualité** : règles du réalisateur vérifiées à chaque montage (parole dès la première seconde, pas plus
  de 3 flashs par seconde, textes lisibles sur téléphone…), vérification de la vidéo finie (loudness, crête,
  début noir ou muet), affirmations trop fortes adoucies. Un **relecteur indépendant** (un second appel à Claude) note
  sur 10 ; sous 7, le réalisateur retouche et la version repasse en relecture, 3 tours au plus.
- **Voix** : voix off IA et doublage (Claude traduit, une voix de synthèse lit) réécoutés par Whisper ;
  sous-titres traduits ; voix Piper naturelles à télécharger dans Réglages, sinon espeak-ng s'il est installé.
- **Autres** : détourage sans fond vert, étalonnage par LUT (5 intégrées ou `.cube`), écran partagé, images clés,
  rampes de vitesse, musiques libres de droits (ccMixter) et téléchargement depuis un lien (yt-dlp).

## L'éditeur

- **Six modules à gauche** : Médias, IA, Audio, Texte, Effets, Projet (versions, modèles, marque).
- **Neuf onglets à droite**, touches `1` à `9` : Chat (le réalisateur), Film, Plan, Look, Mixage, Qualité, Export,
  Script (transcription), Web (recherche web). Le plan sélectionné s'ouvre dans Plan, avec cinq sous-onglets : Vidéo,
  Audio, Vitesse, Animation, Ajuster.
- **Montage à la main** : un clic sur un rush l'ouvre dans le moniteur source (marques `I` / `O`) ; `,` ou `Entrée`
  l'insère au curseur, `.` l'ajoute à la fin. `S` coupe, `Suppr` supprime, `Ctrl+Z` / `Ctrl+Y` annulent et
  rétablissent, `Ctrl+C` / `X` / `V` copient, coupent et collent, `Ctrl+clic` et `Maj+clic` sélectionnent plusieurs
  plans, `M` pose un repère (ceux « chapitre » deviennent les chapitres YouTube), `J` / `K` / `L` lisent.
  `?` affiche tous les raccourcis, `Ctrl+K` ouvre la palette de commandes : une phrase qui n'est pas une commande
  part en consigne à l'IA.
- **Réglages par plan** : volume, fondus, couleur, vitesse, zoom, effets, transition. Le mixeur audio règle voix,
  musique, bruitages et pistes (jusqu'à 4 pistes audio, voix off enregistrée au micro).
- **Chat du réalisateur** : un agent Claude lit le dossier des rushes (coups, combos, écrans rouges, paroles,
  montage plan par plan), regarde des images quand il en a besoin, cherche sur le web, et agit : refaire le
  découpage, retoucher un plan, régler le mixage, poser un texte. Tout changement s'annule par `Ctrl+Z`. Sans clé,
  le message est appliqué comme une consigne simple.
- **Espace de travail** : panneaux redimensionnables et repliables, mémorisés ; « Espace de travail par défaut »
  remet tout en place.
- **Sauvegardes** : une copie datée au plus toutes les 5 minutes, les 10 dernières gardées (onglet Film,
  « Restaurer »). Si `project.json` est abîmé, la dernière sauvegarde est rechargée toute seule.

## L'edit PvP

Le style **Edit PvP** monte les meilleurs moments d'un rush de combat (PvP Minecraft, filmé à la première
personne). Il ne montre que **les combos que tu donnes**, jamais ceux que tu subis.

1. Les coups sont détectés dans le son et regroupés en rafales au rythme d'un combo, hors menus et inventaires.
2. Le son ne dit pas qui frappe. Deux mesures locales, gratuites, lisent l'image : la **barre de vie** (cœurs lus sur
   les pixels) et le **roulis de la caméra** (la vue penche quand tu es touché). Une rafale subie est écartée d'office,
   et jamais un passage où ta vie chute de plus d'un cœur n'est montré.
3. Avec une clé Claude, un **jury IA** regarde les meilleures rafales restantes (14 au plus, quatre images et deux
   recadrages de la barre de vie chacune, les fins de combat d'abord) et ne garde que celles à ton avantage. Les
   verdicts sont gardés avec le rush : refaire le montage ne repaie pas. Sans clé, le classement se fait au son et aux
   deux mesures locales, et l'application le dit.
4. Un **kill** est montré sans la raclée qui le précède : seuls ses derniers coups, puis un ralenti.
5. Les coupes tombent sur les temps de la musique (un rythme électro est généré si tu n'en choisis pas), le pic de la
   musique se pose sur le kill, et l'habillage reste sobre pour que le final ressorte.
6. S'il manque de bons combos, le montage est **plus court** que la durée cible et son résumé le dit, au lieu de
   se remplir de combos subis.

Le chat du réalisateur monte le même edit (outil `monter_edit_pvp`), avec la musique et le tempo.

## Développeur

### Prérequis

Windows 10 ou 11 (le dépôt se développe sous Windows), Python 3.12, Node 18 ou plus, FFmpeg (avec libass) dans le
PATH, et [Inno Setup 6](https://jrsoftware.org/isinfo.php) pour l'installateur
(`winget install --id JRSoftware.InnoSetup -e`).

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
cd ..\frontend
npm ci
```

`pip install -r backend\requirements-cuda.txt` ajoute les bibliothèques CUDA (cuBLAS, cuDNN) pour transcrire sur une
carte NVIDIA ; sans elles, Whisper retombe tout seul sur le processeur et le signale dans les avertissements.

### Lancer en développement

`.claude/launch.json` définit deux serveurs, utilisés par l'application de bureau Claude Code :

- `monteur` : port 8000, avec les vraies données (`Documents\Monteur IA`) ;
- `monteur-dev` : port 8010, avec des données d'essai (`%LOCALAPPDATA%\Temp\monteur-dev`). C'est lui qu'on utilise
  pour les essais.

À la main, depuis la racine, après `cd frontend && npm run build` (l'API sert alors l'interface sur le même port) :

```powershell
$env:MONTEUR_DATA_DIR = "$env:LOCALAPPDATA\Temp\monteur-dev"
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8010
```

Pour le rechargement à chaud : le même serveur avec `--reload --port 8000`, puis `cd frontend && npm run dev`
(http://localhost:5173, qui renvoie `/api` vers le port 8000). `python desktop/launcher.py` ouvre l'application
de bureau (fenêtre pywebview, sinon le navigateur).

### Vérifier

| Ce qui a changé | Commande |
| --- | --- |
| Backend | `cd backend` puis `.venv\Scripts\python.exe -m pytest -q` (5 à 6 minutes, FFmpeg requis) |
| Interface | dans `frontend/` : `npx tsc -p tsconfig.json --noEmit`, `npm test`, `npm run check`, `npm run build` |

Les tests du backend tournent sans clé Claude ni Whisper (`MONTEUR_DISABLE_CLAUDE` et `MONTEUR_DISABLE_WHISPER`) et
dans un dossier de données temporaire. Le test de bout en bout crée un rush synthétique, lance le montage, retouche,
applique une consigne, exporte et vérifie la vidéo. Les règles de l'interface sont dans
[docs/design/DESIGN.md](docs/design/DESIGN.md) ; [CLAUDE.md](CLAUDE.md) donne les règles de travail du projet.

### Construire l'application et l'installateur

```powershell
cd frontend; npm ci; npm run build; cd ..
backend\.venv\Scripts\python.exe -m pip install -r desktop\requirements.txt
backend\.venv\Scripts\python.exe desktop\build.py --ffmpeg <dossier de ffmpeg et ffprobe> --installer
```

`desktop/build.py` refuse de partir si l'interface construite est plus ancienne que ses sources. Il produit
`dist/Monteur IA/` (PyInstaller) puis `dist/installer/MonteurIA-Setup-<version>.exe`. `--installer-only` remballe un
`dist/Monteur IA` déjà construit ; `desktop/make_icon.py` redessine l'icône. Le numéro de version est lu dans
`backend/app/version.py` (voir « Publier une version » pour les deux autres endroits à tenir à jour).

Sans l'option `--installer`, `desktop/build.py` produit seulement le dossier PyInstaller, aussi sur macOS ou Linux (à
construire sur la machine cible). L'installateur, lui, n'existe que pour Windows.

**Doublage** : le doublage se fait avec `espeak-ng` (Linux : `apt install espeak-ng`, macOS : `brew install espeak-ng`).
Pour des voix naturelles, `pip install piper-tts` et dépose une voix Piper (`fr_FR-siwis-medium.onnx` et son `.json`)
dans `<données>/voices` (ou télécharge-les dans les réglages). **Musique** : dépose des fichiers audio libres de droits
dans `<données>/music` ; un fichier dont le nom contient le style (`vlog`, `short`…) est choisi en priorité.

### Publier une version

1. Change `__version__` dans `backend/app/version.py` et ajoute la section de la version dans `CHANGELOG.md`. Mets la
   même version dans `frontend/package.json` et `frontend/package-lock.json` (deux fois), et dans le
   `#define AppVersion` de `desktop/installer.iss` : `npm run check` et pytest vérifient qu'ils concordent.
2. Fusionne la branche de la version dans `main` (par une PR, on ne commite pas sur `main`), puis `git fetch origin` :
   `release.py` vise le commit de `origin/main` et refuse de partir si la version de `main` n'est pas celle du fichier.
3. Construis l'installateur (ci-dessus) à partir de ce code fusionné, sinon le tag pointerait sur un autre code que
   celui de l'exe.
4. `python desktop/release.py --dry-run` fait les contrôles locaux sans rien envoyer.
5. `python desktop/release.py` écrit `dist/installer/SHA256SUMS.txt` (lignes `<sha256>  <nom du fichier>`), crée la
   release `vX.Y.Z` sur GitHub avec les notes de la section de `CHANGELOG.md` (`## X.Y.Z - AAAA-MM-JJ`), y téléverse
   `MonteurIA-Setup-X.Y.Z.exe` et `SHA256SUMS.txt`, puis la publie (`--draft` la laisse en brouillon). Le jeton GitHub
   vient de `git credential fill`, comme pour `git push`. C'est cette release que les applications installées
   interrogent (`https://api.github.com/repos/7ShIkI3/Montage-video-ia/releases/latest`) pour proposer la mise à jour.

Publier est public et définitif : on le fait avec l'accord du propriétaire du dépôt.

### Réglages et variables d'environnement

Une variable d'environnement l'emporte sur `settings.json`, qui l'emporte sur la valeur par défaut. La clé Claude, les
modèles, l'appareil et la langue Whisper, le port (8000 par défaut) et la vérification des mises à jour se règlent
aussi dans l'application.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | aucune | Active Claude comme réalisateur |
| `MONTEUR_DISABLE_CLAUDE` | non définie | Si définie, coupe Claude même avec une clé (tests) |
| `MONTEUR_CLAUDE_MODEL` | `claude-opus-5-5` | Modèle utilisé |
| `MONTEUR_CLAUDE_EFFORT` | `medium` | Effort de réflexion (`low` à `max`) |
| `MONTEUR_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `MONTEUR_WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `MONTEUR_WHISPER_LANGUAGE` | détection | Forcer une langue (`fr`, `en`…) |
| `MONTEUR_DISABLE_WHISPER` | non définie | Si définie, coupe la transcription (tests) |
| `MONTEUR_DATA_DIR` | `data/` à la racine du dépôt | Projets, caches, exports ; l'application de bureau le fixe à `Documents\Monteur IA` |
| `MONTEUR_MUSIC_DIR` | `<données>/music` | Bibliothèque musicale |
| `MONTEUR_PIPER_VOICES` | `<données>/voices` | Voix Piper (`<langue>*.onnx`) |
| `MONTEUR_SEGMENTER_MODEL` | `<données>/models/selfie_segmenter.tflite`, téléchargé | Modèle de détourage |
| `MONTEUR_FRONTEND_DIST` | `frontend/dist` | Interface servie par l'API |
| `MONTEUR_FFMPEG`, `MONTEUR_FFPROBE` | trouvés dans le PATH | Chemin de FFmpeg et FFprobe |
| `MONTEUR_WORKERS` | `3` | Tâches en parallèle |
| `MONTEUR_PROXY_HEIGHT` | `540` | Hauteur des proxys de lecture |
| `MONTEUR_DEMO_CLIP` | clip embarqué ou rendu | Vidéo utilisée par « Essayer sans vidéo » |
| `MONTEUR_UPDATE_URL` | flux GitHub Releases du dépôt | Autre flux de versions (essais) |
| `MONTEUR_UPDATE_CHECK` | actif | `0` coupe toute vérification de mise à jour |

### Structure du dépôt

```
backend/app/
  main.py, pipeline.py, jobs.py   API HTTP, orchestration, tâches en arrière-plan (progression, annulation)
  settings.py, config.py          réglages persistants, variables d'environnement
  version.py, updates.py          numéro de version (source unique), mises à jour ; lifecycle.py : arrêt de l'application
  storage.py, models.py           projets sur disque, modèles de données (timeline)
  doctor.py, downloads.py         diagnostic du poste, téléchargements (vidéos, musiques, modèles, voix)
  media/                          ffmpeg (probe, proxys, encodeur), clip de démo
  analysis/                       audio, vidéo, transcription, coups (hits), visage, vie et caméra (duel)
  ai/                             réalisateurs (director), chat à outils, segments, pvp, jury, qualité, relecteur,
                                  directions, voix, traduction, marque…
  render/                         rendu FFmpeg, sous-titres, effets, LUT, moteur 3D, export XML
backend/tests/                    pytest
frontend/src/                     React, TypeScript, Vite, Zustand : components/ (left, right, tabs, shell, ui),
                                  store.ts, api.ts, labels.ts (libellés), styles/
frontend/tests/                   tests du store et contrôle de cohérence de l'interface
desktop/                          launcher.py, build.py, release.py, installer.iss, make_icon.py
docs/design/DESIGN.md             règles de design
scripts/                          dev.sh, make_pvp_clip.py
.claude/                          launch.json (serveurs de développement), agents de relecture d'interface
```

### Scripts

- `scripts/make_pvp_clip.py sortie.mp4 [--seconds 40] [--fps 30] [--size 1280x720]` génère le clip de combat en
  voxels de « Essayer sans vidéo » (rendu dans `backend/app/media/demo.py`).
- `scripts/dev.sh` lance l'API (port 8000) et l'interface Vite (port 5173) avec rechargement à chaud, sous bash
  (Git Bash, Linux, macOS). Il prend le Python de `backend/.venv` s'il existe.

### Licence

Le dépôt ne contient pas de fichier de licence.
