# Montage vidéo IA

Application de montage vidéo où **l'IA fait le montage toute seule** : tu importes tes rushes, tu cliques sur
« Monter avec l'IA », et tu obtiens une vidéo montée, sous-titrée, avec musique et volume normalisé. Tout reste
retouchable à la main (timeline) ou en français (« fais une version de 30 secondes »).

Le plan détaillé et la stratégie sont dans [PLAN.md](PLAN.md).

## Ce que fait l'IA

1. **Analyse** chaque rush : silences, pics sonores (rires, réactions), mouvement, changements de plan.
2. **Transcrit** la parole au mot près (faster-whisper, détection automatique de la langue).
3. **Découpe** en phrases et moments, sans jamais couper au milieu d'un mot ; repère les « euh » et les prises
   répétées (garde la dernière).
4. **Note** chaque moment (énergie, débit, accroche, mouvement…).
5. **Réalise** le montage selon le style (YouTube, Short, Vlog, Podcast) et la durée cible : choix et ordre
   des plans, accroche en ouverture pour les shorts, zooms d'emphase et zooms qui masquent les coupes franches.
   Chaque plan gardé ou écarté est justifié.
6. **Habille** : sous-titres animés mot par mot, musique de fond qui baisse automatiquement sous la voix,
   volume normalisé à -14 LUFS.
7. **Recadre** automatiquement sur le visage quand on passe du 16:9 au 9:16 ou au 1:1 (suivi OpenCV lissé
   comme un cadreur, remis à zéro à chaque changement de plan).
8. **Exporte** en 16:9, 9:16 ou 1:1 (1080p ou 720p) avec l'encodeur matériel de ta machine (NVENC,
   QuickSync, VideoToolbox, AMF) ou x264, plus un fichier `.srt`, ou en **XML** pour finir dans Premiere Pro,
   DaVinci Resolve ou Final Cut Pro.
9. **Prépare la publication** : titres, description avec chapitres YouTube, hashtags et miniature.
10. **Moteur 3D** (logiciel, NumPy + OpenCV) : caméra en perspective, transitions cube / retournement / porte,
    écran en orbite ou flottant, bascule et rotation d'entrée, textes en relief animés, et une piste
    **Impacts** (secousse 3D, zoom choc, flash, flash rouge, étincelles 3D, onde de choc).
