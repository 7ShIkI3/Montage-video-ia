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
11. **Edit PvP** (gaming) : détecte les coups dans le son et les regroupe en rafales au rythme d'un combo
    (0,5 s entre deux coups), hors menus et inventaires. Le son ne dit pas qui frappe : avec une clé Claude, un
    **jury IA** regarde quatre images de chaque rafale candidate (14 par tour, deux tours au plus, les fins de
    duel d'abord) et ne garde que celles à l'avantage du joueur ; une rafale subie ou une mort n'entre jamais.
    Les verdicts sont gardés avec le rush : refaire le montage ne repaie pas. Sans clé, le classement se fait
    au son et l'app le dit. L'edit s'ouvre sur un aperçu d'une seconde du meilleur combo et se ferme sur lui,
    avec le seul ralenti (les kills en ont un aussi). L'habillage est sobre pour que ce final ressorte : coupes
    franches sur le tempo de la musique (rythme électro généré si aucune n'est choisie), un seul flash à
    l'entrée du dernier combo, deux secousses par combo (son coup le plus fort et son coup final), une seule
    carte « COMBO xN », et le pic de la musique posé sur une coupe quand le morceau le permet. Ce montage
    n'est pas repris par le réalisateur après relecture.

Avec une clé API Anthropic, le réalisateur est **Claude** : il comprend le sens du discours, choisit les
meilleurs passages, écrit un titre et applique des consignes libres. Sans clé, un réalisateur local
heuristique fait le travail et comprend les consignes courantes (durée, format, sous-titres, musique, zooms,
« enlève "mot" »).

### Contrôle qualité et relecteur

- **Règles du réalisateur**, vérifiées à chaque montage : parole dès la première seconde et accroche avant 3 s,
  moment fort préparé (courte pause avant, zoom ou bruitage dessus), pas de plan fixe trop long, textes courts
  et affichés assez longtemps pour un téléphone, 3 flashs par seconde au plus, bruitages calés sur une coupe ou un mot.
- **Deux contrôles** : sur le plan de montage (avec corrections automatiques), puis sur la vidéo finie après chaque
  export (loudness mesurée à -14 LUFS, crête, flashs, début noir ou muet).
- **Relecteur indépendant** : un second appel à Claude, qui n'a pas vu la construction, note la version sur 10 à
  partir d'images clés, des paroles, de la feuille son/image et du rapport qualité. Sous 7/10, le réalisateur
  retouche avec ses remarques et la version repasse en relecture, 3 tours au plus. Sans clé, un relecteur local
  note à partir du contrôle.
- **Feuille son/image** : chaque son (paroles, impacts des rushes, bruitages, temps forts et pic de la musique)
  décrit en mots, à côté de ce qui est à l'écran au même instant. Le pic de la musique est calé sur le moment fort.

### Avant de monter

- **3 directions créatives** : l'IA regarde les rushes et propose trois façons différentes de raconter la vidéo
  (récit complet, short qui accroche, edit gaming, vlog ou discussion selon le contenu), avec une recommandée.
  Trois questions facultatives orientent les propositions : plateforme, ton, public. Un clic, et l'IA monte seule.
- **Monter comme une vidéo de référence** : dépose un montage que tu aimes ; l'IA mesure son rythme (coupes,
  durée des plans, fondus), puis rapproche le sien en 2 à 5 tours et dit ce qu'elle n'a pas pu égaler.
  Seul le rythme est copié, pas le look.
- **Projecteur sur ce que montre la voix** : quand la voix désigne quelque chose (« regardez ce bouton »,
  « voici », « en haut à droite »), le reste de l'image s'assombrit, la zone est entourée et le cadre s'en
  approche. Claude trouve la zone sur l'image ; sans clé, une carte de saillance locale le fait (seulement
  pour les désignations explicites). Les plans sont aussi calés sur les phrases : un plan ne coupe plus une
  phrase en plein milieu, et un plan de coupe finit avec la phrase qu'il illustre. Bouton « Projecteur sur la
  voix » dans le contrôle qualité pour le relancer.
- **Voix IA calée au mot près et réécoutée** : une voix off IA (un texte lu par la voix de synthèse, posé au
  curseur) ou un doublage est réécouté par Whisper. Si ce qu'on entend ne ressemble pas au texte, le contrôle
  qualité le signale. Chaque mot est horodaté, et les sous-titres animés suivent la voix IA (la voix doublée
  quand la vidéo est doublée). Sans Whisper, les mots sont calés sur l'énergie du son.
