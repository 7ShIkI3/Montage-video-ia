import { useEffect, useRef } from "react";
import { api } from "../api";
import { Icon } from "../icons";
import { defaultLayout, editStep, RIGHT_TAB_ORDER, useStore } from "../store";
import { rightTabTitle } from "../labels";
import { Shortcuts } from "./Shortcuts";
import { Pitch } from "./Pitch";
import { SourceMonitor } from "./SourceMonitor";
import { dialogOpen } from "./Dialog";
import { CommandPalette } from "./CommandPalette";
import { Player } from "./Player";
import { TimelineView } from "./Timeline";
import { LeftWorkspace } from "./left/LeftWorkspace";
import { RightWorkspace } from "./right/RightWorkspace";
import { LeftRail } from "./shell/LeftRail";
import { RightRail } from "./shell/RightRail";
import { Splitter } from "./shell/Splitter";
import { StatusBar } from "./shell/StatusBar";
import { TopBar } from "./shell/TopBar";
import "../styles/shell.css";

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
      if (s.helpOpen || s.paletteOpen || s.pitchOpen || s.sourceMedia) {  // le moniteur source écoute lui-même le clavier
        if (e.key === "Escape") { s.setHelp(false); s.setPalette(false); }
        return;
      }
      if (e.key === "?" || e.key === "F1" || (e.shiftKey && e.key === "/")) { e.preventDefault(); s.setHelp(true); return; }
      if (mod && k === "k") { e.preventDefault(); s.setPalette(true); return; }
      if (!mod && !e.altKey && /^[1-9]$/.test(e.key)) {  // 1-9 : onglets du panneau droit, même panneau replié
        const t = RIGHT_TAB_ORDER[+e.key - 1];
        if (t) s.openRight(t);
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
      else if (mod && k === "d") { e.preventDefault(); if (clip) s.duplicateSelection(); }
      else if (mod && k === "a" && !e.shiftKey && !e.altKey) { e.preventDefault(); s.selectAllClips(); }
      else if (mod && !e.shiftKey && !e.altKey && (k === "c" || k === "x")) {
        if (window.getSelection()?.toString()) return;  // du texte est sélectionné dans la page : copie normale du navigateur
        if (!s.item) return;
        e.preventDefault();
        if (k === "c") s.copySelection(); else s.cutSelection();
      }
      else if (mod && !e.shiftKey && !e.altKey && k === "v") { e.preventDefault(); s.paste(); }
      else if (mod && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); if (clip) s.nudgeSelection(e.key === "ArrowLeft" ? -1 : 1); }
      else if (k === "i" && !mod) { if (clip) s.trimToPlayhead(clip, "in"); else s.markRange("in"); }
      else if (k === "o" && !mod) { if (clip) s.trimToPlayhead(clip, "out"); else s.markRange("out"); }
      else if (k === "x" && !mod) { if (e.shiftKey) s.setRange(null); else s.rangeFromClip(); }
      else if (mod && k === "l") { e.preventDefault(); s.setLoopRange(!s.loopRange); }
      else if (e.key === "Delete" && e.shiftKey) { e.preventDefault(); s.deleteRange(); }
      else if (k === "m" && !mod) { s.addMarker(); }
      else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); s.undo(); }
      else if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); s.redo(); }
      else if (e.key.toLowerCase() === "s" && !mod) { s.splitAtPlayhead(); }
      else if (e.key.toLowerCase() === "c" && !mod) { e.preventDefault(); s.setPlaying(false); s.focusComment(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && s.item) { s.deleteSelection(); }
      else if (e.key === "ArrowLeft") { s.setPlayhead(s.playhead - (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "ArrowRight") { s.setPlayhead(s.playhead + (e.shiftKey ? 1 : 1 / 30)); }
      else if (e.key === "Escape") { s.selectItem(null); s.setPicking(false); s.setPendingRegion(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

export function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const project = useStore((s) => s.project);
  const step = useStore((s) => editStep(s));
  const selected = useStore((s) => s.selected);
  const item = useStore((s) => s.item);
  const left = useStore((s) => s.leftTab);
  const right = useStore((s) => s.rightTab);
  const helpOpen = useStore((s) => s.helpOpen);
  const paletteOpen = useStore((s) => s.paletteOpen);
  const pitchOpen = useStore((s) => s.pitchOpen);
  const sourceMedia = useStore((s) => s.sourceMedia);
  const layout = useStore((s) => s.layout);
  const { setRightTab: setRight, setLayout } = useStore.getState();
  const dragBase = useRef(layout);
  useAutosave();
  useShortcuts();

  useEffect(() => {
    useStore.getState().load(id).catch((e) => useStore.getState().fail("Chargement du projet", e));
  }, [id]);
  // Sélectionner un plan ou un élément ouvre l'onglet Plan, mais seulement depuis Chat, Film ou Plan :
  // depuis Mixage, Look, Qualité ou Export, on reste où on travaille.
  useEffect(() => {
    if (!selected && !item) return;
    const cur = useStore.getState().rightTab;
    if (item?.kind === "comment") {  // un commentaire choisi sur la timeline s'affiche dans la liste des commentaires
      if (cur === "consigne") useStore.getState().setAssistantView("comments");
      return;
    }
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
    <div className={`editor ${layout.leftOpen ? "" : "left-closed"} ${layout.rightOpen ? "" : "right-closed"}`} data-step={step} style={vars}>
      <TopBar onBack={onBack} />
      <aside className="left">
        <LeftRail tab={leftTab} />
        {layout.leftOpen && (
          <div className="rail-panel">
            <LeftWorkspace tab={leftTab} />
          </div>
        )}
        {layout.leftOpen && <Splitter dir="v" label="Largeur du panneau de gauche" onDrag={drag("left", 1)} onReset={() => setLayout({ left: defaultLayout().left })} />}
      </aside>
      <main className="center">
        <Player />
      </main>
      {layout.rightOpen && (
        <aside className="right">
          <Splitter dir="v" label="Largeur du panneau de droite" onDrag={drag("right", -1)} onReset={() => setLayout({ right: defaultLayout().right })} />
          <div className="right-body">
            <div className="tab-head">
              <h2>{rightTabTitle(right)}</h2>
              <span className="spacer" />
              <button className="icon-btn tab-close" title="Replier le panneau" aria-label="Replier le panneau de droite" onClick={() => setLayout({ rightOpen: false })}><Icon name="chevron-right" size={16} /></button>
            </div>
            <div className="tab-body">
              <RightWorkspace tab={right} />
            </div>
          </div>
          <RightRail tab={right} />
        </aside>
      )}
      <footer className="bottom">
        <Splitter dir="h" label="Hauteur de la timeline" onDrag={drag("bottom", -1)} onReset={() => setLayout({ bottom: defaultLayout().bottom })} />
        <TimelineView />
      </footer>
      <StatusBar />
      {helpOpen && <Shortcuts onClose={() => useStore.getState().setHelp(false)} />}
      {paletteOpen && <CommandPalette onClose={() => useStore.getState().setPalette(false)} />}
      {pitchOpen && <Pitch onClose={() => useStore.getState().setPitch(false)} />}
      {sourceMedia && <SourceMonitor mediaId={sourceMedia} onClose={() => useStore.getState().openSource(null)} />}
    </div>
  );
}
