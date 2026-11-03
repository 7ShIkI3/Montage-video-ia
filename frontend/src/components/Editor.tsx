import { useEffect, useRef, useState } from "react";
import { api, Health, waitJob } from "../api";
import { useStore } from "../store";
import { AIPanel, MediaPanel } from "./LeftPanel";
import { Player } from "./Player";
import { CommandBar, Inspector, TranscriptPanel } from "./RightPanel";
import { TimelineView } from "./Timeline";

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
      else if ((e.key === "Delete" || e.key === "Backspace") && s.item) {
        if (s.item.kind === "clip") s.deleteClip(s.item.id);
        else s.deleteItem(s.item.kind, s.item.id);
      }
      else if (e.key === "ArrowLeft") { s.setPlayhead(s.playhead - (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "ArrowRight") { s.setPlayhead(s.playhead + (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "Escape") { s.selectItem(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

function ExportButton() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { setJob, notify } = useStore.getState();
  const [open, setOpen] = useState(false);
  const [quality, setQuality] = useState("1080p");
  const [last, setLast] = useState<string | null>(null);
  if (!project) return null;
  const busy = job?.kind === "export" && job.status !== "termine" && job.status !== "erreur";

  const go = async () => {
    try {
      const st = useStore.getState();
      if (st.dirty && st.timeline) { await api.saveTimeline(project.id, st.timeline); st.markSaved(); }
      const j = await api.export(project.id, quality);
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
          <label>Qualité
            <select value={quality} onChange={(e) => setQuality(e.target.value)}>
              <option value="1080p">1080p (recommandé)</option>
              <option value="720p">720p (plus rapide)</option>
            </select>
          </label>
          <p className="muted small">Format {project.timeline.format}, sous-titres incrustés, volume normalisé (-14 LUFS). Un fichier .srt est aussi créé.</p>
          <button className="primary" disabled={busy} onClick={go}>{busy ? "Rendu en cours…" : "Lancer le rendu"}</button>
          {busy && job && <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{Math.round(job.progress * 100)} %</span></div>}
          {last && <p><a href={api.exportUrl(project.id, last)} download>Télécharger « {last} »</a></p>}
          {project.exports.length > 0 && (
            <details>
              <summary className="small">Exports précédents ({project.exports.length})</summary>
              <ul className="small">
                {project.exports.slice().reverse().map((x) => (
                  <li key={x}><a href={api.exportUrl(project.id, x)} download>{x}</a> · <a href={api.exportUrl(project.id, x.replace(/\.mp4$/, ".srt"))} download>.srt</a></li>
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
      </aside>
      <main className="center">
        <Player />
      </main>
      <aside className="right">
        <CommandBar />
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
