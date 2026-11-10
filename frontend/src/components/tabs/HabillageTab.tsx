// Onglet Look : étalonnage global et LUT. Les textes, bruitages, impacts et images s'ajoutent depuis le panneau de gauche.
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { LutPicker } from "../Lot3";
import { GlobalFx } from "../RightPanel";
import { EmptyHint } from "../ui/EmptyHint";

export function HabillageTab() {
  const tl = useStore((s) => s.timeline);
  const catalog = useStore((s) => s.catalog);
  const { openLeft } = useStore.getState();
  if (!tl) return null;
  if (!tl.clips.length)
    return (
      <div className="rw">
        <EmptyHint icon="sparkles" text="Monte d'abord, puis règle le look ici." action={{ label: "Monter avec l'IA", onClick: () => openLeft("ia") }} />
      </div>
    );
  return (
    <div className="rw look-tab">
      {catalog && <GlobalFx tl={tl} catalog={catalog} />}
      <LutPicker />
      <div className="look-help">
        <p className="muted small">Textes, bruitages, impacts et images : panneau de gauche.</p>
        <div className="row-btns">
          <button className="small-btn" onClick={() => openLeft("texte")}><Icon name="type" size={13} />Texte</button>
          <button className="small-btn" onClick={() => openLeft("effets")}><Icon name="zap" size={13} />Effets</button>
        </div>
      </div>
    </div>
  );
}