- **Affirmations vérifiées** : le contrôle qualité repère dans les textes et le titre ce qui promet trop
  (« le meilleur », « garanti », « jamais vu », « 100 % ») et l'adoucit, et signale un chiffre affiché qui n'est
  jamais dit dans la vidéo.
- **Plan de réalisation, rôle par rôle** : stratège (objectif, public, plateforme, promesse), directeur créatif
  (angle, ton, accroche, moment fort, fin), puis storyboard, montage et publication. Le brief des deux premiers
  guide le réalisateur ; chaque étape affiche ce qu'elle a décidé et ce qui ne colle pas.
- **Identité de marque** : depuis un logo importé ou le site de la marque, l'IA tire les couleurs (principale et
  accent), la police et le ton. Sous-titres, gros textes et bandeaux en prennent les couleurs, le ton guide le
  réalisateur, et un modèle de style « Marque … » peut être enregistré pour les prochaines vidéos.

### Pendant et après le montage

- **Réalisation en direct** : pendant un montage, des directions, des shorts ou la démo, le lecteur affiche les
  étapes du réalisateur (analyse, transcription, repérage, stratégie, montage, habillage, calage, relecture,
  vérification), celle en cours avec son message, la progression, le chrono et un bouton pour arrêter.
- **Pitch** : à la fin, le réalisateur présente son montage (titre, résumé, nombre de plans, durée, note du
  relecteur, score qualité, avertissements, plan rôle par rôle) et propose de le lire, de voir le plan ou
  d'exporter. Bouton « Pitch » dans l'onglet Plan pour le revoir.
- **Essayer sans vidéo** : sur l'accueil, un clip de combat PvP en voxels généré par l'app (graphismes et sons
  synthétiques) est importé dans un projet de démo et monté en style PvP, sous tes yeux. L'application installée
  embarque le clip ; en dev, il est rendu une fois (~3 min) puis gardé dans le cache des données.

## L'éditeur

- Hors ligne : polices embarquées (Inter, Space Grotesk, JetBrains Mono), aucune ressource externe ; confirmations
  et saisies dans des dialogues de l'application (Entrée valide, Échap annule), plus de fenêtres natives.
- Panneau droit en neuf onglets (touches `1` à `9`) : Consigne et commentaires, Montage, Plan sélectionné,
  Habillage, Audio et voix, Qualité, Export (qualité, cadence, fichier MP4/WebM/MOV/GIF, zone, aperçu du
  résultat, XML/SRT, kit de publication), Texte, Recherche. Sélectionner un plan ouvre l'onglet Plan
  seulement depuis Consigne, Montage ou Plan : depuis Audio, Habillage, Qualité ou Export, on reste où on
  travaille, rien ne disparaît.
- Espace de travail modulable : panneaux de gauche et de droite redimensionnables par leurs poignées (double-clic :
  taille par défaut) et repliables, hauteur de la timeline réglable, barre d'état en bas (moteurs, position, zoom,
  zone). Tout est mémorisé dans le navigateur ; « Espace de travail par défaut » remet tout en place.
- Lecture de la timeline en direct (sans rendu), sous-titres en surimpression, aperçu du format.
- Timeline : glisser-déposer pour réordonner, poignées pour raccourcir, `S` pour couper, `Suppr`,
  `Ctrl+Z` / `Ctrl+Y`, zoom, forme d'onde. Aimantation aux frontières des plans, au curseur et aux repères
  (`Alt` pour l'ignorer).
- Montage à la main depuis zéro : un clic sur un rush du chutier l'ouvre dans le moniteur source (lecture, image
  par image, marques `I` / `O`) ; « Insérer au curseur » (`,` ou `Entrée`, coupe le plan sous le curseur), « Ajouter
  à la fin » (`.`) ou « En plan de coupe » le posent ; on peut aussi glisser un rush sur la piste vidéo (sa portion
  marquée est reprise). `Ctrl+C` / `Ctrl+X` / `Ctrl+V` copient, coupent et collent un plan ou un élément au curseur,
  et `Maj` + glisser sur un plan décale son contenu sans changer sa durée (slip).
