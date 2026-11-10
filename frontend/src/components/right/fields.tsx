// Petits champs communs aux onglets de droite : curseur avec sa valeur, puces à cocher, en-tête d'élément.
import type { ReactNode } from "react";
import { Icon, IconName } from "../../icons";
import type { Opt } from "../../api";

/** Curseur : libellé au-dessus, valeur à droite dans une boîte de largeur fixe (mono). */
export function RangeField({ label, value, min, max, step, onChange, display, disabled }: {
  label: ReactNode;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  /** Valeur affichée à droite, déjà formatée (« 120 % », « 0,5 s »). */
  display: string;
  disabled?: boolean;
}) {
  return (
    <label className="rf">
      <span>{label}</span>
      <span className="range-row">
        <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(e) => onChange(+e.target.value)} />
        <span className="range-val">{display}</span>
      </span>
    </label>
  );
}

/** Pourcentage signé pour les curseurs centrés sur zéro (« +20 % », « -35 % »). */
export const signedPct = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)} %`;

/** Puces à cocher : une par option du catalogue, allumée si l'id est dans `value`. */
export function ChipList({ opts, value, onToggle }: { opts: Opt[]; value: string[]; onToggle: (id: string) => void }) {
  return (
    <div className="fx-chips">
      {opts.map((o) => (
        <button key={o.id} type="button" className={`fx-chip ${value.includes(o.id) ? "on" : ""}`} aria-pressed={value.includes(o.id)} onClick={() => onToggle(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const toggleId = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

/** En-tête d'un élément de la timeline sélectionné : icône du type, titre, et le lien pour le désélectionner. */
export function ItemHead({ icon, title, onBack }: { icon: IconName; title: string; onBack: () => void }) {
  return (
    <div className="item-head">
      <Icon name={icon} size={16} />
      <b>{title}</b>
      <span className="spacer" />
      <button type="button" className="link small" onClick={onBack}><Icon name="back" size={13} />Désélectionner</button>
    </div>
  );
}
