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
    { keys: ["Suppr"], label: "Supprimer l'élément sélectionné" },
    { keys: ["Ctrl", "D"], label: "Dupliquer le plan sélectionné" },
    { keys: ["Ctrl", "←", "→"], label: "Déplacer le plan sélectionné d'un cran" },
    { keys: ["Ctrl", "Z"], label: "Annuler" },
    { keys: ["Ctrl", "Y"], label: "Rétablir" },
    { keys: ["Alt", "glisser"], label: "Désactiver l'aimantation pendant un déplacement" },
  ] },
  { title: "Affichage", items: [
    { keys: ["+", "-"], label: "Zoomer / dézoomer la timeline" },
    { keys: ["Ctrl", "molette"], label: "Zoomer sous la souris" },
    { keys: ["Maj", "Z"], label: "Zoom ajusté : tout le montage" },
    { keys: ["Ctrl", "K"], label: "Palette de commandes" },
    { keys: ["C"], label: "Écrire un commentaire pour l'IA" },
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
