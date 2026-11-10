// Onglet Export : export par réseau en tête (l'action principale), progression et résultat juste dessous, puis quatre blocs repliés :
// réglages sur mesure, XML/SRT, kit de publication, exports précédents.
import { useState } from "react";
import { api, fmtTime } from "../../api";
import { Icon } from "../../icons";
import { isBusy, jobOf, useStore } from "../../store";
import { PresetExport } from "../Studio";
import { EmptyHint } from "../ui/EmptyHint";
import { Section } from "../ui/Section";

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
    <div className="kit">
      <p className="muted small">Titres, description avec chapitres, hashtags et miniature, prêts à coller sur YouTube ou TikTok.</p>
      <button className="small-btn" disabled={busy} onClick={gen}>{busy ? "Rédaction…" : <><Icon name="pencil" size={13} />{kit ? "Régénérer" : "Générer le kit"}</>}</button>
      {kit && (
        <>
          <a href={api.exportUrl(project.id, kit.thumbnail)} download>
            <img className="kit-thumb" src={`${api.exportUrl(project.id, kit.thumbnail)}?v=${project.updated_at}`} alt="Miniature" />
          </a>
          <ul className="kit-titles">
            {kit.titles.map((t) => <li key={t}><span>{t}</span><button className="small-btn" onClick={() => copy(t)}><Icon name="copy" size={12} />Copier</button></li>)}
          </ul>
          <textarea rows={6} readOnly value={kit.description} aria-label="Description" />
          <button className="small-btn" onClick={() => copy(kit.description)}><Icon name="copy" size={12} />Copier la description</button>
          <p className="muted small">Rédigé par : {kit.by === "claude" ? "Claude" : "moteur local"}</p>
        </>
      )}
    </div>
  );
}

/** Dernier fichier rendu : la vidéo reste une vignette jusqu'au clic, puis se lit avec ses commandes. */
function ResultCard({ url, name, video, onReveal }: { url: string; name: string; video: boolean; onReveal: () => void }) {
  const [playing, setPlaying] = useState(false);
  return (
    <div className="export-result">
      {video && (playing
        ? <video controls autoPlay preload="metadata" src={url} />
        : (
          <button type="button" className="result-thumb" aria-label={`Lire « ${name} »`} onClick={() => setPlaying(true)}>
            <video preload="metadata" muted src={`${url}#t=0.1`} />
            <span className="play-badge"><Icon name="play" size={18} /></span>
          </button>
        ))}
      <div className="result-meta">
        <b className="result-name">{name}</b>
        <div className="row-btns">
          <a className="btn" href={url} download><Icon name="download" size={13} />Télécharger</a>
          <button className="small-btn" onClick={onReveal}><Icon name="folder" size={12} />Afficher dans le dossier</button>
        </div>
      </div>
    </div>
  );
}

