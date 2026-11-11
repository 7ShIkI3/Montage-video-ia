// Onglet Plan : l'élément sélectionné (plan, texte, son, image, repère, bruitage, plan de coupe), ou une invitation à en choisir un.
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { ClipEditor, ItemEditor } from "../RightPanel";
import { EmptyHint } from "../ui/EmptyHint";

/** Où vit chaque sorte d'élément dans la timeline (l'éditeur d'un élément disparu ne rend rien : on ne le monte pas). */
const POOLS = { text: "texts", sfx: "sfx", overlay: "overlays", impact: "impacts", image: "images", audio: "audio", marker: "markers" } as const;

export function PlanTab() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  const it = useStore((s) => s.item);
  if (tl && project) {
    const c = tl.clips.find((x) => x.id === selected);
    if (c) return <ClipEditor c={c} />;
    if (it && it.kind !== "clip" && it.kind !== "comment") {
      const pool = (tl[POOLS[it.kind]] ?? []) as { id: string }[];
      if (pool.some((x) => x.id === it.id)) return <ItemEditor />;
    }
  }
  // Rien de sélectionné (ou rien à régler) : l'onglet ne reste jamais vide.
  return (
    <div className="rw plan-empty">
      {tl && tl.clips.length > 0 && (
        <button className="small-btn pitch-btn" onClick={() => useStore.getState().setPitch(true)}>
          <Icon name="sparkles" size={13} />Pitch : le réalisateur présente son montage
        </button>
      )}
      <EmptyHint icon="crosshair" text="Sélectionne un plan dans la timeline pour le régler" />
      <p className="muted small">Un plan de coupe (B-roll) s'ajoute depuis le module Médias, à gauche, sur le rush voulu.</p>
    </div>
  );
}
