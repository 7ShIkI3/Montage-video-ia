import { useEffect, useMemo, useRef, useState } from "react";
import { api, Clip, fmtTime, ImpactItem, OverlayItem, Timeline } from "../api";
import { clipAt, clipDur, clipStarts, commentTime, kfAt, localToSrc, rampOf, speedAt, srcToLocal, timelineDuration, useStore } from "../store";
import { cssFilter, fxClasses, mixGain, overlaysFor } from "../fxPreview";
import { Icon } from "../icons";
import { LIVE_KINDS } from "../stages";
import { LiveDirector } from "./LiveDirector";

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

export type Geo = { fw: number; fh: number; sw: number; sh: number; offX: number; offY: number };

/** Cadrage exact du plan, identique à l'export : image agrandie (cover × zoom) puis recadrée autour du point
 * de cadrage (zone choisie, visage suivi ou centre). */
function geometry(el: HTMLVideoElement, zoom: number, cx: number, cy: number): Geo | null {
  const box = el.parentElement;
  if (!box || !el.videoWidth) return null;
  const fw = box.clientWidth, fh = box.clientHeight;
  const s = Math.max(fw / el.videoWidth, fh / el.videoHeight) * Math.max(1, zoom);
  const sw = el.videoWidth * s, sh = el.videoHeight * s;
  const offX = Math.max(0, Math.min(sw - fw, cx * sw - fw / 2));
  const offY = Math.max(0, Math.min(sh - fh, cy * sh - fh / 2));
  return { fw, fh, sw, sh, offX, offY };
}

function applyGeo(el: HTMLVideoElement, g: Geo | null) {
  if (!g) return;
  Object.assign(el.style, { inset: "auto", left: `${-g.offX}px`, top: `${-g.offY}px`, width: `${g.sw}px`,
    height: `${g.sh}px`, objectFit: "fill" });
}

