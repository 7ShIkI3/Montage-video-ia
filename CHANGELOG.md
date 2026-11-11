# Journal des versions

## 1.1.0 - 2026-11-11

- **Installateur Windows** : un seul fichier `MonteurIA-Setup-<version>.exe`, sans droits d'administrateur, avec
  raccourcis et désinstallation dans « Applications installées ». Tes projets (`Documents\Monteur IA`) ne sont jamais
  touchés.
- **Mises à jour sur ton clic** : au lancement, l'application regarde si une version plus récente existe et le dit dans
  un bandeau discret. Rien n'est téléchargé ni installé sans ton clic, et le fichier est vérifié (SHA-256) avant
  l'installation. La vérification se coupe dans les Réglages (section À propos).
- **Nouvelle interface « Studio »**, façon banc de montage : six modules à gauche (Médias, IA, Audio, Texte, Effets,
  Projet) et neuf onglets à droite, ouverts par les touches 1 à 9.
- **Edit PvP : seulement les combos que tu donnes.** L'application lit ta barre de vie et le roulis de ta caméra pour
  écarter les combos que tu subis. S'il manque de bons combos, le montage est plus court au lieu de se remplir.
- **Kill sans la raclée** : un kill est montré par ses derniers coups, puis au ralenti, sans le passage où tu perds
  de la vie. Plans plus courts, rythme resserré, habillage plus sobre pour que le final ressorte.
- **Jury IA plus attentif** : il regarde la fin de chaque combat, là où se voient les kills, et compare ta barre de
  vie avant et après. Ses verdicts sont gardés : refaire le montage ne repaie pas.
- **Musique pour l'edit demandé au chat** : le réalisateur monte le même edit PvP que le bouton, avec la musique, le
  tempo et le pic de musique sur le kill.
- **Réalisateur à outils** : il regarde les images des rushes, cherche sur le web, corrige les couleurs d'un plan,
  règle le mixage, et tout se défait par `Ctrl+Z`.
- **Montage à la main** : moniteur source, insertion au curseur, copier-coller, décalage du contenu d'un plan,
  sélection multiple, vignettes dans la timeline, zone d'entrée et de sortie, repères et chapitres YouTube, couleur
  réglable plan par plan.
- **Son** : mixeur audio, effet « Voix studio », fondus par plan, et limiteur de crête à l'export
  pour éviter la saturation.
- **Téléchargements** : rushes et musiques libres de droits depuis un lien, modèles Whisper et voix Piper à l'avance,
  recherche web pour la vidéo, démo sans vidéo (« Essayer sans vidéo »).
- **Aussi depuis la 1.0.0** : contrôle qualité et relecteur, directions créatives, doublage, LUT, images clés,
  transcription sur carte graphique, palette de commandes.
- **Fiabilité** : diagnostic du poste, sauvegardes automatiques du projet, plus de fenêtres de console dans
  l'application installée, journal qui dit quand l'application s'arrête.

## 1.0.0 - 2026-11-04

Première version : montage automatique par l'IA, éditeur, export.
