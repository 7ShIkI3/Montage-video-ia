// Onglet Plan : l'élément sélectionné (plan, texte, son, image, repère, bruitage, plan de coupe), ou une invitation à en choisir un.
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { ClipEditor, ItemEditor } from "../RightPanel";
import { EmptyHint } from "../ui/EmptyHint";

export function PlanTab() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  const it = useStore((s) => s.item);
  if (!tl || !project) return null;
  const c = tl.clips.find((x) => x.id === selected);
  if (c) return <ClipEditor c={c} />;
  if (it && it.kind !== "clip" && it.kind !== "comment") return <ItemEditor />;
  return (
    <div className="rw plan-empty">
      {tl.clips.length > 0 && (
        <button className="small-btn pitch-btn" onClick={() => useStore.getState().setPitch(true)}>
          <Icon name="sparkles" size={13} />Pitch : le réalisateur présente son montage
        </button>
      )}
      <EmptyHint icon="crosshair" text="Clique un plan dans la timeline pour le régler ici." />
      <p className="muted small">Un plan de coupe (B-roll) s'ajoute depuis le module Médias, à gauche, sur le rush voulu.</p>
    </div>
  );
}
