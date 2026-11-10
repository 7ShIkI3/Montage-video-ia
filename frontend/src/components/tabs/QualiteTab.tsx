// Onglet Qualité : contrôle du montage et de la vidéo finie, relecture.
import { useStore } from "../../store";
import { QualityPanel } from "../Quality";
import { EmptyHint } from "../ui/EmptyHint";

export function QualiteTab() {
  const tl = useStore((s) => s.timeline);
  if (!tl) return null;
  if (!tl.clips.length)
    return (
      <div className="rw">
        <EmptyHint icon="shield-check" text="Monte d'abord, puis contrôle ici."
          action={{ label: "Monter avec l'IA", onClick: () => useStore.getState().openLeft("ia") }} />
      </div>
    );
  return (
    <div className="rw">
      <QualityPanel />
    </div>
  );
}
