import { useEffect, useRef, useState } from "react";
import { api, Health, waitJob } from "../api";
import { useStore } from "../store";
import { AIPanel, MediaPanel } from "./LeftPanel";
import { Player } from "./Player";
import { CommandBar, CommentsPanel, Inspector, TranscriptPanel } from "./RightPanel";
import { TimelineView } from "./Timeline";
import { PresetExport, TemplatesPanel, VersionsPanel } from "./Studio";

function useAutosave() {
  const tl = useStore((s) => s.timeline);
  const dirty = useStore((s) => s.dirty);
  const project = useStore((s) => s.project);
  const timer = useRef<number>();
  useEffect(() => {
    if (!dirty || !tl || !project) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        await api.saveTimeline(project.id, tl);
        if (useStore.getState().timeline === tl) useStore.getState().markSaved();
        const captions = await api.captions(project.id);
        useStore.setState({ captions });
      } catch (e) {
        useStore.getState().notify(`Sauvegarde impossible : ${(e as Error).message}`);
      }
    }, 500);
    return () => window.clearTimeout(timer.current);
  }, [tl, dirty, project]);
}

function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const s = useStore.getState();
      const mod = e.ctrlKey || e.metaKey;
      if (e.code === "Space") { e.preventDefault(); s.setPlaying(!s.playing); }
      else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); s.undo(); }
      else if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); s.redo(); }
      else if (e.key.toLowerCase() === "s" && !mod) { s.splitAtPlayhead(); }
      else if (e.key.toLowerCase() === "c" && !mod) { e.preventDefault(); s.setPlaying(false); document.getElementById("comment-box")?.focus(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && s.item) {
        if (s.item.kind === "clip") s.deleteClip(s.item.id);
        else s.deleteItem(s.item.kind, s.item.id);
      }
      else if (e.key === "ArrowLeft") { s.setPlayhead(s.playhead - (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "ArrowRight") { s.setPlayhead(s.playhead + (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "Escape") { s.selectItem(null); s.setPicking(false); s.setPendingRegion(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

function PublishKitPanel() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { setJob, notify } = useStore.getState();
  if (!project) return null;
  const kit = project.publish_kit;
  const busy = job?.kind === "publication" && job.status !== "termine" && job.status !== "erreur";
  const copy = (t: string) => navigator.clipboard?.writeText(t).then(() => notify("Copié !"), () => undefined);
  const gen = async () => {
    try {
      const st = useStore.getState();
      if (st.dirty && st.timeline) { await api.saveTimeline(project.id, st.timeline); st.markSaved(); }
      await waitJob(await api.publishKit(project.id), setJob);
      await useStore.getState().refreshProject();
    } catch (e) {
      notify(`Kit impossible : ${(e as Error).message}`);
    } finally {
      setJob(null);
    }
  };
  return (
    <div className="export-alt kit">
      <h4>Kit de publication</h4>
      <p className="muted small">Titres, description avec chapitres, hashtags et miniature, prêts à coller sur YouTube ou TikTok.</p>
      <button disabled={busy} onClick={gen}>{busy ? "L'IA rédige…" : kit ? "↻ Régénérer" : "✨ Générer le kit"}</button>
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
          <p className="muted small">Rédigé par : {kit.by === "claude" ? "Claude (IA)" : "moteur local"}</p>
        </>
      )}
    </div>
  );
}

function ExportButton() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { setJob, notify } = useStore.getState();
  const [open, setOpen] = useState(false);
  const [quality, setQuality] = useState("1080p");
  const [fps, setFps] = useState<number | null>(null);
  const [smooth, setSmooth] = useState(false);
  const [last, setLast] = useState<string | null>(null);
  const [lastMany, setLastMany] = useState<string[]>([]);
  if (!project) return null;
  const busy = job?.kind === "export" && job.status !== "termine" && job.status !== "erreur";
  const used = new Set(project.timeline.clips.map((c) => c.media_id));
  const vids = project.media.filter((m) => used.has(m.id) && m.has_video);
  const srcFps = Math.round(Math.max(0, ...vids.map((m) => m.fps)) || 30);
  const srcH = Math.max(0, ...vids.map((m) => Math.min(m.width, m.height)));
  const wantFps = fps ?? srcFps;
  const upscale = srcH > 0 && +quality.replace("p", "") > srcH + 8;

  // Les téléchargements lisent la timeline enregistrée : on sauvegarde d'abord les retouches.
  const saveFirst = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    const st = useStore.getState();
    if (!st.dirty || !st.timeline) return;
    e.preventDefault();
    const href = e.currentTarget.href;
    await api.saveTimeline(project.id, st.timeline);
    st.markSaved();
    window.location.href = href;
  };

  const go = async () => {
    try {
      const st = useStore.getState();
      if (st.dirty && st.timeline) { await api.saveTimeline(project.id, st.timeline); st.markSaved(); }
      const j = await api.export(project.id, quality, fps, smooth);
      const done = await waitJob(j, setJob);
      setLast(done.result as string);
      await useStore.getState().refreshProject();
    } catch (e) {
      notify(`Export impossible : ${(e as Error).message}`);
    } finally {
      setJob(null);
    }
  };

  return (
    <div className="export-wrap">
      <button className="primary" onClick={() => setOpen(!open)} disabled={!project.timeline.clips.length}>⬇ Exporter</button>
      {open && (
        <div className="popover">
          <h4>Exporter la vidéo</h4>
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
          {wantFps > srcFps + 1 && (
            <label className="inline">
              <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
              Fluidité IA : créer les images manquantes ({srcFps} → {wantFps} i/s, rendu plus long)
            </label>
          )}
          {upscale && <p className="warn small">Tes rushes font {srcH}p : l'export {quality} sera agrandi, pas plus net.</p>}
          {wantFps > 60 && <p className="muted small">YouTube, TikTok et Instagram lisent jusqu'à 60 i/s ; 100-120 i/s sert aux écrans rapides et au ralenti en montage.</p>}
          <p className="muted small">Format {project.timeline.format}, H.264 + AAC, sous-titres incrustés, volume normalisé (-14 LUFS). Un fichier .srt est aussi créé.</p>
          <button className="primary" disabled={busy} onClick={go}>{busy ? "Rendu en cours…" : "Lancer le rendu"}</button>
          {busy && job && <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{Math.round(job.progress * 100)} %</span></div>}
          {last && <p><a href={api.exportUrl(project.id, last)} download>Télécharger « {last} »</a></p>}
          <div className="export-alt">
            <h4>Finir dans un logiciel pro</h4>
            <p className="muted small">Le montage de l'IA en XML, avec repères pour les textes, bruitages et B-roll.</p>
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
                    {/\.(mp4|webm|mov)$/.test(x) && <> · <a href={api.exportUrl(project.id, x.replace(/\.(mp4|webm|mov)$/, ".srt"))} download>.srt</a></>}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const project = useStore((s) => s.project);
  const dirty = useStore((s) => s.dirty);
  const toast = useStore((s) => s.toast);
  const [health, setHealth] = useState<Health | null>(null);
  const [name, setName] = useState("");
  useAutosave();
  useShortcuts();

  useEffect(() => {
    useStore.getState().load(id).catch((e) => useStore.getState().notify(e.message));
    api.health().then(setHealth).catch(() => undefined);
  }, [id]);
  useEffect(() => { if (project) setName(project.name); }, [project?.id]); // eslint-disable-line
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => useStore.getState().notify(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  if (!project) return <div className="loading">Chargement…</div>;

  return (
    <div className="editor">
      <header className="topbar">
        <button className="icon-btn" onClick={onBack} title="Projets">←</button>
        <span className="brand-mark" aria-hidden>▶</span>
        <input className="project-name" value={name} onChange={(e) => setName(e.target.value)}
          onBlur={() => name !== project.name && api.patchProject(project.id, { name }).then(useStore.getState().setProject)} />
        <span className="muted small">{dirty ? "Enregistrement…" : "Enregistré"}</span>
        <span className="spacer" />
        {health && (
          <span className={`pill ${health.claude ? "ok" : ""}`}
            title={health.claude ? `Réalisateur : ${health.claude_model}` : "Ajoute ANTHROPIC_API_KEY pour activer Claude comme réalisateur"}>
            {health.claude ? "IA Claude active" : "IA locale"} · Whisper {health.whisper_model} · {health.encoder}
          </span>
        )}
        <ExportButton />
      </header>
      <aside className="left">
        <MediaPanel />
        <AIPanel />
        <VersionsPanel />
        <TemplatesPanel />
      </aside>
      <main className="center">
        <Player />
      </main>
      <aside className="right">
        <CommandBar />
        <CommentsPanel />
        <Inspector />
        <TranscriptPanel />
      </aside>
      <footer className="bottom">
        <TimelineView />
      </footer>
      {toast && <div className="toast" onClick={() => useStore.getState().notify(null)}>{toast}</div>}
    </div>
  );
}
