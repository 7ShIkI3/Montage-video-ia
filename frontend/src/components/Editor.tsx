import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon, IconName } from "../icons";
import { isBusy, jobOf, LeftTab, RightTab, useStore } from "../store";
import { Shortcuts } from "./Shortcuts";
import { CommandPalette } from "./CommandPalette";
import { AIPanel, MediaPanel } from "./LeftPanel";
import { Player } from "./Player";
import { CommandBar, CommentsPanel, Inspector, TranscriptPanel } from "./RightPanel";
import { TimelineView } from "./Timeline";
import { PresetExport, BrandPanel, TemplatesPanel, VersionsPanel } from "./Studio";
import { Tasks } from "./Tasks";

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
        useStore.getState().markSaved(tl);
        const captions = await api.captions(project.id);
        useStore.setState({ captions });
      } catch (e) {
        useStore.getState().notify(`Sauvegarde impossible : ${(e as Error).message}`);
      }
    }, 500);
    return () => window.clearTimeout(timer.current);
  }, [tl, dirty, project]);
  // En quittant l'éditeur, la sauvegarde en attente part tout de suite au lieu d'être annulée.
  useEffect(() => () => { useStore.getState().saveNow().catch(() => undefined); }, []);
}

function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const s = useStore.getState();
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      const clip = s.item?.kind === "clip" ? s.item.id : null;
      if (s.helpOpen || s.paletteOpen) {
        if (e.key === "Escape") { s.setHelp(false); s.setPalette(false); }
        return;
      }
      if (e.key === "?" || e.key === "F1" || (e.shiftKey && e.key === "/")) { e.preventDefault(); s.setHelp(true); return; }
      if (mod && k === "k") { e.preventDefault(); s.setPalette(true); return; }
      if (e.code === "Space") { e.preventDefault(); if (s.playing) s.setRate(1); s.setPlaying(!s.playing); }
      else if (k === "k" && !mod) { s.setRate(1); s.setPlaying(false); }
      else if (k === "l" && !mod) { if (s.playing) s.setRate(s.rate >= 4 ? 1 : s.rate * 2); else { s.setRate(1); s.setPlaying(true); } }
      else if (k === "j" && !mod) { s.setPlayhead(s.playhead - (e.shiftKey ? 5 : 1)); }
      else if (e.key === "Home") { e.preventDefault(); s.setPlayhead(0); }
      else if (e.key === "End") { e.preventDefault(); s.setPlayhead(Number.MAX_SAFE_INTEGER); }
      else if (e.key === "ArrowUp") { e.preventDefault(); s.jumpClip(-1); }
      else if (e.key === "ArrowDown") { e.preventDefault(); s.jumpClip(1); }
      else if ((e.key === "+" || e.key === "=") && !mod) { s.setZoom(s.pxPerSec * 1.25); }
      else if (e.key === "-" && !mod) { s.setZoom(s.pxPerSec / 1.25); }
      else if (k === "z" && e.shiftKey && !mod) { s.zoomFit(); }
      else if (mod && k === "d") { e.preventDefault(); if (clip) s.duplicateClip(clip); }
      else if (mod && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); if (clip) s.nudgeClip(clip, e.key === "ArrowLeft" ? -1 : 1); }
      else if (k === "i" && !mod) { if (clip) s.trimToPlayhead(clip, "in"); }
      else if (k === "o" && !mod) { if (clip) s.trimToPlayhead(clip, "out"); }
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
      <button disabled={busy} onClick={gen}>{busy ? "L'IA rédige…" : <><Icon name="sparkles" />{kit ? "Régénérer" : "Générer le kit"}</>}</button>
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
  const job = useStore((s) => jobOf(s, "export"));
  const busy = useStore((s) => isBusy(s, "export"));
  const open = useStore((s) => s.exportOpen);
  const setOpen = useStore.getState().setExportOpen;
  const wrap = useRef<HTMLDivElement>(null);
  const [quality, setQuality] = useState("1080p");
  const [fps, setFps] = useState<number | null>(null);
  const [smooth, setSmooth] = useState(false);
  const [last, setLast] = useState<string | null>(null);
  const [lastMany, setLastMany] = useState<string[]>([]);
  // Le popover se ferme au clic à l'extérieur et sur Échap.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  if (!project) return null;
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
    if (await st.guard("Sauvegarde impossible", async () => { await st.saveNow(); return true; })) window.location.href = href;
  };

  const go = () => useStore.getState().runJob("Export", () => api.export(project.id, quality, fps, smooth), {
    onDone: (j) => setLast(j.result as string),
  });

  return (
    <div className="export-wrap" ref={wrap}>
      <button className="primary" onClick={() => setOpen(!open)} disabled={!project.timeline.clips.length}><Icon name="download" size={16} strokeWidth={2.2} />Exporter</button>
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

const LEFT_TABS: { id: LeftTab; label: string; icon: IconName }[] = [
  { id: "rushes", label: "Rushes", icon: "film" },
  { id: "ia", label: "Réalisation IA", icon: "sparkles" },
  { id: "versions", label: "Versions et shorts", icon: "branch" },
  { id: "modeles", label: "Modèles de style", icon: "grid" },
  { id: "marque", label: "Identité de marque", icon: "palette" },
];
const RIGHT_TABS: { id: RightTab; label: string }[] = [
  { id: "consigne", label: "Consigne" }, { id: "inspecteur", label: "Inspecteur" }, { id: "texte", label: "Texte" },
];

export function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const project = useStore((s) => s.project);
  const dirty = useStore((s) => s.dirty);
  const health = useStore((s) => s.caps);
  const busy = useStore((s) => isBusy(s));
  const selected = useStore((s) => s.selected);
  const item = useStore((s) => s.item);
  const [name, setName] = useState("");
  const left = useStore((s) => s.leftTab);
  const right = useStore((s) => s.rightTab);
  const helpOpen = useStore((s) => s.helpOpen);
  const paletteOpen = useStore((s) => s.paletteOpen);
  const { setLeftTab: setLeft, setRightTab: setRight } = useStore.getState();
  useAutosave();
  useShortcuts();

  useEffect(() => {
    useStore.getState().load(id).catch((e) => useStore.getState().fail("Chargement du projet", e));
  }, [id]);
  useEffect(() => { if (project) setName(project.name); }, [project?.id]); // eslint-disable-line
  // Sélectionner un plan ou un élément ouvre l'inspecteur.
  useEffect(() => { if (selected || item) setRight("inspecteur"); }, [selected, item]);

  if (!project) return <div className="loading">Chargement…</div>;
  // Sans rush, on commence par l'import ; sinon par la réalisation IA.
  const leftTab = left ?? (project.media.length ? "ia" : "rushes");

  const rename = () => {
    if (name === project.name) return;
    useStore.getState().guard("Renommage impossible", async () => useStore.getState().setProject(await api.patchProject(project.id, { name })));
  };
  const whisper = health?.whisper_device ? ` · Whisper ${health.whisper_model} (${health.whisper_device === "cuda" ? "GPU" : "CPU"})` : health ? ` · Whisper ${health.whisper_model}` : "";

  return (
    <div className="editor">
      <header className="topbar">
        <button className="icon-btn" onClick={async () => {
          try { await useStore.getState().saveNow(); } catch (e) { return useStore.getState().notify(`Sauvegarde impossible : ${(e as Error).message}`); }
          onBack();
        }} title="Retour aux projets" aria-label="Retour aux projets"><Icon name="back" size={18} /></button>
        <span className="brand-mark" aria-hidden><Icon name="play" size={11} /></span>
        <input className="project-name" value={name} aria-label="Nom du projet" onChange={(e) => setName(e.target.value)}
          onBlur={rename} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
        <span className={`saved ${dirty ? "dirty" : ""}`}><Icon name={dirty ? "clock" : "check"} size={12} strokeWidth={3} />{dirty ? "Enregistrement…" : "Enregistré"}</span>
        <span className="spacer" />
        {health && (
          <a href="#/reglages" className={`pill ${health.claude ? "ok" : "warn"}`}
            title={health.claude ? `Réalisateur : ${health.claude_model}. Cliquer pour ouvrir les réglages.` : "Sans clé Claude, le montage utilise l'IA locale (plus simple). Cliquer pour ajouter la clé."}>
            {health.claude ? "Claude" : "Clé Claude absente"}{whisper} · {health.encoder}
          </a>
        )}
        <Tasks />
        <a href="#/reglages" className="icon-btn settings-link" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
        <ExportButton />
      </header>
      <aside className="left">
        <nav className="rail" aria-label="Sections">
          {LEFT_TABS.map((t) => (
            <button key={t.id} className={`${leftTab === t.id ? "on" : ""} ${t.id === "ia" && busy ? "busy" : ""}`} title={t.label} aria-label={t.label}
              aria-current={leftTab === t.id ? "page" : undefined} onClick={() => setLeft(t.id)}>
              <Icon name={t.icon} size={20} />
            </button>
          ))}
        </nav>
        <div className="rail-panel">
          {leftTab === "rushes" && <MediaPanel />}
          {leftTab === "ia" && <AIPanel />}
          {leftTab === "versions" && <VersionsPanel />}
          {leftTab === "modeles" && <TemplatesPanel />}
          {leftTab === "marque" && <BrandPanel />}
        </div>
      </aside>
      <main className="center">
        <Player />
      </main>
      <aside className="right">
        <div className="tabs" role="tablist">
          {RIGHT_TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={right === t.id} className={right === t.id ? "on" : ""} onClick={() => setRight(t.id)}>{t.label}</button>
          ))}
        </div>
        <div className="tab-body">
          {right === "consigne" && <><CommandBar /><CommentsPanel /></>}
          {right === "inspecteur" && <Inspector />}
          {right === "texte" && <TranscriptPanel />}
        </div>
      </aside>
      <footer className="bottom">
        <TimelineView />
      </footer>
      {helpOpen && <Shortcuts onClose={() => useStore.getState().setHelp(false)} />}
      {paletteOpen && <CommandPalette onClose={() => useStore.getState().setPalette(false)} />}
    </div>
  );
}
