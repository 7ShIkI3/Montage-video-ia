import { useEffect, useMemo, useRef, useState } from "react";
import { api, Clip, fmtTime, OverlayItem, Timeline } from "../api";
import { clipAt, clipStarts, commentTime, timelineDuration, useStore } from "../store";
import { cssFilter, fxClasses, overlaysFor } from "../fxPreview";

// Lecture de la timeline en direct, sans rendu : deux lecteurs vidéo alternent
// pour que les coupes entre fichiers soient instantanées. Les effets, transitions,
// textes, bruitages et plans de coupe sont simulés en CSS / Web Audio.
const sfxCache: Record<string, HTMLAudioElement> = {};
function playSfx(kind: string, volume: number) {
  const base = (sfxCache[kind] ||= new Audio(api.sfxUrl(kind)));
  const a = base.cloneNode(true) as HTMLAudioElement;
  a.volume = Math.max(0, Math.min(1, volume));
  a.play().catch(() => undefined);
}

type Faces = { hop: number; cx: number[] } | null;
const facesCache: Record<string, Faces | "loading"> = {};

function faceCx(f: Faces | "loading" | undefined, t: number): number | null {
  if (!f || f === "loading" || !f.cx.length) return null;
  const x = t / f.hop;
  const i = Math.max(0, Math.min(f.cx.length - 1, Math.floor(x)));
  const j = Math.min(f.cx.length - 1, i + 1);
  return f.cx[i] + (f.cx[j] - f.cx[i]) * Math.max(0, Math.min(1, x - i));
}

/** Position object-position (en %) qui centre le visage dans un cadre « cover », comme à l'export. */
function framePos(el: HTMLVideoElement, cx: number | null): string {
  if (cx == null || !el.videoWidth) return "50% 50%";
  const fw = el.clientWidth, fh = el.clientHeight;
  const s = Math.max(fw / el.videoWidth, fh / el.videoHeight);
  const sw = el.videoWidth * s;
  if (sw - fw < 1) return "50% 50%";
  const off = Math.max(0, Math.min(sw - fw, cx * sw - fw / 2));
  return `${((off / (sw - fw)) * 100).toFixed(2)}% 50%`;
}