- Sélection multiple : `Ctrl` + clic ajoute ou retire un plan, `Maj` + clic sélectionne jusqu'au plan cliqué,
  `Ctrl+A` les prend tous. Supprimer, copier, couper, dupliquer, glisser ou décaler d'un cran agit alors sur tout
  le groupe, et un réglage de l'inspecteur (zoom, volume, vitesse, fondus, transition, effets, couleurs) s'applique
  à tous les plans sélectionnés.
- Vignettes dans les plans de la timeline : chaque plan montre les images du rush à l'endroit qu'il recouvre
  (une « pellicule » par rush, fabriquée une fois puis gardée ; le bouton en forme de film les masque).
- Au clavier, comme dans un logiciel pro : `J` / `K` / `L` (reculer, pause, lecture à ×1, ×2, ×4), `↑` / `↓`
  plan précédent / suivant, `I` / `O` rognent le plan au curseur, `Ctrl+D` duplique, `Ctrl+←` / `→` déplace,
  `+` / `-` et `Ctrl+molette` zooment, `Maj+Z` ajuste le zoom, `?` affiche tous les raccourcis.
- Palette de commandes `Ctrl+K` : toute action de l'éditeur par son nom ; une phrase qui n'en est pas une
  part en consigne à l'IA.
- Repères `M` : libellé, couleur, glissables. Ceux marqués « chapitre » deviennent les chapitres YouTube du
  kit de publication (règles YouTube respectées) et tous passent en repères dans l'export XML.
- Audio par plan : volume, fondus d'entrée et de sortie, coupure du son, dessinés sur la timeline et
  identiques au rendu.
- Couleur par plan (exposition, contraste, saturation, température, bouton « Réinitialiser »), mixeur audio
  (volume et muet pour le son des plans, la musique, les bruitages et les pistes audio, gardé quand l'IA refait le
  montage) et effet « Voix studio » (nettoyage, compression douce, présence). Aperçu en CSS proche de l'export.
- Sauvegardes automatiques du projet (une copie datée au plus toutes les 5 minutes, les 10 dernières gardées) :
  onglet Montage, « Restaurer » ; si `project.json` est abîmé, la dernière sauvegarde est rechargée toute seule.
- Depuis Internet : un rush ou une vidéo de référence depuis un lien (YouTube, Vimeo, fichier direct… via
  yt-dlp), une bibliothèque musicale avec recherche de musiques libres de droits (ccMixter, licence gardée
  à côté du fichier) ou le son d'un lien, et dans les réglages le téléchargement à l'avance des modèles Whisper
  et des voix naturelles Piper (Hugging Face).
- Réalisateur IA (onglet « Réalisateur ») : un agent Claude à outils. Il lit le **dossier des rushes** (ce que
  l'app a mesuré : coups au son, combos, écrans rouges, changements de plan, paroles horodatées, montage plan par
  plan, repères, commentaires, notes de recherche), **regarde les images** quand la réponse en dépend (des images
  du rush aux instants qu'il choisit, avec recadrage d'un coin pour lire un petit texte), lit la transcription,
  cherche sur le web, et **agit** : refaire le découpage à des instants précis, retoucher un plan (durée, vitesse,
  couleurs), régler le mixage, poser un texte ou des repères, ou confier une consigne libre au monteur automatique. Sa réponse s'écrit au fil de l'eau, chaque
  message garde la liste de ce qu'il a fait, et tout changement s'annule par `Ctrl+Z`. « Ce qu'il sait » affiche
  le dossier tel qu'il le lit. Au plus 30 images par message. Sans clé Claude, le message est appliqué comme une
  consigne simple par le réalisateur local.