/** Zone du rush (0..1) → rectangle en pixels dans le cadre affiché. */
export function regionToFrame(g: Geo, r: { x: number; y: number; w: number; h: number }) {
  return { left: r.x * g.sw - g.offX, top: r.y * g.sh - g.offY, width: r.w * g.sw, height: r.h * g.sh };
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
  const rate = useStore((s) => s.rate);
  const live = useStore((s) => !!s.job && LIVE_KINDS.includes(s.job.kind));

  const v0 = useRef<HTMLVideoElement>(null);
  const v1 = useRef<HTMLVideoElement>(null);
  const vo = useRef<HTMLVideoElement>(null);
  const veil = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const fxLayer = useRef<HTMLDivElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  // Changement de vitesse (J/K/L) : appliqué tout de suite aux vidéos en cours et à la musique,
  // sans attendre la boucle d'images (qui ne tourne pas dans un onglet en arrière-plan).
  const prevRate = useRef(1);
  useEffect(() => {
    const f = rate / prevRate.current;
    prevRate.current = rate;
    for (const el of [v0.current, v1.current]) if (el && Math.abs(f - 1) > 1e-6) el.playbackRate = el.playbackRate * f;
    if (music.current) music.current.playbackRate = rate;
  }, [rate]);
  const active = useRef(0);
  const cur = useRef(-1);
  const reported = useRef(0);
  const raf = useRef(0);
  const [shown, setShown] = useState(0);
  const [curIdx, setCurIdx] = useState(-1);
  const [, setFacesTick] = useState(0);
  const [, setGeoTick] = useState(0);
  const geo = useRef<(Geo & { mediaId: string; clip: string; srcT: number }) | null>(null);
  const picking = useStore((s) => s.picking);
  const pending = useStore((s) => s.pendingRegion);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

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
    return `scale(${slow})${c.effects.includes("miroir") ? " scaleX(-1)" : ""}`;
  }

  function prepare(el: HTMLVideoElement, c: Clip, offset: number) {
    const url = urlFor(c.media_id);
    if (el.dataset.src !== url) {
      el.src = url;
      el.dataset.src = url;
    }
    const target = c.src_in + localToSrc(c, Math.max(0, offset));
    if (Math.abs(el.currentTime - target) > 0.04) el.currentTime = target;
    el.style.transform = baseTransform(c, offset / Math.max(0.1, clipDur(c)));
    el.style.filter = cssFilter(c.effects, look, c.color);
    reframe(el, c, target);
    el.playbackRate = speedAt(c, Math.max(0, offset)) * useStore.getState().rate;
    const rushes = mixGain(tl?.mix, "rushes");  // mixeur : son des plans
    el.muted = c.audio_effects.includes("muet") || rushes === 0;
    el.volume = Math.min(1, Math.max(0, c.volume * rushes));
  }

  function reframe(el: HTMLVideoElement, c: Clip, srcTime: number) {
    const f = tl?.reframe !== false && project ? facesCache[`${project.id}/${c.media_id}`] : null;
    const run = (t: number) => {
      // Images clés : zoom et cadre animés, comme au rendu.
      const k = c.keyframes?.length ? kfAt(c, srcToLocal(c, Math.max(0, t - c.src_in))) : null;
      const cx = k ? k.x : c.focus_x ?? faceCx(f, t) ?? 0.5;
      const g = k ? geometry(el, k.zoom, k.x, k.y) : geometry(el, c.zoom, cx, c.focus_y ?? 0.5);
      applyGeo(el, g);
      if (el === vids()[active.current] && g) {
        geo.current = { ...g, mediaId: c.media_id, clip: c.id, srcT: t };
        setGeoTick((x) => x + 1);
      }
    };
    run(srcTime);
    if (!el.videoWidth) el.onloadedmetadata = () => run(el.currentTime);
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
    } else if (tr === "cube_3d") {
      el.animate([{ transform: `perspective(1100px) rotateY(-88deg) ${base}`, transformOrigin: "left center", filter: "brightness(.6)" },
        { transform: `perspective(1100px) rotateY(0deg) ${base}`, transformOrigin: "left center", filter: el.style.filter }],
        { duration: d, easing: "cubic-bezier(.6,0,.3,1)" });
    } else if (tr === "flip_3d") {
      el.animate([{ transform: `perspective(1100px) rotateY(-90deg) scale(.8) ${base}` }, { transform: `perspective(1100px) rotateY(0deg) ${base}` }],
        { duration: d, easing: "cubic-bezier(.2,.7,.3,1.2)" });
    } else if (tr === "porte_3d") {
      el.animate([{ transform: `perspective(1000px) rotateY(-100deg) ${base}`, transformOrigin: "left center" },
        { transform: `perspective(1000px) rotateY(0deg) ${base}`, transformOrigin: "left center" }], { duration: d, easing: "ease-out" });
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

  function animateEntry3d(el: HTMLVideoElement, c: Clip) {
    const base = el.style.transform;
    if (c.effects.includes("3d_bascule")) {
      el.animate([{ transform: `perspective(900px) rotateX(78deg) ${base}`, transformOrigin: "center bottom" },
        { transform: `perspective(900px) rotateX(0deg) ${base}`, transformOrigin: "center bottom" }],
        { duration: 650, easing: "cubic-bezier(.2,.8,.3,1.25)" });
    } else if (c.effects.includes("3d_rotation")) {
      el.animate([{ transform: `perspective(900px) translateZ(-900px) rotateY(105deg) rotateZ(-8deg) ${base}` },
        { transform: `perspective(900px) translateZ(0) rotateY(0) ${base}` }], { duration: 600, easing: "cubic-bezier(.2,.8,.3,1.2)" });
    }
  }

  // Impacts 3D (aperçu CSS) : secousse, zoom choc, flashs, étincelles, onde de choc.
  function fireImpact(im: ImpactItem) {
    const A = Math.max(0.2, Math.min(2.5, im.intensity));
    const st = stage.current;
    const v = veil.current;
    if (im.kind === "secousse" && st) {
      const k = 6 * A;
      st.animate([0, 1, 2, 3, 4, 5, 6].map((i) => {
        const f = Math.exp(-i * 0.7);
        return { transform: `perspective(900px) scale(${1 + 0.06 * f * A}) rotateZ(${(i % 2 ? -1 : 1) * 1.6 * f * A}deg) rotateX(${(i % 3 - 1) * 2 * f * A}deg) translate(${(i % 2 ? k : -k) * f}px, ${(i % 3 - 1) * k * f}px)` };
      }).concat([{ transform: "none" }]), { duration: 420, easing: "linear" });
    } else if (im.kind === "zoom" && st) {
      st.animate([{ transform: "scale(1)" }, { transform: `scale(${1 + 0.2 * A})`, offset: 0.15 }, { transform: "scale(1)" }],
        { duration: 340, easing: "ease-out", composite: "add" });
    } else if ((im.kind === "flash" || im.kind === "flash_rouge") && v) {
      v.style.background = im.kind === "flash" ? "#fff" : "radial-gradient(circle, transparent 35%, rgba(230,30,40,.9))";
      v.animate([{ opacity: Math.min(0.85, 0.7 * A) }, { opacity: 0 }], { duration: im.kind === "flash" ? 140 : 220, easing: "ease-out" });
    } else if ((im.kind === "etincelles" || im.kind === "onde") && fxLayer.current) {
      const layer = fxLayer.current;
      if (im.kind === "onde") {
        const ring = document.createElement("div");
        ring.className = "fx-ring";
        ring.style.left = `${im.x * 100}%`;
        ring.style.top = `${im.y * 100}%`;
        layer.appendChild(ring);
        ring.animate([{ transform: "translate(-50%,-50%) rotateX(62deg) scale(.15)", opacity: 1 },
          { transform: "translate(-50%,-50%) rotateX(62deg) scale(1.6)", opacity: 0 }], { duration: 450, easing: "ease-out" })
          .onfinish = () => ring.remove();
      } else {
        const n = Math.round(18 + 14 * A);
        for (let i = 0; i < n; i++) {
          const p = document.createElement("div");
          p.className = "fx-spark";
          p.style.left = `${im.x * 100}%`;
          p.style.top = `${im.y * 100}%`;
          layer.appendChild(p);
          const a = Math.random() * Math.PI * 2, r = (60 + Math.random() * 160) * A;
          p.animate([{ transform: "translate(-50%,-50%) scale(1)", opacity: 1 },
            { transform: `translate(calc(-50% + ${Math.cos(a) * r}px), calc(-50% + ${Math.sin(a) * r + 60}px)) scale(.3)`, opacity: 0 }],
            { duration: 300 + Math.random() * 300, easing: "cubic-bezier(.1,.7,.4,1)" }).onfinish = () => p.remove();
        }
      }
    }
  }

  function syncOverlay(t: number, play: boolean) {
    const el = vo.current;
    if (!el || !tl) return;
    const o = overlayAt(tl, t);
    // Écran partagé : le cadre principal passe sur une moitié (classe sur le cadre), le plan de coupe sur l'autre.
    const frame = el.parentElement;
    const split = o?.mode === "partage" ? (tl.format === "9:16" ? "split-v" : "split-h") : "";
    frame?.classList.toggle("split-h", split === "split-h");
    frame?.classList.toggle("split-v", split === "split-v");
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
    el.className = `ov on ov-${o.mode}${split ? ` ov-${split}` : ""}`;
    el.style.objectPosition = o.mode === "partage" ? `${(o.focus_x ?? 0.5) * 100}% 50%` : "";
    if (play && el.paused) el.play().catch(() => undefined);
    if (!play && !el.paused) el.pause();
  }

  // Pistes audio (voix off, sons) : un lecteur par son, calé sur la position de lecture.
  const audioEls = useRef<Record<string, HTMLAudioElement>>({});
  function syncAudio(t: number, play: boolean) {
    if (!tl || !project) return;
    const live = new Set<string>();
    for (const a of tl.audio || []) {
      live.add(a.id);
      const el = (audioEls.current[a.id] ||= Object.assign(new Audio(api.audioUrl(project.id, a.asset)), { preload: "auto" }));
      const into = t - a.start;
      const on = play && into >= 0 && into < a.src_out - a.src_in;
      if (!on) {
        if (!el.paused) el.pause();
        continue;
      }
      const fadeIn = a.fade_in > 0 ? Math.min(1, into / a.fade_in) : 1;
      const left = a.src_out - a.src_in - into;
      const fadeOut = a.fade_out > 0 ? Math.min(1, left / a.fade_out) : 1;
      el.volume = Math.max(0, Math.min(1, a.volume * mixGain(tl.mix, "tracks") * fadeIn * fadeOut));
      const want = a.src_in + into;
      if (el.paused) {
        el.currentTime = want;
        el.play().catch(() => undefined);
      } else if (Math.abs(el.currentTime - want) > 0.25) el.currentTime = want;
    }
    for (const [id, el] of Object.entries(audioEls.current)) if (!live.has(id)) { el.pause(); delete audioEls.current[id]; }
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
      syncAudio(playhead, false);
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
    if (t0 < 0.05) {
      animateTransition(el, tl.clips[cur.current]);
      animateEntry3d(el, tl.clips[cur.current]);
    }
    preloadNext();
    const mu = music.current;
    if (mu && tl.music.track) {
      if (mu.duration) mu.currentTime = (t0 + (tl.music.start || 0)) % mu.duration;
      mu.volume = Math.min(1, tl.music.volume * mixGain(tl.mix, "music") * 1.5);
      mu.play().catch(() => undefined);
    }
    const starts = clipStarts(tl);
    const sfx = [...(tl.sfx || [])].sort((x, y) => x.start - y.start);
    const impacts = [...(tl.impacts || [])].sort((x, y) => x.start - y.start);

    const tick = () => {
      const vs = vids();
      const el = vs[active.current];
      const c = tl.clips[cur.current];
      if (!c) return;
      const loc = srcToLocal(c, Math.max(0, el.currentTime - c.src_in));
      const t = starts[cur.current] + loc;
      const want = speedAt(c, loc) * useStore.getState().rate;
      if (Math.abs(el.playbackRate - want) > 0.01) el.playbackRate = want;
      // Fondus audio du plan, comme au rendu.
      const fi = c.fade_in || 0, fo = c.fade_out || 0;
      if (fi > 0 || fo > 0) {
        const cd = clipDur(c);
        el.volume = Math.max(0, Math.min(1, c.volume * mixGain(tl.mix, "rushes") * (fi > 0 ? Math.min(1, loc / fi) : 1) * (fo > 0 ? Math.min(1, (cd - loc) / fo) : 1)));
      }
      syncAudio(t, true);
      for (const s of sfx) if (s.start > reported.current && s.start <= t) playSfx(s.kind, s.volume * mixGain(tl.mix, "sfx"));
      for (const im of impacts) if (im.start > reported.current && im.start <= t) fireImpact(im);
      reported.current = t;
      // Boucle sur la zone d'entrée/sortie (Ctrl+L) : arrivé à la sortie, on repart de l'entrée
      // (le saut relance la lecture, comme un clic sur la timeline).
      const rg = useStore.getState().loopRange ? tl.range : null;
      if (rg && t >= rg.end - 1 / 60 && rg.end - rg.start > 0.3) {
        setPlayhead(rg.start);
        return;
      }
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
          !rampOf(n) && !rampOf(c) && !n.keyframes?.length && !c.keyframes?.length &&
          n.effects.join() === c.effects.join() && n.audio_effects.join() === c.audio_effects.join() &&
          JSON.stringify(n.color ?? null) === JSON.stringify(c.color ?? null);
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
          animateEntry3d(other, n);
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
  const g = geo.current && cc && geo.current.clip === cc.id ? geo.current : null;

  return (
    <div className="player">
      <div className="player-stage">
        <div className={`frame frame-${fmt.replace(":", "x")}`} style={{
          aspectRatio: aspect,
          ...(tl?.brand ? {
            "--brand-accent": tl.brand.accent, "--brand-primary": tl.brand.primary, "--brand-text": tl.brand.text,
            ...(tl.brand.font && tl.brand.font_installed ? { "--brand-font": `"${tl.brand.font}"` } : {}),
          } as React.CSSProperties : {}),
        }}>
          <div ref={stage} className={`fxwrap ${fxClasses(fxEff, look).join(" ")}`}>
            <video ref={v0} className={shown === 0 ? "on" : ""} playsInline preload="auto" />
            <video ref={v1} className={shown === 1 ? "on" : ""} playsInline preload="auto" />
          </div>
          <video ref={vo} className="ov" playsInline muted preload="auto" />
          {layers.vignette && <div className="layer vignette" />}
          {layers.grain && <div className="layer grain" />}
          {layers.letterbox && <div className="layer letterbox" />}
          <div ref={veil} className="layer veil" />
          <div ref={fxLayer} className="layer fx-layer" />
          {!tl?.clips.length && !live && (
            <div className="frame-empty">
              Importe tes vidéos puis clique sur <b>Monter automatiquement</b>
            </div>
          )}
          <LiveDirector />
          {g && cc && cc.masks?.filter((m) => {
            if (m.start == null || m.end == null) return true;
            const loc = srcToLocal(cc, Math.max(0, g.srcT - cc.src_in));
            return loc >= m.start && loc < m.end;
          }).map((m, k) => {
            const fc = m.follow_dx != null && project ? faceCx(facesCache[`${project.id}/${cc.media_id}`], g.srcT) : null;
            const x = fc != null ? Math.max(0, Math.min(1 - m.w, fc + (m.follow_dx ?? 0))) : m.x;
            return (
              <div key={k} className={`mask mask-${m.kind}`} style={regionToFrame(g, { ...m, x })}>
                {m.kind === "projecteur" && m.label ? <span className="spot-label">{m.label}</span> : null}
              </div>
            );
          })}
          {g && bubbles.filter((c) => c.region && c.media_id === g.mediaId).map((c) => (
            <div key={"r" + c.id} className={`region-box s-${c.status}`} style={regionToFrame(g, c.region!)} />
          ))}
          {g && pending && pending.media_id === g.mediaId && (
            <div className="region-box pending" style={regionToFrame(g, pending.region)} />
          )}
          {bubbles.map((c) => (
            <div key={c.id} className={`cm-bubble s-${c.status}`}><Icon name="message" size={13} strokeWidth={2.2} />{c.text}</div>
          ))}
          {picking && (
            <div className="pick-layer"
              onMouseDown={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                setDrag({ x0: e.clientX - r.left, y0: e.clientY - r.top, x1: e.clientX - r.left, y1: e.clientY - r.top });
              }}
              onMouseMove={(e) => {
                if (!drag) return;
                const r = e.currentTarget.getBoundingClientRect();
                setDrag({ ...drag, x1: e.clientX - r.left, y1: e.clientY - r.top });
              }}
              onMouseUp={() => {
                const gg = geo.current;
                if (!drag || !gg) return setDrag(null);
                let x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
                let w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
                if (w < 8 || h < 8) { // simple clic : zone par défaut autour du point
                  w = gg.fw * 0.22; h = gg.fh * 0.22; x = drag.x0 - w / 2; y = drag.y0 - h / 2;
                }
                const toSrc = (px: number, sz: number, off: number) => (px + off) / sz;
                const rx = Math.max(0, toSrc(x, gg.sw, gg.offX)), ry = Math.max(0, toSrc(y, gg.sh, gg.offY));
                useStore.getState().setPendingRegion({
                  media_id: gg.mediaId, src_t: +gg.srcT.toFixed(3),
                  region: { x: +rx.toFixed(4), y: +ry.toFixed(4), w: +Math.min(1 - rx, w / gg.sw).toFixed(4), h: +Math.min(1 - ry, h / gg.sh).toFixed(4) },
                });
                setDrag(null);
                document.getElementById("comment-box")?.focus();
              }}>
              {drag && <div className="region-box drawing" style={{ left: Math.min(drag.x0, drag.x1), top: Math.min(drag.y0, drag.y1), width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0) }} />}
              {!drag && <div className="pick-hint">Trace un rectangle (ou clique) sur l'élément à commenter · Échap pour annuler</div>}
            </div>
          )}
          {project && (tl?.images || []).filter((g) => playhead >= g.start && (g.duration == null || playhead < g.start + g.duration)).map((g) => (
            <img key={g.id} className={`img-layer anim-${g.anim}`} src={api.assetUrl(project.id, g.asset)} alt=""
              style={{ left: `${g.x * 100}%`, top: `${g.y * 100}%`, width: `${g.scale * 100}%`, opacity: g.opacity }} />
          ))}
          {texts.map((x) => (
            <div key={x.id} className={`ti ti-${x.style} pos-${x.position}`}>
              {x.style === "texte_3d" ? <span data-t={x.text}>{x.text}</span>
                : x.style === "titre" || x.style === "gros_texte" ? x.text.toUpperCase() : x.text}
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
        <button className="icon-btn" title="Début" aria-label="Début" onClick={() => setPlayhead(0)}><Icon name="skip-back" size={18} /></button>
        <button className="play-btn" title="Lecture / pause (Espace)" onClick={() => setPlaying(!playing)}
          disabled={!tl?.clips.length}>
          <Icon name={playing ? "pause" : "play"} size={16} />
        </button>
        <button className="icon-btn" title="Plan précédent (↑)" aria-label="Plan précédent" onClick={() => useStore.getState().jumpClip(-1)}><Icon name="chevron-up" size={18} /></button>
        <button className="icon-btn" title="Plan suivant (↓)" aria-label="Plan suivant" onClick={() => useStore.getState().jumpClip(1)}><Icon name="chevron-down" size={18} /></button>
        <span className="tc">
          {fmtTime(playhead, true)} <span className="muted">/ {fmtTime(total, true)}</span>
        </span>
        {rate !== 1 && <span className="rate-badge" title="Vitesse de lecture (L pour changer, K pour arrêter)">×{rate}</span>}
        {cc && (cc.speed !== 1 || cc.effects.length > 0 || look !== "aucun") && (
          <span className="fx-badge" title="Effets du plan courant">
            <Icon name="sparkles" size={13} />{[look !== "aucun" ? look : "", cc.speed !== 1 ? `×${cc.speed}` : "", ...cc.effects].filter(Boolean).join(" · ").replace(/_/g, " ")}
          </span>
        )}
      </div>
    </div>
  );
}
