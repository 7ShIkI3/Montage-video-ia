// Onglet Export : rendu vidéo (qualité, cadence, fichier, zone), aperçu du résultat, XML/SRT, kit de publication.
import { useState } from "react";
import { api, fmtTime } from "../../api";
import { Icon } from "../../icons";
import { isBusy, jobOf, useStore } from "../../store";
import { PresetExport } from "../Studio";

const CONTAINERS: [string, string][] = [["mp4", "MP4 (H.264 + AAC)"], ["webm", "WebM (VP9 + Opus)"], ["mov", "MOV ProRes (montage pro)"], ["gif", "GIF animé (sans son)"]];

function PublishKitPanel() {
  const project = useStore((s) => s.project);
  const busy = useStore((s) => isBusy(s, "publication"));
  const { notify, runJob } = useStore.getState();
  if (!project) return null;
  const kit = project.publish_kit;
  const copy = (t: string) => navigator.clipboard?.writeText(t).then(() => notify("Copié !"), () => undefined);
  const gen = () => runJob("Kit de publication", () => api.publishKit(project.id));
  return (
    <div className="export-alt kit">
      <h4>Kit de publication</h4>
      <p className="muted small">Titres, description avec chapitres, hashtags et miniature, prêts à coller sur YouTube ou TikTok.</p>
      <button disabled={busy} onClick={gen}>{busy ? "Rédaction…" : <><Icon name="pencil" />{kit ? "Régénérer" : "Générer le kit"}</>}</button>
      {kit && (
        <>
          <a href={api.exportUrl(project.id, kit.thumbnail)} download>
            <img className="kit-thumb" src={`${api.exportUrl(project.id, kit.thumbnail)}?v=${project.updated_at}`} alt="Miniature" />
          </a>
          <ul className="kit-titles">
            {kit.titles.map((t) => <li key={t}><span>{t}</span><button className="small-btn" onClick={() => copy(t)}>Copier</button></li>)}
          </ul>
          <textarea rows={6} readOnly value={kit.description} />
          <button className="small-btn" onClick={() => copy(kit.description)}>Copier la description</button>
          <p className="muted small">Rédigé par : {kit.by === "claude" ? "Claude" : "moteur local"}</p>
        </>
      )}
    </div>
  );
}

