// Sous-onglets compacts (Discuter / Commentaires, Vidéo / Audio / Vitesse…). Un seul réglage à la fois, clavier compris.
import type { KeyboardEvent } from "react";

export interface SegmentedOption<T extends string> { id: T; label: string; badge?: string | number }

export function Segmented<T extends string>({ value, onChange, options, ariaLabel }: {
  value: T;
  onChange: (v: T) => void;
  options: SegmentedOption<T>[];
  ariaLabel: string;
}) {
  // Flèches gauche et droite : passent à l'onglet voisin, comme un groupe d'onglets natif.
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const i = options.findIndex((o) => o.id === value);
    const next = options[(i + (e.key === "ArrowRight" ? 1 : options.length - 1)) % options.length];
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();  // le raccourci global ne doit pas aussi déplacer la tête de lecture
    onChange(next.id);
    const btns = e.currentTarget.querySelectorAll<HTMLButtonElement>("button");
    btns[options.indexOf(next)]?.focus();
  };
  return (
    <div className="seg" role="tablist" aria-label={ariaLabel} onKeyDown={onKey}>
      {options.map((o) => (
        <button key={o.id} type="button" role="tab" aria-selected={value === o.id} tabIndex={value === o.id ? 0 : -1}
          className={value === o.id ? "on" : ""} onClick={() => onChange(o.id)}>
          {o.label}
          {o.badge !== undefined && o.badge !== "" && <span className="seg-badge">{o.badge}</span>}
        </button>
      ))}
    </div>
  );
}
