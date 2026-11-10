# Règles de design de Monteur IA

Ce fichier est la référence de l'interface. Toute modification de `frontend/src` s'y tient, et l'agent
`ui-critic` relit contre lui. Si une règle gêne, on la change ici d'abord, puis dans le code.

## Références

L'interface s'inspire de deux éditeurs vidéo, sans en copier le graphisme :

- **Éditeur de bureau dense (type CapCut)** : modules en haut à gauche avec icône et libellé, chutier en grille
  de vignettes, aperçu au centre avec le transport juste dessous, inspecteur du plan à droite avec ses
  sous-onglets (Vidéo, Audio, Vitesse, Animation, Ajuster), timeline sur toute la largeur avec sa barre d'outils.
- **Éditeur web épuré** : navigation à icônes libellées, très grand aperçu, pellicule de vignettes, et un bouton
  d'IA mis en avant.

Ce qui distingue Monteur IA et doit se voir d'emblée : le réalisateur IA (bouton « Monter avec l'IA », chat qui
regarde les images) à côté d'un vrai banc de montage manuel.

## Identité : charbon et corail

| Rôle | Jeton | Valeur |
|---|---|---|
| Fonds | `--bg`, `--bg-2`, `--panel` | `#0F0F11`, `#0A0A0C`, `#141416` |
| Surfaces | `--surface`, `--surface-2`, `--surface-3` | `#17171A`, `#1F1F23`, `#26262B` |
| Traits | `--line`, `--line-2` | `#2A2A30`, `#3A3A42` |
| Texte | `--text`, `--text-2`, `--muted`, `--faint` | `#F4F4F2`, `#C9C9CF`, `#A0A0A8`, `#8A8A93` |
| Accent | `--accent`, `--accent-2`, `--accent-ink` | `#FF6B3D`, `#FF8A66`, `#1A0D08` |
| États | `--ok`, `--warn`, `--danger`, `--info` | `#3DD68C`, `#F5B82E`, `#FF5C5C`, `#35D3C0` |

- **Le corail est rare.** Il sert à l'action principale, aux actions d'IA et à la sélection. Jamais en décor,
  jamais pour une piste ou une catégorie (les pistes ont leurs jetons `--trk-*`).
- Les jetons vivent dans `:root` de `frontend/src/styles.css`. Aucune couleur en dur dans un composant ou une
  feuille de `styles/` : on utilise un jeton, ou on en ajoute un ici et dans `:root`.
- Rayons : `--r-ctl` 6 px (boutons, champs, éléments de timeline), `--r-card` 8 px (vignettes, cartes),
  `--r-panel` 10 px, `--r-modal` 14 px, `--r-pill` 999 px.
- Espacements : `--s-1` à `--s-5` = 4, 8, 12, 16, 24 px. Hauteurs de contrôle : 28, 32, 40 px.

## Typographie

- Titres : Space Grotesk. Corps : Inter 13 px. Timecodes et durées : JetBrains Mono, chiffres tabulaires.
- Titre de panneau : 14 px / 600. Titre de section : 12 px / 600, majuscules, couleur `--muted`.
- **Plancher : 11 px.** Seule exception : les libellés sous les icônes des rails, à 10,5 px.
- Contraste : 4,5 : 1 pour tout texte, y compris les notes grises.

## Mise en page de l'éditeur

- Quatre zones : barre du haut (48 px), rangée de travail (gauche, aperçu, inspecteur), timeline pleine
  largeur, barre d'état (24 px). Jamais de défilement de la page : chaque panneau défile seul.
- Fenêtre minimale : 1280×720. Tout doit rester utilisable à cette taille, panneaux ouverts.
- Gauche : six modules à icône et libellé (Médias, IA, Audio, Texte, Effets, Projet). Droite : neuf onglets,
  dans l'ordre des touches 1 à 9, qui ne changent ni d'ordre ni de touche.
- **Un seul bouton plein par zone.** Il suit l'étape du projet : importer, puis monter, puis exporter.
- Un bouton désactivé dit pourquoi, par un texte visible à côté, pas seulement par une infobulle.
- Un état vide dit quoi faire et propose le bouton pour le faire.

## Composants

- Boutons : plein (`.primary`), neutre (défaut, `.small-btn`), discret (`.ghost`, `.icon-btn`), danger. On garde
  les noms de classes existants.
- Icônes : en trait, depuis `src/icons.tsx` (style Lucide, trait 1,8 px). Pas d'emoji. Un bouton à icône seule
  porte `aria-label` et `title`.
- Sélection d'un plan : contour 2 px corail et halo `--sel-glow`. Survol : un cran de surface en plus, 120 ms.
- Focus clavier toujours visible : `outline: 2px solid var(--accent)`.
- Animation : seulement pour dire quelque chose (un travail de l'IA en cours, une progression). Respecter
  `prefers-reduced-motion`.

## Interdits

- Retirer ou renommer un `data-testid`, un `id`, un raccourci clavier ou une ancre (`#/…`).
- Perdre une fonctionnalité en réorganisant : ce qui existait reste atteignable.
- Ajouter une dépendance npm pour un effet visuel.
- Dégradés décoratifs, cartes à liseré coloré, ombres lourdes, texte sous le plancher.
- Recopier le graphisme ou les icônes d'un autre logiciel.

## Méthode pour tout changement d'interface

1. **Références** : regarder de vrais écrans avant de dessiner (agent `reference-researcher`, ou des captures
   fournies).
2. **Règles** : relire ce fichier ; s'y tenir ou le corriger.
3. **Construire**, puis lancer `npm test`, `npm run check` et `npm run build` dans `frontend/`.
4. **Regarder** : ouvrir l'app (`preview_start`, configuration `monteur-dev`) à 1440×900 et à 1280×720.
5. **Critique** : agent `ui-critic` sur les captures et le diff. On corrige ce qu'il confirme.
