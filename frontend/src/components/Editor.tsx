import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import { Icon, IconName } from "../icons";
import { DEFAULT_LAYOUT, isBusy, LeftTab, RIGHT_TAB_ORDER, RightTab, useStore } from "../store";
import { Shortcuts } from "./Shortcuts";
import { Pitch } from "./Pitch";
import { DirectorChat } from "./DirectorChat";
import { dialogOpen } from "./Dialog";
import { CommandPalette } from "./CommandPalette";
import { AIPanel, MediaPanel } from "./LeftPanel";
import { Player } from "./Player";
import { CommentsPanel, TranscriptPanel } from "./RightPanel";
import { MontageTab } from "./tabs/MontageTab";
import { PlanTab } from "./tabs/PlanTab";
import { HabillageTab } from "./tabs/HabillageTab";
import { AudioTab } from "./tabs/AudioTab";
import { QualiteTab } from "./tabs/QualiteTab";
import { ExportTab } from "./tabs/ExportTab";
import { TimelineView } from "./Timeline";
import { ResearchPanel } from "./Research";
import { BrandPanel, TemplatesPanel, VersionsPanel } from "./Studio";
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
      if (dialogOpen()) return;  // un dialogue (confirmation, saisie) a le clavier pour lui
      const s = useStore.getState();
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      const clip = s.item?.kind === "clip" ? s.item.id : null;
      if (s.helpOpen || s.paletteOpen || s.pitchOpen) {
        if (e.key === "Escape") { s.setHelp(false); s.setPalette(false); }
        return;
      }
      if (e.key === "?" || e.key === "F1" || (e.shiftKey && e.key === "/")) { e.preventDefault(); s.setHelp(true); return; }
      if (mod && k === "k") { e.preventDefault(); s.setPalette(true); return; }
      if (!mod && !e.altKey && /^[1-9]$/.test(e.key)) {  // 1-9 : onglets du panneau droit
        const t = RIGHT_TAB_ORDER[+e.key - 1];
        if (t) { s.setRightTab(t); if (!s.layout.rightOpen) s.setLayout({ rightOpen: true }); }
        return;
      }
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
      else if (k === "i" && !mod) { if (clip) s.trimToPlayhead(clip, "in"); else s.markRange("in"); }
      else if (k === "o" && !mod) { if (clip) s.trimToPlayhead(clip, "out"); else s.markRange("out"); }
      else if (k === "x" && !mod) { if (e.shiftKey) s.setRange(null); else s.rangeFromClip(); }
      else if (mod && k === "l") { e.preventDefault(); s.setLoopRange(!s.loopRange); }
      else if (e.key === "Delete" && e.shiftKey) { e.preventDefault(); s.deleteRange(); }
      else if (k === "m" && !mod) { s.addMarker(); }
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

/** Le bouton « Exporter » de la barre du haut ouvre l'onglet Export du panneau droit. */
function ExportButton() {
  const project = useStore((s) => s.project);
  const busy = useStore((s) => isBusy(s, "export"));
  if (!project) return null;
  return (
    <button className="primary" onClick={() => useStore.getState().setExportOpen(true)} disabled={!project.timeline.clips.length}
      title="Exporter la vidéo (onglet Export)">
      <Icon name="download" size={16} strokeWidth={2.2} />{busy ? "Rendu…" : "Exporter"}
    </button>
  );
}

const LEFT_TABS: { id: LeftTab; label: string; icon: IconName }[] = [
  { id: "rushes", label: "Médias", icon: "film" },
  { id: "ia", label: "Montage automatique", icon: "wand" },
  { id: "versions", label: "Versions et shorts", icon: "branch" },
  { id: "modeles", label: "Modèles de style", icon: "grid" },
  { id: "marque", label: "Identité de marque", icon: "palette" },
];
/** Onglets du panneau droit, dans l'ordre des touches 1 à 9. Rien ne disparaît quand on sélectionne un plan. */
const RIGHT_TABS: { id: RightTab; label: string; icon: IconName }[] = [
  { id: "consigne", label: "Réalisateur et commentaires", icon: "message" },
  { id: "montage", label: "Montage", icon: "film" },
  { id: "plan", label: "Plan sélectionné", icon: "square" },
  { id: "habillage", label: "Habillage", icon: "sparkles" },
  { id: "audio", label: "Audio et voix", icon: "volume" },
  { id: "qualite", label: "Qualité", icon: "check" },
  { id: "export", label: "Export", icon: "download" },
  { id: "texte", label: "Texte de la transcription", icon: "pencil" },
  { id: "recherche", label: "Recherche web", icon: "globe" },
];

