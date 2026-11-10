// Barre de modules du panneau de gauche : six boutons (icône et libellé) en haut du panneau.
// Replié, le panneau se réduit à cette barre, en colonne, avec un bouton pour le déplier.
import { Icon } from "../../icons";
import { LEFT_MODULES, leftModuleOf } from "../../labels";
import { isBusy, LeftTab, useStore } from "../../store";

export function LeftRail({ tab }: { tab: LeftTab }) {
  const open = useStore((s) => s.layout.leftOpen);
  const busy = useStore((s) => isBusy(s));
  const current = leftModuleOf(tab);
  const { openLeft, setLayout } = useStore.getState();
  return (
    <nav className="rail" aria-label="Sections">
      {LEFT_MODULES.map((m) => {
        const active = current.id === m.id;
        return (
          <button key={m.id} type="button" className={`${active && open ? "on" : ""} ${m.id === "ia" && busy ? "busy" : ""}`} title={m.label} aria-label={m.label}
            aria-current={active ? "page" : undefined}
            // Cliquer le module déjà actif replie le panneau ; sinon on ouvre le module (et on déplie le panneau).
            onClick={() => (active && open ? setLayout({ leftOpen: false }) : openLeft(active ? tab : m.tab))}>
            <Icon name={m.icon} size={20} />
            <span>{m.label}</span>
          </button>
        );
      })}
      {!open && (
        <button type="button" className="rail-expand" title="Déplier le panneau" aria-label="Déplier le panneau" onClick={() => setLayout({ leftOpen: true })}>
          <Icon name="chevron-right" size={18} />
        </button>
      )}
    </nav>
  );
}
