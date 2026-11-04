# Plan et stratégie

Version complète et commentable : document « Monteur IA — Plan et stratégie » du projet.

## Vision

On importe ses rushes, on clique sur « Monter avec l'IA », et l'application livre un montage complet et
exportable sans intervention. L'humain ne fait que retoucher, s'il le souhaite.

- **Full IA par défaut** : l'IA prend toutes les décisions et explique chacune d'elles.
- **Tout est retouchable** : le montage IA est une timeline normale, modifiable à la souris ou en français.
- **Rapide et local** : analyse et rendu sur la machine ; seul le réalisateur peut appeler Claude, et l'app
  fonctionne sans.

## Architecture

| Couche | Choix |
| --- | --- |
| Moteur vidéo | FFmpeg / FFprobe, encodeurs matériels, sous-titres ASS |
| Serveur | Python, FastAPI, tâches en threads avec progression |
| Transcription | faster-whisper (int8 CPU, float16 GPU), horodatage au mot |
| Signaux | NumPy sur l'audio 16 kHz, images 64×36 à 5 i/s pour mouvement et plans |
| Réalisateur | Claude (sortie JSON structurée) ou heuristique locale |
| Interface | React 18, TypeScript, Vite, Zustand |
| Stockage | `data/projects/<id>/` : médias, cache d'analyse, `project.json`, exports |

## Pipeline

1. Ingestion : probe, proxy 540p (GOP court pour un seek rapide), miniature.
2. Analyse audio : énergie RMS / 50 ms, seuil de silence adaptatif, pics sonores.
3. Analyse vidéo : mouvement et changements de plan.
4. Transcription au mot, repérage des hésitations.
5. Segmentation en phrases (pauses, ponctuation, hésitations) ou en plans d'action.
6. Notation : énergie, dynamique, pics, mouvement, débit, accroche ; détection des prises répétées.
7. Réalisation selon style et durée cible, accroche en premier pour les shorts, zooms.
8. Habillage : sous-titres karaoké, musique avec ducking, -14 LUFS.
9. Export une passe : coupes, recadrage, zooms, sous-titres, musique, normalisation.

## Performance

- Jamais d'analyse en pleine résolution ; tout est mis en cache par média.
- Changer de style ou de durée ne relance que la réalisation (quelques secondes).
- Proxys pour la lecture ; deux lecteurs alternés pour des coupes instantanées dans l'aperçu.
- Rendu en une passe, chaque clip décodé seulement sur sa plage (`-ss`/`-t` par entrée).

## Feuille de route

**V1 (livrée dans cette version)**
- [x] Import multi-fichiers, proxys, analyse audio/vidéo, transcription
- [x] Moments forts, prises répétées, hésitations, silences
- [x] Réalisateurs heuristique et Claude, 4 styles, durée cible, consignes libres
- [x] Éditeur : lecture live, timeline, « pourquoi », passages écartés, texte éditable, annuler/rétablir
- [x] Sous-titres karaoké / classiques, musique avec ducking, export 16:9 / 9:16 / 1:1 + SRT

**V2**
- [ ] Recadrage intelligent 9:16 qui suit le visage (détection de visage au lieu du centre)
- [ ] Multicam : synchronisation de plusieurs caméras par l'audio et choix du plan par locuteur
- [ ] B-roll automatique : insertion de plans d'illustration sur les mots-clés
- [ ] Choix de musique par l'IA selon l'ambiance, coupes calées sur le tempo
- [ ] Plusieurs versions en un clic (YouTube + 3 shorts depuis les mêmes rushes)

**V3**
- [ ] Application de bureau (Tauri) avec FFmpeg et modèles embarqués
- [ ] Étalonnage automatique, réduction de bruit de la voix
- [ ] Apprentissage des préférences : l'IA retient les retouches de l'utilisateur
