// Coque commune des modules du panneau de gauche : en-tête (titre, repli), colonne `.sub` des sous-sections, corps qui défile seul.
import type { ReactNode } from "react";
import { Icon } from "../../icons";
import { LEFT_MODULES, LeftModuleId } from "../../labels";
import { useStore } from "../../store";

export function ModuleFrame({ module, title, active, onSelect, flush, children }: {
  module: LeftModuleId;
  /** Titre affiché ; par défaut le libellé du module. */
  title?: string;
  /** Sous-section affichée (vide : le module n'a pas de colonne `.sub`). */
  active?: string;
  onSelect?: (id: string) => void;
  /** Corps sans marge : le module gère ses propres marges (pied collant pleine largeur). */
  flush?: boolean;
  children: ReactNode;
}) {
  const mod = LEFT_MODULES.find((m) => m.id === module) ?? LEFT_MODULES[0];
  const hasSubs = mod.subs.length > 0 && !!onSelect;
  return (
    <>
      <header className="lw-head">
        <h2>{title ?? mod.label}</h2>
        <span className="spacer" />
        <button type="button" className="icon-btn lw-fold" title="Replier le panneau" aria-label="Replier le panneau"
          onClick={() => useStore.getState().setLayout({ leftOpen: false })}><Icon name="chevron-left" size={16} /></button>
      </header>
      <div className="lw-main">
        {hasSubs && (
          <nav className="sub" aria-label={`Sections : ${mod.label}`}>
            {mod.subs.map((s) => (
              <button key={s.id} type="button" className={active === s.id ? "on" : ""} aria-current={active === s.id ? "page" : undefined}
                onClick={() => onSelect?.(s.id)}>{s.label}</button>
            ))}
          </nav>
        )}
        <div className={`lw-body ${flush ? "flush" : ""}`}>{children}</div>
      </div>
    </>
  );
}