function overlayAt(tl: Timeline, t: number): OverlayItem | undefined {
  return (tl.overlays || []).find((o) => t >= o.start && t < o.start + (o.src_out - o.src_in));
}

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
  const vo = useRef<HTMLVideoElement>(null);
  const veil = useRef<HTMLDivElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const active = useRef(0);
  const cur = useRef(-1);
  const reported = useRef(0);
  const raf = useRef(0);
  const [shown, setShown] = useState(0);
  const [curIdx, setCurIdx] = useState(-1);
  const [, setFacesTick] = useState(0);

  // Trajectoires de visage (recadrage auto) chargées une fois par média.
  useEffect(() => {
    if (!project) return;
    for (const m of project.media) {
      const key = `${project.id}/${m.id}`;
      if (facesCache[key]) continue;
      facesCache[key] = "loading";
      fetch(`/api/projects/${project.id}/media/${m.id}/signals`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          facesCache[key] = d?.faces ?? null;
          setFacesTick((x) => x + 1);
        })
        .catch(() => delete facesCache[key]);
    }
  }, [project]);

  const vids = () => [v0.current!, v1.current!];
  const tl = timeline;
  const total = tl ? timelineDuration(tl) : 0;
  const look = tl?.look || "aucun";

  const urlFor = (mid: string) => (project ? api.proxyUrl(project.id, mid) : "");

  function baseTransform(c: Clip, progress = 0) {
    const slow = c.effects.includes("zoom_lent") ? 1 + 0.12 * progress : 1;
    return `scale(${c.zoom * slow})${c.effects.includes("miroir") ? " scaleX(-1)" : ""}`;
  }

  function prepare(el: HTMLVideoElement, c: Clip, offset: number) {
    const url = urlFor(c.media_id);
    if (el.dataset.src !== url) {
      el.src = url;
      el.dataset.src = url;
    }
    const target = c.src_in + Math.max(0, offset) * (c.speed || 1);
    if (Math.abs(el.currentTime - target) > 0.04) el.currentTime = target;
    el.style.transform = baseTransform(c, offset / Math.max(0.1, (c.src_out - c.src_in) / (c.speed || 1)));
    el.style.filter = cssFilter(c.effects, look);
    reframe(el, c, target);
    el.playbackRate = c.speed || 1;
    el.muted = c.audio_effects.includes("muet");
    el.volume = Math.min(1, Math.max(0, c.volume));
  }

  function reframe(el: HTMLVideoElement, c: Clip, srcTime: number) {
    const f = tl?.reframe !== false && project ? facesCache[`${project.id}/${c.media_id}`] : null;
    el.style.objectPosition = framePos(el, faceCx(f, srcTime));
    if (!el.videoWidth) el.onloadedmetadata = () => (el.style.objectPosition = framePos(el, faceCx(f, el.currentTime)));
  }

  function animateTransition(el: HTMLVideoElement, c: Clip) {
    const d = Math.max(80, (c.transition_duration || 0.35) * 1000);
    const v = veil.current;
    const tr = c.transition;
    const base = el.style.transform;
    if ((tr === "fondu_noir" || tr === "fondu_blanc" || tr === "flash") && v) {
      v.style.background = tr === "fondu_noir" ? "#000" : "#fff";
      v.animate([{ opacity: tr === "flash" ? 0.9 : 1 }, { opacity: 0 }], { duration: tr === "flash" ? Math.min(d, 220) : d, easing: "ease-out" });
    } else if (tr === "zoom_avant") {
      el.animate([{ transform: `${base} scale(1.25)` }, { transform: base }], { duration: d, easing: "ease-out" });
    } else if (tr === "glissement") {
      el.animate([{ transform: `translateX(100%) ${base}` }, { transform: base }], { duration: d, easing: "ease-out" });
    } else if (tr === "flou") {
      el.animate([{ filter: `${el.style.filter === "none" ? "" : el.style.filter} blur(14px)` }, { filter: el.style.filter }], { duration: d });
    } else if (tr === "glitch") {
      el.animate(
        [
          { transform: `${base} translate(-3%, 0)`, filter: "hue-rotate(90deg) saturate(3)" },
          { transform: `${base} translate(3%, 1%)`, filter: "hue-rotate(-90deg)" },
          { transform: `${base} translate(-1%, -1%)`, filter: "invert(0.2)" },
          { transform: base, filter: el.style.filter },
        ],
        { duration: d },
      );
    }
  }

  function syncOverlay(t: number, play: boolean) {
    const el = vo.current;
    if (!el || !tl) return;
    const o = overlayAt(tl, t);
    if (!o) {
      if (el.dataset.on) {
        el.pause();
        delete el.dataset.on;
        el.className = "ov";
      }
      return;
    }
    const url = urlFor(o.media_id);
    if (el.dataset.src !== url) {
      el.src = url;
      el.dataset.src = url;
    }
    const target = o.src_in + (t - o.start);
    if (!play || Math.abs(el.currentTime - target) > 0.3) el.currentTime = target;
    el.style.filter = cssFilter([], look);
    el.dataset.on = "1";
    el.className = `ov on ov-${o.mode}`;
    if (play && el.paused) el.play().catch(() => undefined);
    if (!play && !el.paused) el.pause();
  }

  function showAt(t: number) {
    if (!tl) return null;
    const hit = clipAt(tl, Math.min(t, Math.max(0, total - 1e-3)));
    if (!hit) return null;
    const el = vids()[active.current];
    prepare(el, tl.clips[hit.index], t - hit.start);
    cur.current = hit.index;
    setCurIdx(hit.index);
    syncOverlay(t, false);
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
      vo.current?.pause();
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
    if (t0 < 0.05) animateTransition(el, tl.clips[cur.current]);
    preloadNext();
    const mu = music.current;
    if (mu && tl.music.track) {
      if (mu.duration) mu.currentTime = t0 % mu.duration;
      mu.volume = Math.min(1, tl.music.volume * 1.5);
      mu.play().catch(() => undefined);
    }
    const starts = clipStarts(tl);
    const sfx = [...(tl.sfx || [])].sort((x, y) => x.start - y.start);

    const tick = () => {
      const vs = vids();
      const el = vs[active.current];
      const c = tl.clips[cur.current];
      if (!c) return;
      const sp = c.speed || 1;
      const t = starts[cur.current] + (el.currentTime - c.src_in) / sp;
      for (const s of sfx) if (s.start > reported.current && s.start <= t) playSfx(s.kind, s.volume);
      reported.current = t;
      setPlayhead(t);
      syncOverlay(t, true);
      reframe(el, c, el.currentTime);
      if (c.effects.includes("zoom_lent")) {
        el.style.transform = baseTransform(c, (el.currentTime - c.src_in) / Math.max(0.1, c.src_out - c.src_in));
      }
      if (el.currentTime >= c.src_out - 1 / 60 || el.ended) {
        const ni = cur.current + 1;
        const n = tl.clips[ni];
        if (!n) {
          setPlaying(false);
          setPlayhead(total);
          return;
        }
        const contiguous = n.media_id === c.media_id && Math.abs(n.src_in - c.src_out) < 0.08;
        const same =
          contiguous && Math.abs(n.zoom - c.zoom) < 1e-3 && n.speed === c.speed && n.transition === "aucune" &&
          n.effects.join() === c.effects.join() && n.audio_effects.join() === c.audio_effects.join();
        if (same) {
          cur.current = ni;
        } else {
          const other = vs[1 - active.current];
          prepare(other, n, 0);
          other.play().catch(() => undefined);
          el.pause();
          active.current = 1 - active.current;
          setShown(active.current);
          cur.current = ni;
          animateTransition(other, n);
          preloadNext();
        }
        setCurIdx(ni);
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
  const bubbles = tl ? (tl.comments || []).filter((c) => {
    const t = commentTime(tl, c);
    return t != null && playhead >= t - 0.05 && playhead < t + 2;
  }) : [];
  const texts = (tl?.texts || []).filter((x) => playhead >= x.start && playhead < x.start + x.duration);
  const fmt = tl?.format ?? "16:9";
  const aspect = fmt === "16:9" ? "16 / 9" : fmt === "9:16" ? "9 / 16" : "1 / 1";
  const karaoke = tl?.captions === "karaoke";
  const cc = tl?.clips[curIdx];
  const fxEff = cc ? cc.effects : [];
  const layers = overlaysFor(fxEff, look);

  return (
    <div className="player">
      <div className="player-stage">
        <div className={`frame frame-${fmt.replace(":", "x")}`} style={{ aspectRatio: aspect }}>
          <div className={`fxwrap ${fxClasses(fxEff, look).join(" ")}`}>
            <video ref={v0} className={shown === 0 ? "on" : ""} playsInline preload="auto" />
            <video ref={v1} className={shown === 1 ? "on" : ""} playsInline preload="auto" />
          </div>
          <video ref={vo} className="ov" playsInline muted preload="auto" />
          {layers.vignette && <div className="layer vignette" />}
          {layers.grain && <div className="layer grain" />}
          {layers.letterbox && <div className="layer letterbox" />}
          <div ref={veil} className="layer veil" />
          {!tl?.clips.length && (
            <div className="frame-empty">
              Importe tes rushes puis clique sur <b>Monter avec l'IA</b>
            </div>
          )}
          {bubbles.map((c) => (
            <div key={c.id} className={`cm-bubble s-${c.status}`}>💬 {c.text}</div>
          ))}
          {texts.map((x) => (
            <div key={x.id} className={`ti ti-${x.style} pos-${x.position}`}>
              {x.style === "titre" || x.style === "gros_texte" ? x.text.toUpperCase() : x.text}
            </div>
          ))}
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
        {cc && (cc.speed !== 1 || cc.effects.length > 0 || look !== "aucun") && (
          <span className="fx-badge" title="Effets du plan courant">
            ✨ {[look !== "aucun" ? look : "", cc.speed !== 1 ? `×${cc.speed}` : "", ...cc.effects].filter(Boolean).join(" · ").replace(/_/g, " ")}
          </span>
        )}
      </div>
    </div>
  );
}