export function ExportTab() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => jobOf(s, "export"));
  const busy = useStore((s) => isBusy(s, "export"));
  const range = useStore((s) => s.timeline?.range ?? null);
  const [quality, setQuality] = useState("1080p");
  const [fps, setFps] = useState<number | null>(null);
  const [smooth, setSmooth] = useState(false);
  const [container, setContainer] = useState("mp4");
  const [rangeOnly, setRangeOnly] = useState(false);
  const [last, setLast] = useState<string | null>(null);
  const [lastMany, setLastMany] = useState<string[]>([]);
  if (!project) return null;
  const used = new Set(project.timeline.clips.map((c) => c.media_id));
  const vids = project.media.filter((m) => used.has(m.id) && m.has_video);
  const srcFps = Math.round(Math.max(0, ...vids.map((m) => m.fps)) || 30);
  const srcH = Math.max(0, ...vids.map((m) => Math.min(m.width, m.height)));
  const wantFps = fps ?? srcFps;
  const upscale = srcH > 0 && +quality.replace("p", "") > srcH + 8;
  const empty = !project.timeline.clips.length;

  // Les téléchargements lisent la timeline enregistrée : on sauvegarde d'abord les retouches.
  const saveFirst = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    const st = useStore.getState();
    if (!st.dirty || !st.timeline) return;
    e.preventDefault();
    const href = e.currentTarget.href;
    if (await st.guard("Sauvegarde impossible", async () => { await st.saveNow(); return true; })) window.location.href = href;
  };
  const useRange = !!range && rangeOnly;
  const go = () => useStore.getState().runJob(useRange ? "Export de la zone" : "Export",
    () => api.export(project.id, quality, fps, smooth, useRange, container), { onDone: (j) => setLast(j.result as string) });
  const isVideo = (x: string) => /\.(mp4|webm|mov)$/.test(x);

  return (
    <section className="panel inspector export-tab">
      <header><h3>Export</h3></header>
      {empty && <p className="muted small">La timeline est vide : rien à exporter pour l'instant.</p>}
      <PresetExport onDone={setLastMany} />
      {lastMany.length > 0 && (
        <ul className="small">{lastMany.map((x) => <li key={x}><a href={api.exportUrl(project.id, x)} download>Télécharger « {x} »</a></li>)}</ul>
      )}
      <h4>Réglages sur mesure</h4>
      <div className="field-row">
        <label>Qualité
          <select value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="720p">720p (rapide)</option>
            <option value="1080p">1080p Full HD</option>
            <option value="1440p">1440p 2K</option>
            <option value="2160p">2160p 4K</option>
          </select>
        </label>
        <label>Images / seconde
          <select value={fps ?? ""} onChange={(e) => setFps(e.target.value ? +e.target.value : null)}>
            <option value="">Comme la source ({srcFps} i/s)</option>
            {[24, 25, 30, 50, 60, 100, 120].map((f) => <option key={f} value={f}>{f} i/s</option>)}
          </select>
        </label>
      </div>
      <label>Fichier
        <select value={container} onChange={(e) => setContainer(e.target.value)}>
          {CONTAINERS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </label>
      {wantFps > srcFps + 1 && (
        <label className="inline">
          <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
          Interpolation : créer les images manquantes ({srcFps} → {wantFps} i/s, rendu plus long)
        </label>
      )}
      {range && (
        <label className="inline" title="La zone posée avec I et O sur la timeline">
          <input type="checkbox" checked={rangeOnly} onChange={(e) => setRangeOnly(e.target.checked)} />
          Seulement la zone {fmtTime(range.start)} → {fmtTime(range.end)} ({(range.end - range.start).toFixed(1)} s)
        </label>
      )}
      {upscale && <p className="warn small">Tes rushes font {srcH}p : l'export {quality} sera agrandi, pas plus net.</p>}
      {wantFps > 60 && <p className="muted small">YouTube, TikTok et Instagram lisent jusqu'à 60 i/s ; 100-120 i/s sert aux écrans rapides et au ralenti en montage.</p>}
      <p className="muted small">Format {project.timeline.format}, sous-titres incrustés, volume normalisé (-14 LUFS). Un fichier .srt est aussi créé.</p>
      <button className="primary" disabled={busy || empty} onClick={go}>{busy ? "Rendu en cours…" : "Lancer le rendu"}</button>
      {busy && job && <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || `${Math.round(job.progress * 100)} %`}</span></div>}
      {last && (
        <div className="export-result">
          {isVideo(last) && <video controls preload="metadata" src={api.exportUrl(project.id, last)} />}
          <a href={api.exportUrl(project.id, last)} download>Télécharger « {last} »</a>
        </div>
      )}
      <div className="export-alt">
        <h4>Finir dans un logiciel pro</h4>
        <p className="muted small">Le montage en XML, avec repères pour les textes, bruitages et B-roll.</p>
        <div className="row-btns">
          <a className="btn" href={api.xmlUrl(project.id)} download onClick={saveFirst}>XML Premiere · Resolve · Final Cut</a>
          <a className="btn" href={api.srtUrl(project.id)} download onClick={saveFirst}>Sous-titres .srt</a>
        </div>
      </div>
      <PublishKitPanel />
      {project.exports.length > 0 && (
        <details>
          <summary className="small">Exports précédents ({project.exports.length})</summary>
          <ul className="small">
            {[...project.exports].reverse().map((x) => (
              <li key={x}><a href={api.exportUrl(project.id, x)} download>{x}</a>
                {isVideo(x) && <> · <a href={api.exportUrl(project.id, x.replace(/\.(mp4|webm|mov)$/, ".srt"))} download>.srt</a></>}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
