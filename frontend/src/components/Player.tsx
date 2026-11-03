import { useEffect, useMemo, useRef, useState } from "react";
import { api, Clip, fmtTime } from "../api";
import { clipAt, clipStarts, timelineDuration, useStore } from "../store";

// Lecture de la timeline en direct, sans rendu : deux lecteurs vidéo alternent
// pour que les coupes entre fichiers soient instantanées.
export function Player() {
  const project = useStore((s) => s.project);
  const timeline = useStore((s) => s.timeline);
  const playhead = useStore((s) => s.playhead);
  const playing = useStore((s) => s.playing);
  const captions = useStore((s) => s.captions);
  const setPlayhead = useStore((s) => s.setPlayhead);
  const setPlaying = useStore((s) => s.setPlaying);

  const v0 = useRef<HTMLVideoElement>(null);
  const v1 = useRef<HTMLVideoElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const active = useRef(0);
  const cur = useRef(-1);
  const reported = useRef(0);
  const raf = useRef(0);
  const [shown, setShown] = useState(0);

  const vids = () => [v0.current!, v1.current!];
  const tl = timeline;
  const total = tl ? timelineDuration(tl) : 0;

  const urlFor = (c: Clip) => (project ? api.proxyUrl(project.id, c.media_id) : "");

  function prepare(el: HTMLVideoElement, c: Clip, offset: number) {
    const url = urlFor(c);
    if (el.dataset.src !== url) {
      el.src = url;
      el.dataset.src = url;
    }
    const target = c.src_in + Math.max(0, offset);
    if (Math.abs(el.currentTime - target) > 0.04) el.currentTime = target;
    el.style.transform = `scale(${c.zoom})`;
    el.volume = Math.min(1, Math.max(0, c.volume));
  }

  function showAt(t: number) {
    if (!tl) return null;
    const hit = clipAt(tl, Math.min(t, Math.max(0, total - 1e-3)));
    if (!hit) return null;
    const el = vids()[active.current];
    prepare(el, tl.clips[hit.index], t - hit.start);
    cur.current = hit.index;
    return hit;
  }

  function preloadNext() {
    if (!tl) return;
    const n = tl.clips[cur.current + 1];
    if (n) prepare(vids()[1 - active.current], n, 0);
  }

  // Arrêt / aperçu de l'image sous le curseur.
  useEffect(() => {
    if (playing || !tl) return;
    showAt(playhead);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playhead, tl, playing]);

  // Boucle de lecture.
  useEffect(() => {
    if (!tl) return;
    const [a, b] = vids();
    if (!playing) {
      a.pause();
      b.pause();
      music.current?.pause();
      cancelAnimationFrame(raf.current);
      return;
    }
    let t0 = playhead;
    if (t0 >= total - 0.05) t0 = 0;
    if (!showAt(t0)) {
      setPlaying(false);
      return;
    }
    reported.current = t0;
    const el = vids()[active.current];
    el.play().catch(() => setPlaying(false));
    preloadNext();
    const mu = music.current;
    if (mu && tl.music.track) {
      if (mu.duration) mu.currentTime = t0 % mu.duration;
      mu.volume = Math.min(1, tl.music.volume * 1.5);
      mu.play().catch(() => undefined);
    }
    const starts = clipStarts(tl);

    const tick = () => {
      const vs = vids();
      const el = vs[active.current];
      const c = tl.clips[cur.current];
      if (!c) return;
      const t = starts[cur.current] + (el.currentTime - c.src_in);
      reported.current = t;
      setPlayhead(t);
      if (el.currentTime >= c.src_out - 1 / 60 || el.ended) {
        const ni = cur.current + 1;
        const n = tl.clips[ni];
        if (!n) {
          setPlaying(false);
          setPlayhead(total);
          return;
        }
        const contiguous = n.media_id === c.media_id && Math.abs(n.src_in - c.src_out) < 0.08;
        if (contiguous && Math.abs(n.zoom - c.zoom) < 1e-3) {
          cur.current = ni;
        } else {
          const other = vs[1 - active.current];
          prepare(other, n, 0);
          other.play().catch(() => undefined);
          el.pause();
          active.current = 1 - active.current;
          setShown(active.current);
          cur.current = ni;
          preloadNext();
        }
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, tl]);

  // Saut manuel pendant la lecture (clic sur la timeline).
  useEffect(() => {
    if (!playing || !tl) return;
    if (Math.abs(playhead - reported.current) > 0.25) {
      cancelAnimationFrame(raf.current);
      setPlaying(false);
      setTimeout(() => setPlaying(true), 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playhead]);

  const line = useMemo(
    () => captions.find((l) => playhead >= l.start && playhead < l.end + 0.05),
    [captions, playhead],
  );
  const fmt = tl?.format ?? "16:9";
  const aspect = fmt === "16:9" ? "16 / 9" : fmt === "9:16" ? "9 / 16" : "1 / 1";
  const karaoke = tl?.captions === "karaoke";

  return (
    <div className="player">
      <div className="player-stage">
        <div className={`frame frame-${fmt.replace(":", "x")}`} style={{ aspectRatio: aspect }}>
          <video ref={v0} className={shown === 0 ? "on" : ""} playsInline preload="auto" />
          <video ref={v1} className={shown === 1 ? "on" : ""} playsInline preload="auto" />
          {!tl?.clips.length && (
            <div className="frame-empty">
              Importe tes rushes puis clique sur <b>Monter avec l'IA</b>
            </div>
          )}
          {line && tl?.captions !== "aucun" && (
            <div className={`caption ${karaoke ? "karaoke" : "classic"} cap-${fmt.replace(":", "x")}`}>
              {line.words.map((w, i) => (
                <span key={i} className={`cw ${karaoke && playhead >= w.start && playhead < (line.words[i + 1]?.start ?? line.end) ? "hl" : ""}`}>
                  {karaoke ? w.text.toUpperCase() : w.text}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      {tl?.music.track && project && (
        <audio ref={music} src={api.musicUrl(project.id, tl.music.track)} loop preload="auto" />
      )}
      <div className="transport">
        <button className="icon-btn" title="Début" onClick={() => setPlayhead(0)}>⏮</button>
        <button className="play-btn" title="Lecture / pause (Espace)" onClick={() => setPlaying(!playing)}
          disabled={!tl?.clips.length}>
          {playing ? "❚❚" : "▶"}
        </button>
        <span className="tc">
          {fmtTime(playhead, true)} <span className="muted">/ {fmtTime(total, true)}</span>
        </span>
      </div>
    </div>
  );
}
