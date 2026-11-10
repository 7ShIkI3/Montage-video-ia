// État vide qui dit quoi faire : une icône, une phrase, et au besoin le bouton qui mène à l'étape suivante.
import { Icon, IconName } from "../../icons";

export function EmptyHint({ icon, text, action }: { icon: IconName; text: string; action?: { label: string; onClick: () => void } }) {
  return (
    <div className="empty-hint">
      <Icon name={icon} size={22} />
      <p>{text}</p>
      {action && <button type="button" className="small-btn primary-soft" onClick={action.onClick}>{action.label}</button>}
    </div>
  );
}