export function ExportTab() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => jobOf(s, "export"));
  const busy = useStore((s) => isBusy(s, "export"));
  const range = useStore((s) => s.timeline?.range ?? null);
  const editing = useStore((s) => s.timeline);
  const [quality, setQuality] = useState("1080p");
  const [fps, setFps] = useState<number | null>(null);
  const [smooth, setSmooth] = useState(false);
  const [container, setContainer] = useState("mp4");
  const [rangeOnly, setRangeOnly] = useState(false);
  const [last, setLast] = useState<string | null>(null);
  const [lastMany, setLastMany] = useState<string[]>([]);
  if (!project) return null;
  // La timeline en cours d'édition prime sur celle du projet, qui n'est pas remise à jour par les retouches.
  const clips = (editing ?? project.timeline).clips;
  const used = new Set(clips.map((c) => c.media_id));
  const vids = project.media.filter((m) => used.has(m.id) && m.has_video);
  const srcFps = Math.round(Math.max(0, ...vids.map((m) => m.fps)) || 30);
  const srcH = Math.max(0, ...vids.map((m) => Math.min(m.width, m.height)));
  const wantFps = fps ?? srcFps;
  const upscale = srcH > 0 && +quality.replace("p", "") > srcH + 8;
  const empty = !clips.length;

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
  // Ouvre le dossier des exports dans l'explorateur (le fichier sélectionné) : marche même là où un lien
  // de téléchargement ne fait rien.
  const reveal = (file?: string) => useStore.getState().guard("Ouverture du dossier impossible", () => api.openFolder(project.id, { sub: "exports", file }));

  if (empty)
    return (
      <div className="rw">
        <EmptyHint icon="download" text="Fais d'abord un montage." action={{ label: "Monter avec l'IA", onClick: () => useStore.getState().openLeft("ia") }} />
      </div>
    );

  return (
    <div className="rw export-tab">
      <PresetExport onDone={setLastMany} />
      {busy && job && (
        <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || `${Math.round(job.progress * 100)} %`}</span></div>
      )}
      {lastMany.length > 0 && (
        <ul className="small export-many">{lastMany.map((x) => <li key={x}><a href={api.exportUrl(project.id, x)} download>Télécharger « {x} »</a></li>)}</ul>
      )}
      {last && <ResultCard key={last} url={api.exportUrl(project.id, last)} name={last} video={isVideo(last)} onReveal={() => reveal(last)} />}

      <Section title="Réglages sur mesure">
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
            <input type="checkbox" className="switch" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
            Interpolation : créer les images manquantes ({srcFps} → {wantFps} i/s, rendu plus long)
          </label>
        )}
        {range && (
          <label className="inline">
            <input type="checkbox" className="switch" checked={rangeOnly} onChange={(e) => setRangeOnly(e.target.checked)} />
            Seulement la zone I → O : {fmtTime(range.start)} → {fmtTime(range.end)} ({(range.end - range.start).toFixed(1)} s)
          </label>
        )}
        {upscale && <p className="warn small">Tes rushes font {srcH}p : l'export {quality} sera agrandi, pas plus net.</p>}
        {wantFps > 60 && <p className="muted small">YouTube, TikTok et Instagram lisent jusqu'à 60 i/s ; 100-120 i/s sert aux écrans rapides et au ralenti en montage.</p>}
        <p className="muted small">Format {(editing ?? project.timeline).format}, sous-titres incrustés, volume normalisé (-14 LUFS). Un fichier .srt est aussi créé.</p>
        <button className="small-btn primary-soft" disabled={busy} onClick={go}>{busy ? "Rendu en cours…" : "Lancer le rendu"}</button>
      </Section>

      <Section title="Finir dans un logiciel pro">
        <p className="muted small">Le montage en XML, avec repères pour les textes, bruitages et B-roll.</p>
        <div className="row-btns">
          <a className="btn" href={api.xmlUrl(project.id)} download onClick={saveFirst}>XML Premiere · Resolve · Final Cut</a>
          <a className="btn" href={api.srtUrl(project.id)} download onClick={saveFirst}>Sous-titres .srt</a>
        </div>
      </Section>

      <Section title="Kit de publication"><PublishKitPanel /></Section>

      {project.exports.length > 0 && (
        <Section title={`Exports précédents (${project.exports.length})`}>
          <p className="muted small">Les fichiers sont dans le dossier du projet (Documents › Monteur IA). <button className="link small" onClick={() => reveal()}>Ouvrir le dossier des exports</button></p>
          <ul className="small export-old">
            {[...project.exports].reverse().map((x) => (
              <li key={x}><a href={api.exportUrl(project.id, x)} download>{x}</a>
                {isVideo(x) && <> · <a href={api.exportUrl(project.id, x.replace(/\.(mp4|webm|mov)$/, ".srt"))} download>.srt</a></>}
                {" · "}<button className="link small" onClick={() => reveal(x)}>afficher</button></li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