- Analyse visuelle des rushes (« Regarder » dans l'onglet Réalisateur) : sur ta demande, Claude regarde des images
  de chaque rush aux instants qui comptent (fin des combos, écrans rouges, changements de plan, puis une grille
  régulière, 36 au plus) et note ce qu'il voit, texte à l'écran compris. Ces observations entrent dans le dossier
  et guident aussi le montage automatique. Appel payant, fait une fois par rush, jamais lancé tout seul.
- Recherche web (onglet « Recherche ») : une question, une réponse rédigée et sourcée par Claude (recherche web
  d'Anthropic) ou, sans clé, les premiers résultats DuckDuckGo. L'assistant reçoit aussi le dossier des rushes.
  Les notes restent dans le projet et le réalisateur s'en sert au montage et dans les consignes ; une phrase se
  pose en bandeau d'un clic.
- Accueil : menu « ⋯ » sur chaque projet (ouvrir, renommer, dupliquer avec médias et analyses, ouvrir le dossier
  dans l'explorateur, supprimer). Onglet Export : « Afficher dans le dossier » ouvre l'explorateur sur le fichier
  exporté ; les liens de téléchargement marchent aussi dans l'application de bureau.
- Zone d'entrée/sortie : `I` / `O` sans plan sélectionné (ou `X` pour le plan au curseur) posent une zone,
  glissable par ses bornes. `Ctrl+L` la lit en boucle, `Maj+Suppr` retire son contenu et referme le trou,
  et l'export propose « seulement la zone » (plans coupés, textes, sons, musique et repères recalés).
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

**Transcription sur GPU (NVIDIA)** : `pip install -r backend/requirements-cuda.txt` installe les bibliothèques
CUDA (cuBLAS, cuDNN) dont Whisper a besoin pour utiliser la carte graphique. Sans elles, ou si le GPU échoue au
chargement, l'app retombe toute seule sur le processeur (plus lent) et l'indique dans les avertissements du projet.

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
| `MONTEUR_WORKERS` | `3` | Tâches en parallèle |
| `MONTEUR_PIPER_VOICES` | `./data/voices` | Voix Piper du doublage (`<langue>*.onnx`) |
| `MONTEUR_SEGMENTER_MODEL` | téléchargé | Modèle de détourage `selfie_segmenter.tflite` |
| `MONTEUR_FFMPEG`, `MONTEUR_FFPROBE` | PATH | Chemin de FFmpeg (appli de bureau) |
| `MONTEUR_DEMO_CLIP` | clip embarqué ou rendu | Vidéo utilisée par « Essayer sans vidéo » |

La clé Claude, les modèles, l'appareil Whisper et le port peuvent aussi se régler dans l'app (Réglages,
engrenage en haut à droite). Ils sont stockés dans `Documents/Monteur IA/settings.json`, hors dépôt ; une
variable d'environnement a toujours priorité sur ce fichier.

**Musique** : dépose des fichiers audio libres de droits dans `data/music/`. Un fichier dont le nom contient
le style (`vlog`, `short`…) est choisi en priorité. Tu peux aussi choisir une musique par projet dans l'éditeur.

## Tests

```bash
cd backend && python -m pytest -q
```

Le test de bout en bout crée un rush synthétique, lance le montage IA, retouche la timeline, applique une
consigne puis exporte et vérifie la vidéo produite. `tests/test_pvp.py` vérifie la détection des coups, le
tempo, chaque élément 3D et un edit PvP exporté.

Pour essayer l'edit PvP sans vidéo de jeu, `scripts/make_pvp_clip.py` génère le clip de combat en voxels
(vue à la première personne, coups, éliminations et sons synthétiques) que le bouton « Essayer sans vidéo »
utilise (rendu dans `backend/app/media/demo.py`) :

```bash
python scripts/make_pvp_clip.py pvp.mp4 --seconds 40 --fps 30
```

`MONTEUR_DEMO_CLIP=chemin.mp4` remplace ce clip par le tien pour la démo (et dans les tests).

## Structure

```
backend/app/
  media/ffmpeg.py        probe, proxys, décodage, choix de l'encodeur
  analysis/              audio (silences, pics), vidéo (mouvement, plans), transcription
  ai/segments.py         découpe en moments + notation + prises répétées
  ai/director.py         réalisateurs heuristique et Claude, consignes
  analysis/hits.py       coups (flux spectral x volume), combos, tempo
  ai/pvp.py              style Edit PvP (combos, ralentis, calage sur le tempo, musique générée)
  ai/jury.py             jury des combos : Claude regarde les rafales et dit à qui elles profitent
  render/                sous-titres (ASS animé, SRT) et rendu FFmpeg une passe
  render/engine3d.py     moteur 3D : caméra, plans texturés, texte extrudé, particules
  render/scene3d.py      compositeur 3D image par image entre deux passes FFmpeg
  pipeline.py, jobs.py   orchestration et tâches en arrière-plan
  main.py                API HTTP
frontend/src/            éditeur React (lecteur, timeline, panneaux)
```
