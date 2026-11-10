import { memo, useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../api";
import { clipDur, clipStarts, commentTime, localToSrc, MEDIA_DRAG_TYPE, rampOf, Sel, snapPoints, snapTo, sourceRange, timelineDuration, useStore } from "../store";
import { AddSoundButton, VoiceOverButton } from "./AudioTools";
import { Icon } from "../icons";
import { dropStrips, loadStrip, peekStrip, Strip } from "../lib/filmstrip";
import "../styles/timeline.css";

/** Hauteur d'un plan sur la piste vidéo, en pixels (piste de 86 px moins les marges : voir `.track-video` et `.clip`). */
const CLIP_H = 74;

/** Vignettes du rush sous un plan : chaque case montre l'image du rush à l'endroit qu'elle recouvre. */
const Filmstrip = memo(function Filmstrip({ pid, mediaId, duration, srcIn, srcOut, width }: {
  pid: string; mediaId: string; duration: number; srcIn: number; srcOut: number; width: number;
}) {
  const [strip, setStrip] = useState<Strip | null>(peekStrip(pid, mediaId));
  useEffect(() => {
    let alive = true;
    loadStrip(pid, mediaId).then((st) => { if (alive) setStrip(st); });
    return () => { alive = false; };
  }, [pid, mediaId]);
  if (!strip || duration <= 0) return null;
  const tileW = (CLIP_H * strip.tw) / strip.th;
  const n = Math.max(1, Math.min(80, Math.ceil(width / tileW)));
  const step = Math.max(tileW, width / n);  // plan très large : au-delà de 80 cases, elles s'espacent
  const rows = Math.ceil(strip.count / strip.cols);
  const tiles = [];
  for (let j = 0; j < n; j++) {
    const x = j * step;
    const t = srcIn + (Math.min(width, x + tileW / 2) / width) * (srcOut - srcIn);
    const k = Math.max(0, Math.min(strip.count - 1, Math.floor((t / duration) * strip.count)));
    tiles.push(<i key={j} data-k={k} style={{
      left: x, width: tileW, height: CLIP_H, backgroundImage: `url(${strip.url})`,
      backgroundSize: `${strip.cols * tileW}px ${rows * CLIP_H}px`,
      backgroundPosition: `-${(k % strip.cols) * tileW}px -${Math.floor(k / strip.cols) * CLIP_H}px`,
    }} />);
  }
  return <div className="strip" aria-hidden="true">{tiles}</div>;
});

type Env = { hop: number; envelope: number[] };
const envCache: Record<string, Env | "loading"> = {};

function Waveform({ pid, mediaId, srcIn, srcOut, width }: {
  pid: string; mediaId: string; srcIn: number; srcOut: number; width: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [, force] = useState(0);
  const key = `${pid}/${mediaId}`;
  useEffect(() => {
    if (!envCache[key]) {
      envCache[key] = "loading";
      fetch(`/api/projects/${pid}/media/${mediaId}/signals`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d) envCache[key] = d;
          else delete envCache[key];
          force((x) => x + 1);
        });
    }
  }, [key, pid, mediaId]);
  useEffect(() => {
    const env = envCache[key];
    const cv = ref.current;
    if (!cv || !env || env === "loading") return;
    const w = Math.max(1, Math.floor(width));
    const h = 26;
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext("2d")!;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    const a = srcIn / env.hop;
    const b = srcOut / env.hop;
    for (let x = 0; x < w; x++) {
      const i = Math.floor(a + ((b - a) * x) / w);
      const db = env.envelope[i] ?? -90;
      const v = Math.max(0, Math.min(1, (db + 60) / 60));
      const bh = v * h;
      ctx.fillRect(x, (h - bh) / 2, 1, Math.max(1, bh));
    }
  });
  return <canvas ref={ref} className="wave" style={{ width }} />;
}

