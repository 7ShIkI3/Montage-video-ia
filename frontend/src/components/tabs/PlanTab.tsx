// Onglet Plan : l'élément sélectionné (plan, texte, son, image, repère, bruitage, plan de coupe), ou quoi ajouter.
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { ClipEditor, ItemEditor } from "../RightPanel";
import { ImageButtons } from "../Studio";
import { AddSoundButton, VoiceOverButton } from "../AudioTools";

export function PlanTab() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const selected = useStore((s) => s.selected);
  const it = useStore((s) => s.item);
  const catalog = useStore((s) => s.catalog);
  const { addText, addSfx, addImpact } = useStore.getState();
  if (!tl || !project) return null;
  const c = tl.clips.find((x) => x.id === selected);
  return (
    <section className="panel inspector">
      {c ? <ClipEditor c={c} /> : it && it.kind !== "clip" && it.kind !== "comment" ? <ItemEditor /> : (
        <>
          <header><h3>Plan</h3></header>
          <p className="muted small">Clique un plan ou un élément de la timeline pour le régler ici. Ou ajoute au curseur :</p>
          <div className="add-grid">
            <button onClick={() => addText()}><Icon name="plus" size={14} />Texte</button>
            <button onClick={() => addSfx(catalog?.sfx[0]?.id ?? "whoosh")}><Icon name="volume" size={14} />Bruitage</button>
            <button onClick={() => addImpact(catalog?.impacts?.[0]?.id ?? "secousse")}><Icon name="zap" size={14} />Impact 3D</button>
            <VoiceOverButton />
            <AddSoundButton />
          </div>
          <ImageButtons />
          <p className="muted small">Un plan de coupe (B-roll) s'ajoute depuis le panneau Médias, sur le rush voulu.</p>
        </>
      )}
    </section>
  );
}
