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
7. **Exporte** en 16:9, 9:16 ou 1:1 (1080p ou 720p) avec l'encodeur matériel de ta machine (NVENC,
   QuickSync, VideoToolbox, AMF) ou x264, plus un fichier `.srt`.

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

**Musique** : dépose des fichiers audio libres de droits dans `data/music/`. Un fichier dont le nom contient
le style (`vlog`, `short`…) est choisi en priorité. Tu peux aussi choisir une musique par projet dans l'éditeur.

## Tests

```bash
cd backend && python -m pytest -q
```

Le test de bout en bout crée un rush synthétique, lance le montage IA, retouche la timeline, applique une
consigne puis exporte et vérifie la vidéo produite.

## Structure

```
backend/app/
  media/ffmpeg.py        probe, proxys, décodage, choix de l'encodeur
  analysis/              audio (silences, pics), vidéo (mouvement, plans), transcription
  ai/segments.py         découpe en moments + notation + prises répétées
  ai/director.py         réalisateurs heuristique et Claude, consignes
  render/                sous-titres (ASS animé, SRT) et rendu FFmpeg une passe
  pipeline.py, jobs.py   orchestration et tâches en arrière-plan
  main.py                API HTTP
frontend/src/            éditeur React (lecteur, timeline, panneaux)
```
