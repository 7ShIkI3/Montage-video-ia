// Onglet Habillage : look global, LUT, textes, bruitages, impacts, images (logo, filigrane).
import { useStore } from "../../store";
import { LutPicker } from "../Lot3";
import { GlobalFx } from "../RightPanel";

export function HabillageTab() {
  const tl = useStore((s) => s.timeline);
  const catalog = useStore((s) => s.catalog);
  if (!tl) return null;
  return (
    <section className="panel inspector">
      <header><h3>Habillage</h3></header>
      {catalog && <GlobalFx tl={tl} catalog={catalog} />}
      <LutPicker />
    </section>
  );
}
