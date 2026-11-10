// Moniteur source : on regarde un rush du chutier, on marque l'entrée (I) et la sortie (O), puis on le pose sur la
// timeline (au curseur, à la fin) ou en plan de coupe. Les marques sont mémorisées par rush dans le store : le
// glisser-déposer du chutier sur la timeline les réutilise.
import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import { Icon } from "../icons";
import { sourceRange, useStore } from "../store";
import { Kbd } from "./Shortcuts";
import { dialogOpen } from "./Dialog";

/** Timecode à l'image : m:ss:ff. */
function tc(t: number, fps: number): string {
  const r = Math.max(1, Math.round(fps));
  const frames = Math.max(0, Math.round(t * fps));
  const s = Math.floor(frames / r);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}:${String(frames % r).padStart(2, "0")}`;
}

export function SourceMonitor({ mediaId, onClose }: { mediaId: string; onClose: () => void }) {
  const project = useStore((s) => s.project);
  const allMarks = useStore((s) => s.sourceMarks);
  const playhead = useStore((s) => s.playhead);
  const media = project?.media.find((m) => m.id === mediaId);
  const marks = allMarks[mediaId];
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const stopScrub = useRef<(() => void) | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [broken, setBroken] = useState(false);
  const [done, setDone] = useState("");

  const dur = media?.duration ?? 0;
  const fps = media?.fps && media.fps > 0 ? media.fps : 30;
  const hasProxy = !!media?.proxy && !broken;
  const range = sourceRange(project, allMarks, mediaId);
  const pct = (x: number) => `${dur > 0 ? Math.max(0, Math.min(100, (x / dur) * 100)) : 0}%`;

  const seek = (x: number) => {
    const v = video.current;
    const clamped = Math.max(0, Math.min(x, dur));
    if (v) v.currentTime = clamped;
    setT(clamped);
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => undefined); else v.pause();
  };
  const step = (dir: -1 | 1, big = false) => {
    video.current?.pause();
    seek((video.current?.currentTime ?? t) + dir * (big ? 1 : 1 / fps));
  };
  const mark = (side: "in" | "out") => useStore.getState().markSource(mediaId, side, video.current?.currentTime ?? t);

  const insert = (where: "playhead" | "end") => {
    if (!range) { setDone("Portion trop courte : place l'entrée avant la sortie."); return; }
    const id = useStore.getState().insertClip(mediaId, range[0], range[1], where);
    setDone(id ? `${where === "end" ? "Ajouté à la fin" : "Inséré au curseur"} : ${(range[1] - range[0]).toFixed(1)} s` : "Insertion impossible.");
  };
  const asBroll = () => {
    if (!range) { setDone("Portion trop courte : place l'entrée avant la sortie."); return; }
    const st = useStore.getState();
    st.addOverlay({ media_id: mediaId, src_in: range[0], src_out: range[1], start: +st.playhead.toFixed(3), mode: "plein", reason: "Plan de coupe posé à la main." });
    setDone(`Plan de coupe posé au curseur : ${(range[1] - range[0]).toFixed(1)} s`);
  };

  // Clavier : écouté en phase de capture, comme le pitch, pour que les raccourcis de l'éditeur ne voient rien.
  const key = useRef<(e: KeyboardEvent) => void>(() => undefined);
  key.current = (e) => {
    const st = useStore.getState();
    if (dialogOpen() || st.helpOpen || st.paletteOpen || st.pitchOpen) return;  // un autre dialogue a le clavier
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const onButton = tag === "BUTTON";  // sur un bouton, Entrée et Espace l'activent au lieu de lancer le raccourci
    const eat = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === "Escape") { eat(); onClose(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (e.code === "Space" && !onButton) { eat(); toggle(); }
    else if (k === "i") { eat(); mark("in"); }
    else if (k === "o") { eat(); mark("out"); }
    else if (e.key === "ArrowLeft") { eat(); step(-1, e.shiftKey); }
    else if (e.key === "ArrowRight") { eat(); step(1, e.shiftKey); }
    else if (e.key === "," || (e.key === "Enter" && !onButton)) { eat(); insert("playhead"); }
    else if (e.key === ".") { eat(); insert("end"); }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => key.current(e);
    window.addEventListener("keydown", h, true);
    box.current?.focus();  // le clavier ne reste pas sur le rush cliqué dans le chutier
    return () => { window.removeEventListener("keydown", h, true); stopScrub.current?.(); };
  }, []);
  // Rush retiré du projet pendant que le moniteur est ouvert.
  useEffect(() => { if (!media) onClose(); }, [media]); // eslint-disable-line react-hooks/exhaustive-deps
  // Le timecode suit la lecture image par image (timeupdate ne tombe que 4 fois par seconde).
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => { if (video.current) setT(video.current.currentTime); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  if (!project || !media) return null;

  const startScrub = (e: React.MouseEvent) => {
    e.preventDefault();
    const rect = bar.current!.getBoundingClientRect();
    const at = (ev: MouseEvent | React.MouseEvent) => seek(((ev.clientX - rect.left) / rect.width) * dur);
    at(e);
    const move = (ev: MouseEvent) => at(ev);
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); stopScrub.current = null; };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    stopScrub.current = up;
  };

  const a = marks?.in ?? null;
  const b = marks?.out ?? null;
  const len = range ? range[1] - range[0] : 0;
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal source-monitor" role="dialog" aria-modal="true" aria-labelledby="source-title" data-testid="source-monitor" ref={box} tabIndex={-1}
        onClick={(e) => (e.target as HTMLElement).closest("button")?.blur()}>
        <header>
          <div>
            <span className="muted small">Moniteur source</span>
            <h2 id="source-title" title={media.filename}>{media.filename}</h2>
          </div>
          <button className="icon-btn" aria-label="Fermer" title="Fermer (Échap)" onClick={onClose}><Icon name="x" size={16} /></button>
        </header>
        <div className="sm-screen">
          {hasProxy ? (
            <video ref={video} src={api.proxyUrl(project.id, mediaId)} preload="auto" playsInline
              onClick={toggle} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
              onTimeUpdate={(e) => { if (!playing) setT(e.currentTarget.currentTime); }} onError={() => setBroken(true)} />
          ) : (
            <p className="sm-empty">Pas encore d'aperçu pour ce rush : lance d'abord une analyse ou un montage.<br />
              <span className="muted small">Tu peux quand même le poser en entier sur la timeline.</span></p>
          )}
        </div>
        <div className="sm-scrub" ref={bar} onMouseDown={hasProxy ? startScrub : undefined} role="slider" aria-label="Position dans le rush"
          aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(t)}>
          {(a != null || b != null) && <div className="sm-range" style={{ left: pct(a ?? 0), width: `calc(${pct(b ?? dur)} - ${pct(a ?? 0)})` }} />}
          {a != null && <div className="sm-mark in" style={{ left: pct(a) }} title={`Entrée ${tc(a, fps)}`} />}
          {b != null && <div className="sm-mark out" style={{ left: pct(b) }} title={`Sortie ${tc(b, fps)}`} />}
          <div className="sm-head" style={{ left: pct(t) }} />
        </div>
        <div className="sm-controls">
          <button className="icon-btn" onClick={() => step(-1)} disabled={!hasProxy} title="Image précédente (←)" aria-label="Image précédente"><Icon name="chevron-down" size={16} style={{ transform: "rotate(90deg)" }} /></button>
          <button className="icon-btn" onClick={toggle} disabled={!hasProxy} title="Lecture / pause (Espace)" aria-label={playing ? "Pause" : "Lecture"}><Icon name={playing ? "pause" : "play"} size={16} /></button>
          <button className="icon-btn" onClick={() => step(1)} disabled={!hasProxy} title="Image suivante (→)" aria-label="Image suivante"><Icon name="chevron-down" size={16} style={{ transform: "rotate(-90deg)" }} /></button>
          <span className="sm-time mono">{tc(t, fps)} <span className="muted">/ {tc(dur, fps)}</span></span>
          <span className="spacer" />
          <button className="small-btn" onClick={() => mark("in")} disabled={!hasProxy} title="Marquer l'entrée à la position lue (I)">Entrée <Kbd k="I" /></button>
          <button className="small-btn" onClick={() => mark("out")} disabled={!hasProxy} title="Marquer la sortie à la position lue (O)">Sortie <Kbd k="O" /></button>
          <button className="icon-btn" onClick={() => useStore.getState().markSource(mediaId, "clear")} disabled={a == null && b == null} title="Effacer les marques" aria-label="Effacer les marques"><Icon name="x" size={14} /></button>
        </div>
        <div className="sm-info muted small">
          {a != null || b != null
            ? <>Portion marquée : <b>{tc(a ?? 0, fps)}</b> → <b>{tc(b ?? dur, fps)}</b> · {len.toFixed(1)} s</>
            : <>Pas de marque : le rush entier ({fmtTime(dur, true)}) sera posé.</>}
          {" "}· Curseur du montage : <b>{fmtTime(playhead, true)}</b>
        </div>
        <footer className="sm-actions">
          <button className="primary" onClick={() => insert("playhead")} disabled={!range} title="Pose la portion au curseur du montage ; un plan sous le curseur est coupé en deux (, ou Entrée)">
            <Icon name="plus" size={14} />Insérer au curseur <Kbd k="," />
          </button>
          <button onClick={() => insert("end")} disabled={!range} title="Pose la portion après le dernier plan (.)"><Icon name="skip-forward" size={14} />Ajouter à la fin <Kbd k="." /></button>
          <button onClick={asBroll} disabled={!range} title="Pose la portion en plein écran sur la piste B-roll, au curseur du montage"><Icon name="image" size={14} />En plan de coupe (B-roll)</button>
          <span className="sm-done muted small" role="status" aria-live="polite">{done}</span>
        </footer>
      </div>
    </div>
  );
}
