// Onglet Montage : le film dans son ensemble (titre, réalisateur, format, sous-titres, rôles, passages écartés).
import { useStore } from "../../store";
import { CaptionLanguage } from "../AudioTools";
import { PlanPanel } from "../Quality";
import { RejectedList } from "../RightPanel";

export function MontageTab() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const { setTimelineProps } = useStore.getState();
  if (!tl || !project) return null;
  return (
    <section className="panel inspector">
      <header><h3>Montage</h3></header>
      {tl.title && <p className="title">{tl.title}</p>}
      <p className="muted small">{tl.summary || "Aucun montage pour l'instant : importe des médias, puis « Monter automatiquement » ou pose des plans à la main."}</p>
      {tl.director && <p className="muted small">Réalisé par : {tl.director === "claude" ? "Claude" : "moteur local"}</p>}
      <div className="field-row">
        <label>Format
          <select value={tl.format} onChange={(e) => setTimelineProps({ format: e.target.value as typeof tl.format })}>
            <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
          </select>
        </label>
        <label>Sous-titres
          <select value={tl.captions} onChange={(e) => setTimelineProps({ captions: e.target.value as typeof tl.captions })}>
            <option value="karaoke">Animés</option><option value="classique">Classiques</option><option value="aucun">Aucun</option>
          </select>
        </label>
      </div>
      <CaptionLanguage />
      {tl.format !== "16:9" && (
        <label className="inline"><input type="checkbox" checked={tl.reframe !== false}
          onChange={(e) => setTimelineProps({ reframe: e.target.checked })} /> Recadrage auto sur le visage</label>
      )}
      <PlanPanel />
      <RejectedList />
    </section>
  );
}
