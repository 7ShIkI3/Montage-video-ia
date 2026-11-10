// Aide des raccourcis clavier (touche « ? ») : la liste sert aussi à la palette de commandes.
import { useEffect } from "react";
import { Icon } from "../icons";

export interface Shortcut { keys: string[]; label: string }
export const SHORTCUT_GROUPS: { title: string; items: Shortcut[] }[] = [
  { title: "Lecture", items: [
    { keys: ["Espace"], label: "Lecture / pause" },
    { keys: ["J", "K", "L"], label: "Reculer d'1 s · pause · lecture (L répété : ×2, ×4)" },
    { keys: ["Maj", "J"], label: "Reculer de 5 s" },
    { keys: ["←", "→"], label: "Image précédente / suivante" },
    { keys: ["Maj", "←", "→"], label: "Une seconde en arrière / en avant" },
    { keys: ["↑", "↓"], label: "Plan précédent / suivant" },
    { keys: ["Début", "Fin"], label: "Début / fin du montage" },
  ] },
  { title: "Montage", items: [
    { keys: ["S"], label: "Couper le plan au curseur" },
    { keys: ["I", "O"], label: "Rogner l'entrée / la sortie du plan sélectionné au curseur" },
    { keys: ["Suppr"], label: "Supprimer l'élément ou les plans sélectionnés" },
    { keys: ["Ctrl", "clic"], label: "Ajouter un plan à la sélection, ou l'en retirer" },
    { keys: ["Maj", "clic"], label: "Sélectionner tous les plans jusqu'à celui-ci" },
    { keys: ["Ctrl", "A"], label: "Sélectionner tous les plans" },
    { keys: ["M"], label: "Poser un repère au curseur (chapitre YouTube, repère XML)" },
    { keys: ["Ctrl", "D"], label: "Dupliquer le ou les plans sélectionnés" },
    { keys: ["Ctrl", "←", "→"], label: "Déplacer le plan ou le groupe sélectionné d'un cran" },
    { keys: ["Ctrl", "Z"], label: "Annuler" },
    { keys: ["Ctrl", "Y"], label: "Rétablir" },
    { keys: ["Alt", "glisser"], label: "Désactiver l'aimantation pendant un déplacement" },
    { keys: ["Ctrl", "C"], label: "Copier les plans ou l'élément sélectionné (texte, bruitage, plan de coupe, image, impact, son)" },
    { keys: ["Ctrl", "X"], label: "Couper les plans ou l'élément sélectionné" },
    { keys: ["Ctrl", "V"], label: "Coller au curseur (un plan s'insère, en coupant le plan sous le curseur)" },
    { keys: ["Maj", "glisser"], label: "Décaler le contenu d'un plan sans changer sa durée (slip)" },
  ] },
  { title: "Moniteur source", items: [
    { keys: ["Clic"], label: "Ouvrir un rush du chutier dans le moniteur source" },
    { keys: ["Glisser"], label: "Glisser un rush du chutier sur la piste vidéo : pose sa portion marquée, ou le rush entier" },
    { keys: ["Espace"], label: "Lecture / pause du rush" },
    { keys: ["←", "→"], label: "Image précédente / suivante (Maj : une seconde)" },
    { keys: ["I", "O"], label: "Marquer l'entrée / la sortie à la position lue" },
    { keys: [",", "Entrée"], label: "Insérer au curseur du montage" },
    { keys: ["."], label: "Ajouter à la fin du montage" },
    { keys: ["Échap"], label: "Fermer le moniteur source" },
  ] },
  { title: "Zone d'entrée/sortie", items: [
    { keys: ["I", "O"], label: "Sans plan sélectionné : entrée / sortie de la zone au curseur" },
    { keys: ["X"], label: "Zone sur le plan au curseur" },
    { keys: ["Ctrl", "L"], label: "Lire la zone en boucle" },
    { keys: ["Maj", "Suppr"], label: "Supprimer le contenu de la zone et refermer" },
    { keys: ["Maj", "X"], label: "Effacer la zone" },
  ] },
  { title: "Affichage", items: [
    { keys: ["+", "-"], label: "Zoomer / dézoomer la timeline" },
    { keys: ["Ctrl", "molette"], label: "Zoomer sous la souris" },
    { keys: ["Maj", "Z"], label: "Zoom ajusté : tout le montage" },
    { keys: ["Ctrl", "K"], label: "Palette de commandes" },
    { keys: ["1", "…", "9"], label: "Onglets du panneau droit : consigne, montage, plan, habillage, audio, qualité, export, texte, recherche" },
    { keys: ["C"], label: "Écrire un commentaire de montage" },
    { keys: ["Échap"], label: "Désélectionner, fermer" },
    { keys: ["?"], label: "Cette aide" },
  ] },
];

export function Kbd({ k }: { k: string }) {
  return <kbd className="kbd">{k}</kbd>;
}

export function Shortcuts({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal shortcuts" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title">
        <header>
          <h2 id="shortcuts-title">Raccourcis clavier</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Fermer"><Icon name="x" size={16} /></button>
        </header>
        <div className="shortcut-groups">
          {SHORTCUT_GROUPS.map((g) => (
            <section key={g.title}>
              <h3>{g.title}</h3>
              <ul>
                {g.items.map((it) => (
                  <li key={it.label}>
                    <span className="keys">{it.keys.map((k, i) => <Kbd key={i} k={k} />)}</span>
                    <span>{it.label}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
