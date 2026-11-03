import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../api";
import { clipDur, clipStarts, Sel, timelineDuration, useStore } from "../store";

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
  const selected = useStore((s) => s.selected);
  const item = useStore((s) => s.item);
  const captions = useStore((s) => s.captions);
  const catalog = useStore((s) => s.catalog);
  const { setPlayhead, select, moveClip, trimClip, setZoom, splitAtPlayhead, deleteClip, selectItem, deleteItem, addText } =
    useStore.getState();
  const scroller = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);

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
    const move = (ev: MouseEvent) => {
      const v = base + ((ev.clientX - x0) / pps) * (c.speed || 1);
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
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // Glisser un texte, un bruitage ou un plan de coupe sur sa piste (une seule étape d'annulation).
  const startMoveItem = (e: React.MouseEvent, kind: "text" | "sfx" | "overlay", id: string, start: number) => {
    e.stopPropagation();
    e.preventDefault();
    selectItem({ kind, id });
    const x0 = e.clientX;
    let first = true;
    const move = (ev: MouseEvent) => {
      const v = Math.max(0, start + (ev.clientX - x0) / pps);
      const st = useStore.getState();
      if (first) {
        st.updateItem(kind, id, { start: v });
        first = false;
      } else {
        const past = st.past;
        st.updateItem(kind, id, { start: v });
        useStore.setState({ past });
      }
    };
    const up = () => {
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
    return ["#7c5cff", "#2bb3a3", "#e9853c", "#d0498d", "#4f8ff7", "#9bbf3a"][Math.max(0, i) % 6];
  };

  return (
    <div className="timeline">
      <div className="timeline-toolbar">
        <button onClick={splitAtPlayhead} title="Couper au curseur (S)">✂ Couper</button>
        <button onClick={() => addText()} title="Ajouter un texte au curseur">＋ Texte</button>
        <button
          onClick={() => item && (item.kind === "clip" ? deleteClip(item.id) : deleteItem(item.kind, item.id))}
          disabled={!item}
          title="Supprimer (Suppr)"
        >
          🗑 Supprimer
        </button>
        <button onClick={() => useStore.getState().undo()} title="Annuler (Ctrl+Z)">↶</button>
        <button onClick={() => useStore.getState().redo()} title="Rétablir (Ctrl+Y)">↷</button>
        <span className="spacer" />
        <span className="muted small">{tl.clips.length} plans · {fmtTime(total)}</span>
        <label className="zoom">
          Zoom
          <input type="range" min={4} max={300} value={pps} onChange={(e) => setZoom(+e.target.value)} />
        </label>
      </div>
      <div className="timeline-scroll" ref={scroller}>
        <div className="timeline-inner" style={{ width }}>
          <div className="ruler" onMouseDown={seekFromEvent}>
            {ticks.map((t) => (
              <div key={t} className="tick" style={{ left: t * pps }}>
                <span>{fmtTime(t)}</span>
              </div>
            ))}
          </div>
          <div className="track track-broll" data-label="B-roll" onMouseDown={seekFromEvent}>
            {(tl.overlays || []).map((o) => (
              <div key={o.id} className={`item-block broll ${isSel("overlay", o.id) ? "sel" : ""}`}
                style={{ left: o.start * pps, width: Math.max(4, (o.src_out - o.src_in) * pps), borderColor: colorFor(o.media_id) }}
                onMouseDown={(e) => startMoveItem(e, "overlay", o.id, o.start)}
                title={`${mediaName(o.media_id)} · ${o.mode === "plein" ? "plein écran" : "incrustation"}\n${o.reason}`}>
                {o.mode === "plein" ? "▣" : "◱"} {mediaName(o.media_id)}
              </div>
            ))}
          </div>
          <div className="track track-video" onMouseDown={(e) => e.target === e.currentTarget && seekFromEvent(e)}
            onDragOver={(e) => e.preventDefault()}>
            {tl.clips.map((c, i) => {
              const w = clipDur(c) * pps;
              return (
                <div
                  key={c.id}
                  className={`clip ${selected === c.id ? "sel" : ""} ${drag === i ? "dragging" : ""} ${dropAt === i ? "drop-before" : ""}`}
                  style={{ left: starts[i] * pps, width: Math.max(2, w), background: colorFor(c.media_id) }}
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
                    if (drag !== null && dropAt !== null && dropAt !== drag && dropAt !== drag + 1) moveClip(drag, dropAt);
                    setDrag(null);
                    setDropAt(null);
                  }}
                  onMouseDown={(e) => {
                    select(c.id);
                    const r = e.currentTarget.getBoundingClientRect();
                    setPlayhead(starts[i] + (e.clientX - r.left) / pps);
                  }}
                  title={`${mediaName(c.media_id)} · ${c.src_in.toFixed(2)}–${c.src_out.toFixed(2)} s\n${c.reason}`}
                >
                  <div className="handle left" onMouseDown={(e) => startTrim(e, c.id, "in")} />
                  {c.transition !== "aucune" && i > 0 && (
                    <div className="trans-mark" title={`Transition : ${labelOf(catalog?.transitions, c.transition)}`}>⟡</div>
                  )}
                  {w > 30 && (
                    <div className="clip-label">
                      {c.zoom > 1.001 && <span className="badge">🔍</span>}
                      {c.speed !== 1 && <span className="badge">×{c.speed}</span>}
                      {(c.effects.length > 0 || c.audio_effects.length > 0) && (
                        <span className="badge" title={[...c.effects, ...c.audio_effects].join(", ")}>✨</span>
                      )}
                      {mediaName(c.media_id)}
                    </div>
                  )}
                  {w > 20 && <Waveform pid={project.id} mediaId={c.media_id} srcIn={c.src_in} srcOut={c.src_out} width={w} />}
                  <div className="score" style={{ width: `${Math.round(c.score * 100)}%` }} />
                  <div className="handle right" onMouseDown={(e) => startTrim(e, c.id, "out")} />
                </div>
              );
            })}
            {dropAt === tl.clips.length && <div className="drop-end" style={{ left: total * pps }} />}
          </div>
          <div className="track track-texts" data-label="Textes" onMouseDown={seekFromEvent}>
            {(tl.texts || []).map((x) => (
              <div key={x.id} className={`item-block text ${isSel("text", x.id) ? "sel" : ""}`}
                style={{ left: x.start * pps, width: Math.max(4, x.duration * pps) }}
                onMouseDown={(e) => startMoveItem(e, "text", x.id, x.start)}
                title={`${labelOf(catalog?.text_styles, x.style)} : ${x.text}`}>
                T {x.text}
              </div>
            ))}
          </div>
          <div className="track track-sfx" data-label="Bruitages" onMouseDown={seekFromEvent}>
            {(tl.sfx || []).map((s) => (
              <div key={s.id} className={`item-block sfx ${isSel("sfx", s.id) ? "sel" : ""}`}
                style={{ left: s.start * pps }}
                onMouseDown={(e) => startMoveItem(e, "sfx", s.id, s.start)}
                title={`${labelOf(catalog?.sfx, s.kind)} · ${Math.round(s.volume * 100)} %`}>
                🔊 {labelOf(catalog?.sfx, s.kind)}
              </div>
            ))}
          </div>
          <div className="track track-captions" onMouseDown={seekFromEvent}>
            {tl.captions !== "aucun" &&
              captions.map((l, i) => (
                <div key={i} className="cap-block" style={{ left: l.start * pps, width: Math.max(2, (l.end - l.start) * pps) }}>
                  {(l.end - l.start) * pps > 30 && l.words.map((w) => w.text).join(" ")}
                </div>
              ))}
          </div>
          <div className="track track-music" onMouseDown={seekFromEvent}>
            {tl.music.track && (
              <div className="music-block" style={{ width: total * pps }}>
                ♪ {tl.music.track.split(/[\\/]/).pop()} · {Math.round(tl.music.volume * 100)} %
                {tl.music.ducking && " · baisse auto sous la voix"}
              </div>
            )}
          </div>
          <div className="playhead" style={{ left: playhead * pps }} />
        </div>
      </div>
    </div>
  );
}