/** Poignée de redimensionnement d'un panneau (glisser ; double-clic : taille par défaut). */
function Splitter({ dir, onDrag, onReset, label }: { dir: "v" | "h"; onDrag: (delta: number, first: boolean) => void; onReset: () => void; label: string }) {
  const start = (e: React.MouseEvent) => {
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    let first = true;
    document.body.classList.add(dir === "v" ? "resizing-v" : "resizing-h");
    const move = (ev: MouseEvent) => { onDrag(dir === "v" ? ev.clientX - x0 : ev.clientY - y0, first); first = false; };
    const up = () => {
      document.body.classList.remove("resizing-v", "resizing-h");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  return <div className={`splitter ${dir}`} role="separator" aria-label={label} aria-orientation={dir === "v" ? "vertical" : "horizontal"}
    title={`${label} (double-clic : taille par défaut)`} onMouseDown={start} onDoubleClick={onReset} />;
}

/** Barre d'état : moteurs, position, zoom, espace de travail. */
function StatusBar() {
  const health = useStore((s) => s.caps);
  const playhead = useStore((s) => s.playhead);
  const pps = useStore((s) => s.pxPerSec);
  const range = useStore((s) => s.timeline?.range ?? null);
  const layout = useStore((s) => s.layout);
  const { setLayout, resetLayout } = useStore.getState();
  const whisper = health ? `Whisper ${health.whisper_model}${health.whisper_device ? ` · ${health.whisper_device === "cuda" ? "GPU" : "CPU"}` : health.cuda ? " · GPU prêt" : ""}` : "";
  return (
    <footer className="statusbar">
      {health && (
        <a href="#/reglages" className={`status-item ${health.claude ? "" : "warn"}`} title={health.claude ? `Réalisateur : ${health.claude_model}. Ouvrir les réglages.` : "Sans clé Claude : consignes simples, moteur local. Ouvrir les réglages."}>
          <span className={`led ${health.claude ? "ok" : "warn"}`} />{health.claude ? health.claude_model : "Moteur local"}
        </a>
      )}
      {health && <span className="status-item" title="Transcription">{whisper}</span>}
      {health && <span className="status-item" title="Encodeur vidéo">{health.encoder}</span>}
      <span className="spacer" />
      {range && <span className="status-item accent" title="Zone d'entrée/sortie">Zone {fmtTime(range.start, true)} → {fmtTime(range.end, true)}</span>}
      <span className="status-item mono" title="Position du curseur">{fmtTime(playhead, true)}</span>
      <span className="status-item" title="Zoom de la timeline">{Math.round(pps)} px/s</span>
      <span className="status-item panels">
        <button className={layout.leftOpen ? "on" : ""} title="Panneau de gauche" aria-label="Panneau de gauche" aria-pressed={layout.leftOpen} onClick={() => setLayout({ leftOpen: !layout.leftOpen })}><Icon name="panel-left" size={14} /></button>
        <button className={layout.rightOpen ? "on" : ""} title="Panneau de droite" aria-label="Panneau de droite" aria-pressed={layout.rightOpen} onClick={() => setLayout({ rightOpen: !layout.rightOpen })}><Icon name="panel-right" size={14} /></button>
        <button title="Espace de travail par défaut" aria-label="Espace de travail par défaut" onClick={resetLayout}><Icon name="layout" size={14} /></button>
      </span>
    </footer>
  );
}

export function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const project = useStore((s) => s.project);
  const dirty = useStore((s) => s.dirty);
  const busy = useStore((s) => isBusy(s));
  const selected = useStore((s) => s.selected);
  const item = useStore((s) => s.item);
  const [name, setName] = useState("");
  const left = useStore((s) => s.leftTab);
  const right = useStore((s) => s.rightTab);
  const helpOpen = useStore((s) => s.helpOpen);
  const paletteOpen = useStore((s) => s.paletteOpen);
  const pitchOpen = useStore((s) => s.pitchOpen);
  const layout = useStore((s) => s.layout);
  const exportBusy = useStore((s) => isBusy(s, "export"));
  const openComments = useStore((s) => (s.timeline?.comments || []).filter((c) => c.status === "ouvert").length);
  const { setLeftTab: setLeft, setRightTab: setRight, setLayout } = useStore.getState();
  const dragBase = useRef(layout);
  useAutosave();
  useShortcuts();
  // Un panneau replié se rouvre quand on choisit une de ses sections.
  const openLeft = (t: LeftTab) => { setLeft(t); if (!useStore.getState().layout.leftOpen) setLayout({ leftOpen: true }); };

  useEffect(() => {
    useStore.getState().load(id).catch((e) => useStore.getState().fail("Chargement du projet", e));
  }, [id]);
  useEffect(() => { if (project) setName(project.name); }, [project?.id]); // eslint-disable-line
  // Sélectionner un plan ou un élément ouvre l'onglet Plan, mais seulement depuis Consigne, Montage ou Plan :
  // depuis Audio, Habillage, Qualité ou Export, on reste où on travaille.
  useEffect(() => {
    if (!selected && !item) return;
    if (item?.kind === "comment") return;
    const cur = useStore.getState().rightTab;
    if (cur === "consigne" || cur === "montage" || cur === "plan") setRight("plan");
  }, [selected, item]);
  // La timeline ne peut pas dépasser 70 % de la fenêtre : on recale après un redimensionnement.
  useEffect(() => {
    const onResize = () => setLayout({});
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  if (!project) return <div className="loading">Chargement…</div>;
  // Sans rush, on commence par l'import ; sinon par la réalisation IA.
  const leftTab = left ?? (project.media.length ? "ia" : "rushes");

  const rename = () => {
    if (name === project.name) return;
    useStore.getState().guard("Renommage impossible", async () => useStore.getState().setProject(await api.patchProject(project.id, { name })));
  };
  const vars = {
    "--left-w": layout.leftOpen ? `${layout.left}px` : "56px",
    "--right-w": layout.rightOpen ? `${layout.right}px` : "0px",
    "--bottom-h": `${layout.bottom}px`,
  } as React.CSSProperties;
  const drag = (key: "left" | "right" | "bottom", sign: 1 | -1) => (delta: number, first: boolean) => {
    if (first) dragBase.current = useStore.getState().layout;
    setLayout({ [key]: dragBase.current[key] + sign * delta });
  };

  return (
    <div className={`editor ${layout.leftOpen ? "" : "left-closed"} ${layout.rightOpen ? "" : "right-closed"}`} style={vars}>
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
        <Tasks />
        <button className="icon-btn" title="Palette de commandes (Ctrl+K)" aria-label="Palette de commandes" onClick={() => useStore.getState().setPalette(true)}><Icon name="search" size={18} /></button>
        <a href="#/reglages" className="icon-btn settings-link" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
        <ExportButton />
      </header>
      <aside className="left">
        <nav className="rail" aria-label="Sections">
          {LEFT_TABS.map((t) => (
            <button key={t.id} className={`${leftTab === t.id && layout.leftOpen ? "on" : ""} ${t.id === "ia" && busy ? "busy" : ""}`} title={t.label} aria-label={t.label}
              aria-current={leftTab === t.id ? "page" : undefined}
              onClick={() => (leftTab === t.id && layout.leftOpen ? setLayout({ leftOpen: false }) : openLeft(t.id))}>
              <Icon name={t.icon} size={20} />
            </button>
          ))}
        </nav>
        {layout.leftOpen && (
          <div className="rail-panel">
            {leftTab === "rushes" && <MediaPanel />}
            {leftTab === "ia" && <AIPanel />}
            {leftTab === "versions" && <VersionsPanel />}
            {leftTab === "modeles" && <TemplatesPanel />}
            {leftTab === "marque" && <BrandPanel />}
          </div>
        )}
        {layout.leftOpen && <Splitter dir="v" label="Largeur du panneau de gauche" onDrag={drag("left", 1)} onReset={() => setLayout({ left: DEFAULT_LAYOUT.left })} />}
      </aside>
      <main className="center">
        <Player />
      </main>
      {layout.rightOpen && (
        <aside className="right">
          <Splitter dir="v" label="Largeur du panneau de droite" onDrag={drag("right", -1)} onReset={() => setLayout({ right: DEFAULT_LAYOUT.right })} />
          <div className="right-body">
            <div className="tab-head">
              <h2>{RIGHT_TABS.find((t) => t.id === right)?.label}</h2>
              <span className="spacer" />
              <button className="icon-btn tab-close" title="Replier le panneau" aria-label="Replier le panneau de droite" onClick={() => setLayout({ rightOpen: false })}><Icon name="panel-right" size={14} /></button>
            </div>
            <div className="tab-body">
              {right === "consigne" && <><DirectorChat /><CommentsPanel /></>}
              {right === "montage" && <MontageTab />}
              {right === "plan" && <PlanTab />}
              {right === "habillage" && <HabillageTab />}
              {right === "audio" && <AudioTab />}
              {right === "qualite" && <QualiteTab />}
              {right === "export" && <ExportTab />}
              {right === "texte" && <TranscriptPanel />}
              {right === "recherche" && <ResearchPanel />}
            </div>
          </div>
          <nav className="rtabs" role="tablist" aria-label="Onglets du panneau droit">
            {RIGHT_TABS.map((t, i) => (
              <button key={t.id} role="tab" aria-selected={right === t.id} className={`${right === t.id ? "on" : ""} ${t.id === "export" && exportBusy ? "busy" : ""} ${t.id === "consigne" && openComments > 0 ? "badge" : ""}`}
                data-badge={t.id === "consigne" && openComments > 0 ? openComments : undefined}
                title={`${t.label} (${i + 1})`} aria-label={t.label} onClick={() => setRight(t.id)}>
                <Icon name={t.icon} size={17} />
              </button>
            ))}
          </nav>
        </aside>
      )}
      <footer className="bottom">
        <Splitter dir="h" label="Hauteur de la timeline" onDrag={drag("bottom", -1)} onReset={() => setLayout({ bottom: DEFAULT_LAYOUT.bottom })} />
        <TimelineView />
      </footer>
      <StatusBar />
      {helpOpen && <Shortcuts onClose={() => useStore.getState().setHelp(false)} />}
      {paletteOpen && <CommandPalette onClose={() => useStore.getState().setPalette(false)} />}
      {pitchOpen && <Pitch onClose={() => useStore.getState().setPitch(false)} />}
    </div>
  );
}
