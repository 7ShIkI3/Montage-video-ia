// Rail de l'inspecteur (panneau de droite) : neuf onglets, icône et libellé, touches 1 à 9.
import { Icon } from "../../icons";
import { RIGHT_TABS, rightTabHint } from "../../labels";
import { isBusy, RightTab, useStore } from "../../store";

export function RightRail({ tab }: { tab: RightTab }) {
  const exportBusy = useStore((s) => isBusy(s, "export"));
  const openComments = useStore((s) => (s.timeline?.comments || []).filter((c) => c.status === "ouvert").length);
  const setRight = useStore.getState().setRightTab;
  return (
    <nav className="rtabs" role="tablist" aria-label="Onglets du panneau droit">
      {RIGHT_TABS.map((t) => {
        const badge = t.id === "consigne" && openComments > 0;
        return (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
            className={`${tab === t.id ? "on" : ""} ${t.id === "export" && exportBusy ? "busy" : ""} ${badge ? "badge" : ""}`}
            data-badge={badge ? openComments : undefined}
            title={rightTabHint(t)} aria-label={t.label} onClick={() => setRight(t.id)}>
            <Icon name={t.icon} size={18} />
            <span>{t.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
