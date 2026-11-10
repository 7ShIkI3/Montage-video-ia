// Onglet Montage : le film dans son ensemble (titre, réalisateur, format, sous-titres, rôles, passages écartés,
// sauvegardes automatiques).
import { useCallback, useEffect, useState } from "react";
import { api, Backup, fmtTime } from "../../api";
import { useStore } from "../../store";
import { CaptionLanguage } from "../AudioTools";
import { ask } from "../Dialog";
import { PlanPanel } from "../Quality";
import { RejectedList } from "../RightPanel";

/** Copies datées du projet, faites automatiquement à chaque enregistrement (au plus une toutes les 5 minutes,
 * les 10 dernières sont gardées). « Restaurer » remet l'une d'elles en place ; l'état courant est gardé avant. */
function BackupsSection({ projectId }: { projectId: string }) {
  const [list, setList] = useState<Backup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => {
    api.backups(projectId).then(setList).catch(() => setList([]));
  }, [projectId]);
  useEffect(refresh, [refresh]);

  const restore = async (b: Backup) => {
    const when = new Date(b.at * 1000).toLocaleString("fr-FR", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
    if (!await ask.confirm(`Revenir à la sauvegarde du ${when} (${b.clips} plans, ${fmtTime(b.duration)}) ? Le montage actuel est gardé lui aussi dans les sauvegardes.`,
      { title: "Restaurer une sauvegarde", ok: "Restaurer" })) return;
    setBusy(true);
    try {
      await api.restoreBackup(projectId, b.name);
      await useStore.getState().load(projectId);
      useStore.getState().notify("Sauvegarde restaurée");
    } catch (e) {
      useStore.getState().notify((e as Error).message, { level: "erreur" });
    } finally {
      setBusy(false);
      refresh();
    }
  };

  return (
    <div className="fx-section backups">
      <div className="fx-title">
        Sauvegardes automatiques
        <button className="small-btn" onClick={refresh} disabled={busy}>Actualiser</button>
      </div>
      {list && list.length === 0 && <p className="muted small">Pas encore de sauvegarde : la première arrive à ton prochain enregistrement.</p>}
      {list && list.length > 0 && (
        <ul className="backup-list">
          {list.map((b) => (
            <li key={b.name}>
              <span className="backup-when">{new Date(b.at * 1000).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
              <small className="muted">{b.clips} plan{b.clips > 1 ? "s" : ""} · {fmtTime(b.duration)}</small>
              <button className="small-btn" disabled={busy} onClick={() => restore(b)}>Restaurer</button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">Une copie au plus toutes les 5 minutes, les 10 dernières sont gardées.</p>
    </div>
  );
}

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
      <BackupsSection projectId={project.id} />
    </section>
  );
}