11. **Edit PvP** (gaming) : détecte les coups dans le son, regroupe les combos, garde les meilleurs, met un
    ralenti sur le coup final, cale chaque coupe sur le tempo de la musique (rythme électro généré si aucune
    musique n'est choisie) et pose secousses, étincelles, flashs et « COMBO xN » en 3D.

Avec une clé API Anthropic, le réalisateur est **Claude** : il comprend le sens du discours, choisit les
meilleurs passages, écrit un titre et applique des consignes libres. Sans clé, un réalisateur local
heuristique fait le travail et comprend les consignes courantes (durée, format, sous-titres, musique, zooms,
« enlève "mot" »).

## L'éditeur

- Lecture de la timeline en direct (sans rendu), sous-titres en surimpression, aperçu du format.
- Timeline : glisser-déposer pour réordonner, poignées pour raccourcir, `S` pour couper, `Suppr`,
  `Ctrl+Z` / `Ctrl+Y`, zoom, forme d'onde.
- Panneau « Pourquoi » : raison et score de chaque plan, passages écartés réintégrables en un clic.
- Texte : cliquer un mot pour y aller, Maj+clic pour sélectionner et couper des mots, double-clic pour
  corriger un sous-titre.
- « Demande à l'IA » : consignes en langage naturel, annulables comme une retouche.
- Images clés : zoom et cadrage animés sur un plan (zoom avant, arrière, panoramique, zoom choc ou à la
  main), rendus à l'identique à l'export.
- Rampes de vitesse : la vitesse passe progressivement d'une valeur à une autre sur le plan, son compris.
- Pistes audio : jusqu'à 4 pistes pour la voix off (enregistrée au micro depuis le curseur), des sons et
  des musiques, avec volume et fondus ; la musique de fond baisse sous la voix off.
- Sous-titres traduits : choisis une langue, Claude traduit chaque ligne en gardant son minutage
  (traductions gardées en cache, sous-titres .srt et vidéo dans la langue choisie).
- Doublage IA : Claude traduit chaque phrase, une voix de synthèse la lit à l'instant où elle est dite
  (accélérée au besoin pour tenir dans le temps), sur la piste audio 4, et la voix d'origine baisse.
  Voix Piper (naturelles) si installées, sinon espeak-ng.
- Écran partagé : un plan de coupe peut occuper une moitié de l'image (droite en paysage, bas en vertical)
  pendant que le plan principal reste sur l'autre.
- Détourage IA sans fond vert : fond flouté, studio, noir, blanc ou image, avec aperçu image fixe.
- Étalonnage par LUT : 5 LUT intégrées (teal & orange, film chaud…) ou import d'un fichier `.cube`,
  avec intensité réglable et aperçu.

## Installation

Prérequis : **Python 3.11+**, **Node 18+** et **FFmpeg** (avec libass) dans le PATH.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt

# Interface
cd ../frontend
npm install
npm run build
```

Lancement (sert l'interface et l'API sur http://localhost:8000) :

```bash
cd backend
uvicorn app.main:app --port 8000
```

Ou en développement avec rechargement à chaud : `./scripts/dev.sh` puis http://localhost:5173.

**Application de bureau** : `python desktop/launcher.py` ouvre le Monteur dans sa propre fenêtre
(pywebview, sinon le navigateur), avec les projets dans `Documents/Monteur IA`. Pour en faire une appli
autonome : `pip install -r desktop/requirements.txt` puis `python desktop/build.py --ffmpeg <dossier de ffmpeg>`
(sur la machine cible : Windows, macOS ou Linux), résultat dans `dist/`.

**Doublage** : `espeak-ng` (Linux : `apt install espeak-ng`, macOS : `brew install espeak-ng`) suffit. Pour des
voix naturelles, `pip install piper-tts` et dépose une voix Piper (`fr_FR-siwis-medium.onnx` et son `.json`)
dans `data/voices/`.

Au premier détourage, le modèle de segmentation (250 Ko) est téléchargé depuis storage.googleapis.com.

Au premier montage, Whisper télécharge son modèle (« small » ≈ 480 Mo) depuis Hugging Face.

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Active Claude comme réalisateur |
| `MONTEUR_CLAUDE_MODEL` | `claude-opus-5-5` | Modèle utilisé pour la réalisation |
| `MONTEUR_CLAUDE_EFFORT` | `medium` | Effort de réflexion (`low` à `max`) |
| `MONTEUR_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `MONTEUR_WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `MONTEUR_WHISPER_LANGUAGE` | auto | Forcer une langue (`fr`, `en`…) |
| `MONTEUR_DATA_DIR` | `./data` | Projets, caches et exports |
| `MONTEUR_MUSIC_DIR` | `./data/music` | Bibliothèque de musiques libres de droits |
| `MONTEUR_WORKERS` | `2` | Tâches en parallèle |
| `MONTEUR_PIPER_VOICES` | `./data/voices` | Voix Piper du doublage (`<langue>*.onnx`) |
| `MONTEUR_SEGMENTER_MODEL` | téléchargé | Modèle de détourage `selfie_segmenter.tflite` |
| `MONTEUR_FFMPEG`, `MONTEUR_FFPROBE` | PATH | Chemin de FFmpeg (appli de bureau) |

**Musique** : dépose des fichiers audio libres de droits dans `data/music/`. Un fichier dont le nom contient
le style (`vlog`, `short`…) est choisi en priorité. Tu peux aussi choisir une musique par projet dans l'éditeur.

## Tests

```bash
cd backend && python -m pytest -q
```

Le test de bout en bout crée un rush synthétique, lance le montage IA, retouche la timeline, applique une
consigne puis exporte et vérifie la vidéo produite. `tests/test_pvp.py` vérifie la détection des coups, le
tempo, chaque élément 3D et un edit PvP exporté.

Pour essayer l'edit PvP sans vidéo de jeu, `scripts/make_pvp_clip.py` génère un clip de combat en voxels
(vue à la première personne, coups, éliminations et sons synthétiques) :

```bash
python scripts/make_pvp_clip.py pvp.mp4 --seconds 40 --fps 60
```

## Structure

```
backend/app/
  media/ffmpeg.py        probe, proxys, décodage, choix de l'encodeur
  analysis/              audio (silences, pics), vidéo (mouvement, plans), transcription
  ai/segments.py         découpe en moments + notation + prises répétées
  ai/director.py         réalisateurs heuristique et Claude, consignes
  analysis/hits.py       coups (flux spectral x volume), combos, tempo
  ai/pvp.py              style Edit PvP (combos, ralentis, calage sur le tempo, musique générée)
  render/                sous-titres (ASS animé, SRT) et rendu FFmpeg une passe
  render/engine3d.py     moteur 3D : caméra, plans texturés, texte extrudé, particules
  render/scene3d.py      compositeur 3D image par image entre deux passes FFmpeg
  pipeline.py, jobs.py   orchestration et tâches en arrière-plan
  main.py                API HTTP
frontend/src/            éditeur React (lecteur, timeline, panneaux)
```
