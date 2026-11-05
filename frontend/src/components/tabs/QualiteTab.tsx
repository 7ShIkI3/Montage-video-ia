// Onglet Qualité : contrôle du montage et de la vidéo finie, relecture.
import { useStore } from "../../store";
import { QualityPanel } from "../Quality";

export function QualiteTab() {
  const tl = useStore((s) => s.timeline);
  if (!tl) return null;
  return (
    <section className="panel inspector">
      <header><h3>Qualité</h3></header>
      <QualityPanel />
    </section>
  );
}