export function TimelineView() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const playhead = useStore((s) => s.playhead);
  const pps = useStore((s) => s.pxPerSec);
  const selectedIds = useStore((s) => s.selectedIds);
  const thumbs = useStore((s) => s.layout.thumbs);
  const item = useStore((s) => s.item);
  const captions = useStore((s) => s.captions);
  const catalog = useStore((s) => s.catalog);
  const loopRange = useStore((s) => s.loopRange);
  const builtAt = useStore((s) => s.builtAt);
  const pitchOpen = useStore((s) => s.pitchOpen);
  const { setPlayhead, select, moveClip, trimClip, setZoom, splitAtPlayhead, selectItem, addText } =
    useStore.getState();
  const scroller = useRef<HTMLDivElement>(null);
  const projectId = project?.id;
  useEffect(() => dropStrips, [projectId]);  // les pellicules d'un projet ne restent pas en mémoire après lui
  const [drag, setDrag] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [snapAt, setSnapAt] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);  // « Toutes les pistes » : rouvre les pistes vides
  // Arrivée d'un montage : les plans jouent une courte cascade, mais seulement une fois le pitch du réalisateur refermé.
  const [justBuilt, setJustBuilt] = useState(false);
  const handledBuilt = useRef(builtAt);  // un montage déjà là à l'ouverture de l'éditeur ne rejoue rien
  const builtTimer = useRef<number>();
  useEffect(() => {
    if (builtAt <= 0 || builtAt === handledBuilt.current || pitchOpen) return;
    handledBuilt.current = builtAt;
    setJustBuilt(true);
    window.clearTimeout(builtTimer.current);
    builtTimer.current = window.setTimeout(() => setJustBuilt(false), 1600);
  }, [builtAt, pitchOpen]);
  useEffect(() => () => window.clearTimeout(builtTimer.current), []);

  // Largeur visible (pour « zoom ajusté ») et zoom à la molette avec Ctrl, centré sous la souris.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => useStore.setState({ timelineWidth: el.clientWidth }));
    ro.observe(el);
    useStore.setState({ timelineWidth: el.clientWidth });
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const st = useStore.getState();
      const rect = el.getBoundingClientRect();
      const tAtMouse = (el.scrollLeft + e.clientX - rect.left) / st.pxPerSec;
      st.setZoom(st.pxPerSec * (e.deltaY < 0 ? 1.2 : 1 / 1.2));
      const pps2 = useStore.getState().pxPerSec;
      el.scrollLeft = Math.max(0, tAtMouse * pps2 - (e.clientX - rect.left));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => { ro.disconnect(); el.removeEventListener("wheel", onWheel); };
  }, []);

  const total = tl ? timelineDuration(tl) : 0;
  const starts = useMemo(() => (tl ? clipStarts(tl) : []), [tl]);
  const width = Math.max(total * pps + 200, 600);

  const ticks = useMemo(() => {
    const step = pps > 120 ? 1 : pps > 50 ? 2 : pps > 20 ? 5 : pps > 8 ? 10 : 30;
    const out: number[] = [];
    for (let t = 0; t <= total + step; t += step) out.push(t);
    return out;
  }, [pps, total]);

  // Le curseur reste visible pendant la lecture.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const x = playhead * pps;
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - 80);
  }, [playhead, pps]);

  if (!tl || !project) return null;

  const seekFromEvent = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setPlayhead((e.clientX - rect.left) / pps);
  };

  const startTrim = (e: React.MouseEvent, id: string, side: "in" | "out") => {
    e.stopPropagation();
    e.preventDefault();
    const c = tl.clips.find((x) => x.id === id)!;
    const x0 = e.clientX;
    const base = side === "in" ? c.src_in : c.src_out;
    let first = true;
    const i = tl.clips.findIndex((x) => x.id === id);
    const start = starts[i];
    const move = (ev: MouseEvent) => {
      let v = base + ((ev.clientX - x0) / pps) * (side === "in" ? c.speed || 1 : c.speed_end ?? (c.speed || 1));
      // Aimantation au curseur de lecture (Alt pour l'ignorer).
      const st0 = useStore.getState();
      const ph = st0.playhead;
      let snapped: number | null = null;
      if (!ev.altKey && ph > start && ph < start + clipDur(c)) {
        const srcAtPlayhead = c.src_in + localToSrc(c, ph - start);
        if (Math.abs(srcAtPlayhead - v) * pps / (c.speed || 1) < 8) { v = srcAtPlayhead; snapped = ph; }
      }
      setSnapAt(snapped);
      if (first) {
        trimClip(id, side, v);
        first = false;
      } else {
        // Les mouvements suivants remplacent l'étape d'annulation créée au premier.
        const st = useStore.getState();
        const past = st.past;
        st.trimClip(id, side, v);
        useStore.setState({ past });
      }
    };
    const up = () => {
      setSnapAt(null);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // Slip : Maj + glisser sur un plan décale son contenu (src_in et src_out ensemble), la durée ne change pas.
  // Le contenu suit la souris : vers la droite, on voit un passage plus tôt dans le rush. Une seule étape d'annulation.
  const startSlip = (e: React.MouseEvent, id: string) => {
    e.preventDefault();  // ni glisser-déposer natif du plan, ni sélection de texte
    const c = tl.clips.find((x) => x.id === id)!;
    const m = project.media.find((x) => x.id === c.media_id);
    const maxT = m && m.duration > 0 ? m.duration : Infinity;
    const perSec = (c.src_out - c.src_in) / Math.max(1e-3, clipDur(c));  // secondes de rush par seconde de montage (vitesse)
    const x0 = e.clientX;
    let first = true;
    let moved = false;  // Maj + clic sans bouger : ce n'est pas un slip, la sélection s'étend jusqu'à ce plan
    const move = (ev: MouseEvent) => {
      if (!moved) {
        if (Math.abs(ev.clientX - x0) < 4) return;
        moved = true;
        document.body.classList.add("slipping");
        if (useStore.getState().selectedIds.join() !== id) select(id);
      }
      const shift = Math.max(-c.src_in, Math.min(maxT - c.src_out, -((ev.clientX - x0) / pps) * perSec));
      const src_in = +(c.src_in + shift).toFixed(3);
      const st = useStore.getState();
      if (src_in === st.timeline?.clips.find((x) => x.id === id)?.src_in) return;  // rien n'a bougé : pas d'étape d'annulation
      const past = st.past;
      st.updateClip(id, { src_in, src_out: +(src_in + (c.src_out - c.src_in)).toFixed(3) });
      if (!first) useStore.setState({ past });  // les mouvements suivants remplacent l'étape créée au premier
      first = false;
    };
    const up = () => {
      if (!moved) useStore.getState().selectRange(id);
      document.body.classList.remove("slipping");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // Glisser un texte, un bruitage ou un plan de coupe sur sa piste (une seule étape d'annulation).
  const startMoveItem = (e: React.MouseEvent, kind: "text" | "sfx" | "overlay" | "impact" | "image" | "audio" | "marker", id: string, start: number) => {
    e.stopPropagation();
    e.preventDefault();
    selectItem({ kind, id });
    const x0 = e.clientX;
    let first = true;
    const points = [...snapPoints(tl), useStore.getState().playhead];
    const move = (ev: MouseEvent) => {
      let v = Math.max(0, start + (ev.clientX - x0) / pps);
      // Aimantation aux frontières des plans et au curseur (Alt pour l'ignorer).
      if (!ev.altKey) { const r = snapTo(v, points, pps); v = r.t; setSnapAt(r.snapped); } else setSnapAt(null);
      const st = useStore.getState();
      if (first) {
        st.updateItem(kind, id, kind === "marker" ? { t: v } : { start: v });
        first = false;
      } else {
        const past = st.past;
        st.updateItem(kind, id, kind === "marker" ? { t: v } : { start: v });
        useStore.setState({ past });
      }
    };
    const up = () => {
      setSnapAt(null);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  // Glisser une borne de la zone d'entrée/sortie (aimantée ; une seule étape d'annulation).
  const startMoveRange = (e: React.MouseEvent, side: "in" | "out") => {
    e.stopPropagation();
    e.preventDefault();
    const r0 = tl.range;
    if (!r0) return;
    const x0 = e.clientX;
    const base = side === "in" ? r0.start : r0.end;
    const points = [...snapPoints(tl), useStore.getState().playhead];
    let first = true;
    const move = (ev: MouseEvent) => {
      let v = Math.max(0, Math.min(total, base + (ev.clientX - x0) / pps));
      if (!ev.altKey) { const s = snapTo(v, points, pps); v = s.t; setSnapAt(s.snapped); } else setSnapAt(null);
      const next = side === "in" ? { start: Math.min(v, r0.end - 0.1), end: r0.end } : { start: r0.start, end: Math.max(v, r0.start + 0.1) };
      const st = useStore.getState();
      const past = st.past;
      st.setRange({ start: +next.start.toFixed(3), end: +next.end.toFixed(3) });
      if (!first) useStore.setState({ past });
      first = false;
    };
    const up = () => {
      setSnapAt(null);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  const isSel = (k: Sel["kind"], id: string) => item?.kind === k && item.id === id;
  const labelOf = (list: { id: string; label: string }[] | undefined, id: string) =>
    list?.find((x) => x.id === id)?.label ?? id.replace(/_/g, " ");

  const mediaName = (id: string) => project.media.find((m) => m.id === id)?.filename ?? "?";
  const colorFor = (id: string) => {
    const i = project.media.findIndex((m) => m.id === id);
    return `var(--rush-${(Math.max(0, i) % 6) + 1})`;
  };
  const tracks = {
    broll: !(tl.overlays || []).length, texts: !(tl.texts || []).length, sfx: !(tl.sfx || []).length, images: !(tl.images || []).length,
    impacts: !(tl.impacts || []).length, comments: !(tl.comments || []).length, video: !tl.clips.length,
    captions: tl.captions === "aucun" || !captions.length, music: !tl.music.track,
  };
  const empty = (b: boolean) => (b ? "is-empty" : "");
  const nSel = selectedIds.length;

  return (
    <div className={`timeline ${justBuilt ? "just-built" : ""}`}>
      <div className="timeline-toolbar" role="toolbar" aria-label="Outils de la timeline">
        <button className="icon-btn" onClick={() => useStore.getState().undo()} title="Annuler (Ctrl+Z)" aria-label="Annuler"><Icon name="undo" size={15} /></button>
        <button className="icon-btn" onClick={() => useStore.getState().redo()} title="Rétablir (Ctrl+Y)" aria-label="Rétablir"><Icon name="redo" size={15} /></button>
        <span className="sep" />
        <button className="tb-cut" onClick={splitAtPlayhead} title="Couper au curseur (S)" aria-label="Couper au curseur"><Icon name="scissors" size={14} /><span className="tb-label">Couper</span></button>
        <button
          onClick={() => useStore.getState().deleteSelection()}
          disabled={!item}
          aria-label={nSel > 1 ? `Supprimer les ${nSel} plans sélectionnés` : "Supprimer"}
          title={nSel > 1 ? `Supprimer les ${nSel} plans sélectionnés (Suppr)` : "Supprimer (Suppr)"}
        >
          <Icon name="trash" size={14} /><span className="tb-label">Supprimer{nSel > 1 ? ` (${nSel})` : ""}</span>
        </button>
        <span className="sep" />
        <button onClick={() => addText()} title="Ajouter un texte au curseur" aria-label="Ajouter un texte au curseur"><Icon name="plus" size={14} /><span className="tb-label">Texte</span></button>
        <VoiceOverButton />
        <AddSoundButton />
        <button onClick={() => useStore.getState().addMarker()} title="Poser un repère au curseur (M) : chapitre YouTube, repère XML" aria-label="Poser un repère au curseur"><Icon name="flag" size={14} /><span className="tb-label">Repère</span></button>
        {tl.range ? (
          <span className="range-tools">
            <span className="range-stat" title="Zone d'entrée/sortie : I et O (sans plan sélectionné), X = le plan au curseur">
              Zone <b>{fmtTime(tl.range.start)}</b> → <b>{fmtTime(tl.range.end)}</b>
            </span>
            <button className={`icon-btn ${loopRange ? "on" : ""}`} onClick={() => useStore.getState().setLoopRange(!loopRange)}
              title="Lecture en boucle de la zone (Ctrl+L)" aria-label="Boucle sur la zone" aria-pressed={loopRange}><Icon name="repeat" size={14} /></button>
            <button className="icon-btn" onClick={() => useStore.getState().deleteRange()} title="Supprimer le contenu de la zone et refermer (Maj+Suppr)" aria-label="Supprimer la zone"><Icon name="trash" size={14} /></button>
            <button className="icon-btn" onClick={() => useStore.getState().setRange(null)} title="Effacer la zone (Maj+X)" aria-label="Effacer la zone"><Icon name="x" size={14} /></button>
          </span>
        ) : (
          <button onClick={() => useStore.getState().rangeFromClip()} title="Zone d'entrée/sortie sur le plan au curseur (X) ; I et O la posent au curseur" aria-label="Zone d'entrée et de sortie sur le plan au curseur" disabled={!tl.clips.length}><Icon name="range" size={14} /><span className="tb-label">Zone</span></button>
        )}
        <span className="spacer" />
        {nSel > 1
          ? <span className="stat sel-count" data-testid="sel-count"><b>{nSel}</b> sur <b>{tl.clips.length}</b> plans · <b>{fmtTime(total)}</b></span>
          : <span className="stat"><b>{tl.clips.length}</b> plans · <b>{fmtTime(total)}</b></span>}
        <button className={`icon-btn ${thumbs ? "on" : ""}`} title="Vignettes dans les plans" aria-label="Vignettes dans les plans" aria-pressed={thumbs}
          data-testid="thumbs-toggle" onClick={() => useStore.getState().setLayout({ thumbs: !thumbs })}><Icon name="image" size={15} /></button>
        <label className="zoom">
          <span className="tb-label">Zoom</span>
          <input type="range" min={4} max={300} value={pps} aria-label="Zoom de la timeline" onChange={(e) => setZoom(+e.target.value)} />
        </label>
        <button className="icon-btn" title="Zoom ajusté : tout le montage (Maj+Z)" aria-label="Zoom ajusté" onClick={() => useStore.getState().zoomFit()}><Icon name="maximize" size={15} /></button>
        <button className={`icon-btn ${showAll ? "on" : ""}`} title="Afficher toutes les pistes, même vides" aria-label="Toutes les pistes" aria-pressed={showAll}
          onClick={() => setShowAll(!showAll)}><Icon name="layers" size={15} /></button>
        <button className="icon-btn" title="Raccourcis clavier (?)" aria-label="Raccourcis clavier" onClick={() => useStore.getState().setHelp(true)}><span className="kbd-q">?</span></button>
      </div>
      <div className="timeline-scroll" ref={scroller}>
        <div className={`timeline-inner ${showAll ? "show-all" : ""}`} style={{ width }}>
          <div className="ruler" onMouseDown={seekFromEvent}>
            {ticks.map((t) => (
              <div key={t} className="tick" style={{ left: t * pps }}>
                <span>{fmtTime(t)}</span>
              </div>
            ))}
            {(tl.markers || []).map((k) => (
              <div key={k.id} className={`marker-flag c-${k.color} ${isSel("marker", k.id) ? "sel" : ""} ${k.chapter ? "" : "no-chapter"}`}
                style={{ left: k.t * pps }} title={`${k.label || "Repère"} · ${fmtTime(k.t, true)}${k.chapter ? " · chapitre" : ""}`}
                onMouseDown={(e) => { startMoveItem(e, "marker", k.id, k.t); }}
                onDoubleClick={() => setPlayhead(k.t)}>
                <span>{k.label}</span>
              </div>
            ))}
          </div>
          <div className={`track track-broll ${empty(tracks.broll)}`} data-label="B-roll" onMouseDown={seekFromEvent}>
            {(tl.overlays || []).map((o) => (
              <div key={o.id} className={`item-block broll ${isSel("overlay", o.id) ? "sel" : ""}`}
                style={{ left: o.start * pps, width: Math.max(14, (o.src_out - o.src_in) * pps), "--rush": colorFor(o.media_id) } as React.CSSProperties}
                onMouseDown={(e) => startMoveItem(e, "overlay", o.id, o.start)}
                title={`${mediaName(o.media_id)} · ${o.mode === "plein" ? "plein écran" : "incrustation"}\n${o.reason}`}>
                <Icon name={o.mode === "plein" ? "square" : "image"} size={10} strokeWidth={2.4} />{mediaName(o.media_id)}
              </div>
            ))}
          </div>
          <div className={`track track-video ${empty(tracks.video)}`} data-label="Vidéo" onMouseDown={(e) => e.target === e.currentTarget && seekFromEvent(e)}
            onDragOver={(e) => {
              e.preventDefault();
              if (!e.dataTransfer.types.includes(MEDIA_DRAG_TYPE)) return;
              e.dataTransfer.dropEffect = "copy";
              if (e.target === e.currentTarget) setDropAt(tl.clips.length);  // au-delà du dernier plan : à la fin
            }}
            onDragLeave={(e) => { if (drag === null && !e.currentTarget.contains(e.relatedTarget as Node | null)) setDropAt(null); }}
            onDrop={(e) => {
              // Rush du chutier déposé : sa portion marquée (I/O dans le moniteur source), ou le rush entier.
              const mediaId = e.dataTransfer.getData(MEDIA_DRAG_TYPE);
              if (!mediaId) return;
              e.preventDefault();
              const at = dropAt ?? tl.clips.length;
              setDropAt(null);
              const st = useStore.getState();
              const r = sourceRange(st.project, st.sourceMarks, mediaId);
              if (!r) return st.notify("Portion du rush trop courte : vérifie ses marques d'entrée et de sortie.");
              st.insertClip(mediaId, r[0], r[1], at);
            }}>
            {tl.clips.map((c, i) => {
              const groupDrag = drag !== null && selectedIds.length > 1 && selectedIds.includes(tl.clips[drag]?.id ?? "");
              const w = clipDur(c) * pps;
              const md = project.media.find((x) => x.id === c.media_id);
              const inSel = selectedIds.includes(c.id);
              return (
                <div
                  key={c.id}
                  className={`clip ${inSel ? "sel" : ""} ${drag === i || (groupDrag && inSel) ? "dragging" : ""} ${dropAt === i ? "drop-before" : ""} ${i < 10 ? "cascade" : ""}`}
                  data-clip={c.id}
                  style={{ left: starts[i] * pps, width: Math.max(2, w), background: colorFor(c.media_id), "--i": i } as React.CSSProperties}
                  draggable
                  onDragStart={(e) => {
                    setDrag(i);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    const r = e.currentTarget.getBoundingClientRect();
                    setDropAt(e.clientX < r.left + r.width / 2 ? i : i + 1);
                  }}
                  onDragEnd={() => {
                    if (drag !== null && dropAt !== null) {
                      // Un plan d'une sélection multiple emmène tout le groupe ; sinon il bouge seul.
                      if (groupDrag) useStore.getState().moveClips(selectedIds, dropAt);
                      else if (dropAt !== drag && dropAt !== drag + 1) moveClip(drag, dropAt);
                    }
                    setDrag(null);
                    setDropAt(null);
                  }}
                  onMouseDown={(e) => {
                    if (e.ctrlKey || e.metaKey) { e.preventDefault(); useStore.getState().toggleSelect(c.id); return; }
                    if (e.shiftKey) { startSlip(e, c.id); return; }  // glisser : slip ; simple clic : étendre la sélection
                    // Un plan d'un groupe garde le groupe au clic enfoncé (on peut glisser le tout) ; le clic relâché le réduit.
                    if (!(inSel && selectedIds.length > 1)) select(c.id);
                    const r = e.currentTarget.getBoundingClientRect();
                    setPlayhead(starts[i] + (e.clientX - r.left) / pps);
                  }}
                  onClick={(e) => { if (!e.ctrlKey && !e.metaKey && !e.shiftKey && inSel && selectedIds.length > 1) select(c.id); }}
                  title={`${mediaName(c.media_id)} · ${c.src_in.toFixed(2)}–${c.src_out.toFixed(2)} s\nNote du réalisateur : ${Math.round(c.score * 100)}/100\n${c.reason}\nMaj + glisser : décaler le contenu du plan\nCtrl + clic : ajouter à la sélection · Maj + clic : sélectionner jusqu'ici`}
                >
                  {thumbs && w > 24 && md?.has_video && (
                    <Filmstrip pid={project.id} mediaId={c.media_id} duration={md.duration} srcIn={c.src_in} srcOut={c.src_out} width={w} />
                  )}
                  <div className="handle left" onMouseDown={(e) => startTrim(e, c.id, "in")} onClick={(e) => e.stopPropagation()} />
                  {c.transition !== "aucune" && i > 0 && (
                    <div className="trans-mark" title={`Transition : ${labelOf(catalog?.transitions, c.transition)}`}><Icon name="transition" size={12} strokeWidth={2.2} /></div>
                  )}
                  {w > 30 && (
                    <div className="clip-label">
                      {c.zoom > 1.001 && <span className="badge" title="Zoom"><Icon name="search" size={10} strokeWidth={2.4} /></span>}
                      {rampOf(c) ? <span className="badge" title="Rampe de vitesse">×{c.speed}→{c.speed_end}</span>
                        : c.speed !== 1 && <span className="badge">×{c.speed}</span>}
                      {(c.effects.length > 0 || c.audio_effects.length > 0) && (
                        <span className="badge" title={[...c.effects, ...c.audio_effects].join(", ")}><Icon name="sparkles" size={10} strokeWidth={2.4} /></span>
                      )}
                      {mediaName(c.media_id)}
                    </div>
                  )}
                  {w > 20 && <Waveform pid={project.id} mediaId={c.media_id} srcIn={c.src_in} srcOut={c.src_out} width={w} />}
                  {(c.keyframes || []).map((k, j) => (
                    <div key={j} className="kf-mark" style={{ left: k.t * pps }} title={`Image clé ${fmtTime(k.t, true)} · zoom ${Math.round(k.zoom * 100)} %`} />
                  ))}
                  {(c.fade_in ?? 0) > 0 && <div className="fade fade-in" title={`Fondu d'entrée ${c.fade_in} s`} style={{ width: Math.min(w, (c.fade_in ?? 0) * pps) }} />}
                  {(c.fade_out ?? 0) > 0 && <div className="fade fade-out" title={`Fondu de sortie ${c.fade_out} s`} style={{ width: Math.min(w, (c.fade_out ?? 0) * pps) }} />}
                  <div className={`score score-${c.score >= 0.7 ? "hi" : c.score >= 0.5 ? "mid" : "lo"}`} style={{ width: `${Math.round(c.score * 100)}%` }}
                    title={`Note du réalisateur : ${Math.round(c.score * 100)}/100`} />
                  <div className="handle right" onMouseDown={(e) => startTrim(e, c.id, "out")} onClick={(e) => e.stopPropagation()} />
                </div>
              );
            })}
            {dropAt === tl.clips.length && <div className="drop-end" style={{ left: total * pps }} />}
          </div>
          <div className={`track track-texts ${empty(tracks.texts)}`} data-label="Textes" onMouseDown={seekFromEvent}>
            {(tl.texts || []).map((x) => (
              <div key={x.id} className={`item-block text ${isSel("text", x.id) ? "sel" : ""}`}
                style={{ left: x.start * pps, width: Math.max(14, x.duration * pps) }}
                onMouseDown={(e) => startMoveItem(e, "text", x.id, x.start)}
                title={`${labelOf(catalog?.text_styles, x.style)} : ${x.text}`}>
                <Icon name="type" size={10} strokeWidth={2.4} />{x.text}
              </div>
            ))}
          </div>
          <div className={`track track-sfx ${empty(tracks.sfx)}`} data-label="Bruitages" onMouseDown={seekFromEvent}>
            {(tl.sfx || []).map((s) => (
              <div key={s.id} className={`item-block sfx ${isSel("sfx", s.id) ? "sel" : ""}`}
                style={{ left: s.start * pps }}
                onMouseDown={(e) => startMoveItem(e, "sfx", s.id, s.start)}
                title={`${labelOf(catalog?.sfx, s.kind)} · ${Math.round(s.volume * 100)} %`}>
                <Icon name="volume" size={10} strokeWidth={2.4} />{labelOf(catalog?.sfx, s.kind)}
              </div>
            ))}
          </div>
          <div className={`track track-images ${empty(tracks.images)}`} data-label="Images" onMouseDown={seekFromEvent}>
            {(tl.images || []).map((g) => (
              <div key={g.id} className={`item-block image ${isSel("image", g.id) ? "sel" : ""}`}
                style={{ left: g.start * pps, width: Math.max(16, (g.duration ?? Math.max(0, total - g.start)) * pps) }}
                onMouseDown={(e) => startMoveItem(e, "image", g.id, g.start)} title={g.asset}>
                <Icon name="image" size={10} strokeWidth={2.4} />{g.duration == null ? "Filigrane · " : ""}{g.asset}
              </div>
            ))}
          </div>
          <div className={`track track-impacts ${empty(tracks.impacts)}`} data-label="Impacts 3D" onMouseDown={seekFromEvent}>
            {(tl.impacts || []).map((s, k, all) => (
              <div key={s.id} className={`impact-pin k-${s.kind} ${isSel("impact", s.id) ? "sel" : ""}`}
                // les impacts posés au même instant s'affichent en éventail pour rester cliquables
                style={{ left: s.start * pps + 8 * all.slice(0, k).filter((o) => Math.abs(o.start - s.start) < 0.02).length }}
                onMouseDown={(e) => startMoveItem(e, "impact", s.id, s.start)}
                title={`${labelOf(catalog?.impacts, s.kind)} · ${Math.round(s.intensity * 100)} %${s.reason ? " · " + s.reason : ""}`} />
            ))}
          </div>
          <div className={`track track-comments ${empty(tracks.comments)}`} data-label="Commentaires" onMouseDown={seekFromEvent}>
            {(tl.comments || []).map((cm) => {
              const t = commentTime(tl, cm);
              if (t == null) return null;
              return (
                <div key={cm.id} className={`cm-pin s-${cm.status} ${isSel("comment", cm.id) ? "sel" : ""}`}
                  style={{ left: t * pps }} title={`${cm.text}${cm.reply ? "\nIA : " + cm.reply : ""}`}
                  onMouseDown={(e) => { e.stopPropagation(); selectItem({ kind: "comment", id: cm.id }); setPlayhead(t); }}>
                  <Icon name="message" size={14} strokeWidth={2.2} />
                </div>
              );
            })}
          </div>
          {Array.from({ length: Math.min(4, Math.max(1, ...(tl.audio || []).map((a) => a.lane + 1))) }, (_, lane) => (
            <div key={lane} className={`track track-audio ${empty(!(tl.audio || []).some((a) => a.lane === lane))}`} data-label={`Audio ${lane + 1}`} onMouseDown={seekFromEvent}>
              {(tl.audio || []).filter((a) => a.lane === lane).map((a) => (
                <div key={a.id} className={`item-block audio k-${a.kind} ${isSel("audio", a.id) ? "sel" : ""}`}
                  style={{ left: a.start * pps, width: Math.max(14, (a.src_out - a.src_in) * pps) }}
                  onMouseDown={(e) => startMoveItem(e, "audio", a.id, a.start)}
                  title={`${a.label || a.asset} · ${(a.src_out - a.src_in).toFixed(1)} s · ${Math.round(a.volume * 100)} %`}>
                  <Icon name={a.kind === "voix" ? "mic" : a.kind === "musique" ? "music" : "volume"} size={10} strokeWidth={2.4} />{a.label || a.asset}
                </div>
              ))}
            </div>
          ))}
          <div className={`track track-captions ${empty(tracks.captions)}`} data-label="Sous-titres" onMouseDown={seekFromEvent}>
            {tl.captions !== "aucun" &&
              captions.map((l, i) => (
                <div key={i} className="cap-block" style={{ left: l.start * pps, width: Math.max(2, (l.end - l.start) * pps) }}>
                  {(l.end - l.start) * pps > 30 && l.words.map((w) => w.text).join(" ")}
                </div>
              ))}
          </div>
          <div className={`track track-music ${empty(tracks.music)}`} data-label="Musique" onMouseDown={seekFromEvent}>
            {tl.music.track && (
              <div className="music-block" style={{ width: total * pps }}>
                <Icon name="music" size={11} strokeWidth={2.2} /> {tl.music.track.split(/[\\/]/).pop()} · {Math.round(tl.music.volume * 100)} %
                {tl.music.ducking && " · baisse auto sous la voix"}
              </div>
            )}
          </div>
          {tl.range && (
            <div className={`range-zone ${loopRange ? "loop" : ""}`} style={{ left: tl.range.start * pps, width: Math.max(2, (tl.range.end - tl.range.start) * pps) }}
              title={`Zone ${fmtTime(tl.range.start, true)} → ${fmtTime(tl.range.end, true)}`}>
              <div className="range-handle in" onMouseDown={(e) => startMoveRange(e, "in")} title="Entrée de la zone (glisser)" />
              <div className="range-handle out" onMouseDown={(e) => startMoveRange(e, "out")} title="Sortie de la zone (glisser)" />
            </div>
          )}
          {snapAt != null && <div className="snap-line" style={{ left: snapAt * pps }} />}
          <div className="playhead" style={{ left: playhead * pps }} />
        </div>
      </div>
    </div>
  );
}
