import { create } from "zustand";
import { api, CaptionLine, Clip, Comment, EffectsCatalog, Region, Job, OverlayItem, Project, Segment, SfxItem, TextItem, Timeline, Transcript } from "./api";

const uid = () => "c_" + Math.random().toString(36).slice(2, 12);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const MIN_CLIP = 0.1;

export const clipDur = (c: Clip) => (c.src_out - c.src_in) / Math.max(0.05, c.speed || 1);
const rid = (p: string) => p + Math.random().toString(36).slice(2, 12);

export type Sel = { kind: "clip" | "text" | "sfx" | "overlay" | "comment"; id: string };
type ItemKind = "text" | "sfx" | "overlay" | "comment";

/** Position sur la timeline d'un commentaire ancré sur un rush (null si le passage n'est plus monté). */
export function commentTime(tl: Timeline, c: Comment): number | null {
  if (!c.media_id) return null;
  let t = 0;
  for (const clip of tl.clips) {
    if (clip.media_id === c.media_id && c.src_t >= clip.src_in - 1e-3 && c.src_t < clip.src_out + 1e-3)
      return t + (c.src_t - clip.src_in) / Math.max(0.05, clip.speed || 1);
    t += clipDur(clip);
  }
  return null;
}

export function clipStarts(tl: Timeline): number[] {
  const out: number[] = [];
  let t = 0;
  for (const c of tl.clips) {
    out.push(t);
    t += clipDur(c);
  }
  return out;
}

export function timelineDuration(tl: Timeline): number {
  return tl.clips.reduce((a, c) => a + clipDur(c), 0);
}

export function clipAt(tl: Timeline, t: number): { index: number; start: number } | null {
  const starts = clipStarts(tl);
  for (let i = 0; i < tl.clips.length; i++) {
    const d = clipDur(tl.clips[i]);
    if (t >= starts[i] - 1e-6 && t < starts[i] + d - 1e-6) return { index: i, start: starts[i] };
  }
  return null;
}

interface State {
  project: Project | null;
  timeline: Timeline | null;
  past: Timeline[];
  future: Timeline[];
  dirty: boolean;
  selected: string | null;
  playhead: number;
  playing: boolean;
  captions: CaptionLine[];
  transcripts: Record<string, Transcript>;
  segments: Segment[];
  job: Job | null;
  toast: string | null;
  pxPerSec: number;
  catalog: EffectsCatalog | null;
  item: Sel | null;
  picking: boolean;
  pendingRegion: { media_id: string; src_t: number; region: Region } | null;
  setPicking: (p: boolean) => void;
  setPendingRegion: (r: State["pendingRegion"]) => void;

  load: (id: string) => Promise<void>;
  refreshProject: () => Promise<void>;
  refreshDerived: () => Promise<void>;
  setProject: (p: Project) => void;
  setJob: (j: Job | null) => void;
  notify: (msg: string | null) => void;
  setPlayhead: (t: number) => void;
  setPlaying: (p: boolean) => void;
  select: (id: string | null) => void;
  setZoom: (pps: number) => void;

  edit: (fn: (tl: Timeline) => void) => void;
  undo: () => void;
  redo: () => void;
  moveClip: (from: number, to: number) => void;
  trimClip: (id: string, side: "in" | "out", value: number) => void;
  updateClip: (id: string, patch: Partial<Clip>) => void;
  deleteClip: (id: string) => void;
  splitAtPlayhead: () => void;
  restoreRejected: (clip: Clip) => void;
  cutSourceRange: (mediaId: string, start: number, end: number) => void;
  setTimelineProps: (patch: Partial<Timeline>) => void;
  markSaved: () => void;
  selectItem: (s: Sel | null) => void;
  addText: (patch?: Partial<TextItem>) => void;
  addSfx: (kind: string) => void;
  addOverlay: (o: Omit<OverlayItem, "id">) => void;
  updateItem: (kind: ItemKind, id: string, patch: Record<string, unknown>) => void;
  deleteItem: (kind: ItemKind, id: string) => void;
  addComment: (text: string, general?: boolean) => boolean;
}

const listOf = (tl: Timeline, kind: ItemKind) =>
  (kind === "text" ? tl.texts : kind === "sfx" ? tl.sfx : kind === "comment" ? tl.comments : tl.overlays) as Array<{ id: string }>;

export const useStore = create<State>((set, get) => ({
  project: null,
  timeline: null,
  past: [],
  future: [],
  dirty: false,
  selected: null,
  playhead: 0,
  playing: false,
  captions: [],
  transcripts: {},
  segments: [],
  job: null,
  toast: null,
  pxPerSec: 40,
  catalog: null,
  item: null,
  picking: false,
  pendingRegion: null,
  setPicking: (p) => set({ picking: p, playing: p ? false : get().playing }),
  setPendingRegion: (r) => set({ pendingRegion: r, picking: false }),

  load: async (id) => {
    if (!get().catalog) api.effects().then((c) => set({ catalog: c })).catch(() => undefined);
    const p = await api.getProject(id);
    set({ project: p, timeline: p.timeline, past: [], future: [], dirty: false, playhead: 0, selected: null });
    const jobs = await api.activeJobs(id).catch(() => []);
    if (jobs.length) set({ job: jobs[0] });
    await get().refreshDerived();
  },
  refreshProject: async () => {
    const p = get().project;
    if (!p) return;
    const np = await api.getProject(p.id);
    set({ project: np, timeline: np.timeline, dirty: false });
    await get().refreshDerived();
  },
  refreshDerived: async () => {
    const p = get().project;
    if (!p) return;
    const [captions, transcripts, segments] = await Promise.all([
      api.captions(p.id).catch(() => []),
      api.transcripts(p.id).catch(() => ({})),
      api.segments(p.id).catch(() => []),
    ]);
    set({ captions, transcripts, segments });
  },
  setProject: (p) => set({ project: p }),
  setJob: (j) => set({ job: j }),
  notify: (msg) => set({ toast: msg }),
  setPlayhead: (t) => {
    const tl = get().timeline;
    const max = tl ? timelineDuration(tl) : 0;
    set({ playhead: Math.max(0, Math.min(t, max)) });
  },
  setPlaying: (p) => set({ playing: p }),
  select: (id) => set({ selected: id, item: id ? { kind: "clip", id } : null }),
  setZoom: (pps) => set({ pxPerSec: Math.max(4, Math.min(300, pps)) }),

  edit: (fn) => {
    const tl = get().timeline;
    if (!tl) return;
    const next = clone(tl);
    fn(next);
    set({ past: [...get().past.slice(-99), tl], future: [], timeline: next, dirty: true });
  },
  undo: () => {
    const { past, timeline, future } = get();
    if (!past.length || !timeline) return;
    set({ timeline: past[past.length - 1], past: past.slice(0, -1), future: [timeline, ...future], dirty: true });
  },
  redo: () => {
    const { past, timeline, future } = get();
    if (!future.length || !timeline) return;
    set({ timeline: future[0], future: future.slice(1), past: [...past, timeline], dirty: true });
  },
  moveClip: (from, to) =>
    get().edit((tl) => {
      const [c] = tl.clips.splice(from, 1);
      tl.clips.splice(to > from ? to - 1 : to, 0, c);
    }),
  trimClip: (id, side, value) => {
    const p = get().project;
    get().edit((tl) => {
      const c = tl.clips.find((x) => x.id === id);
      if (!c || !p) return;
      const m = p.media.find((x) => x.id === c.media_id);
      const maxT = m?.duration ?? c.src_out;
      if (side === "in") c.src_in = Math.max(0, Math.min(value, c.src_out - MIN_CLIP));
      else c.src_out = Math.min(maxT, Math.max(value, c.src_in + MIN_CLIP));
    });
  },
  updateClip: (id, patch) =>
    get().edit((tl) => {
      const c = tl.clips.find((x) => x.id === id);
      if (c) Object.assign(c, patch);
    }),
  deleteClip: (id) => {
    get().edit((tl) => {
      const i = tl.clips.findIndex((x) => x.id === id);
      if (i >= 0) {
        const [c] = tl.clips.splice(i, 1);
        tl.rejected.unshift({ ...c, reason: "Retiré à la main. " + c.reason });
      }
    });
    set({ selected: null });
  },
  splitAtPlayhead: () => {
    const { timeline, playhead } = get();
    if (!timeline) return;
    const hit = clipAt(timeline, playhead);
    if (!hit) return;
    const c = timeline.clips[hit.index];
    const cut = c.src_in + (playhead - hit.start) * (c.speed || 1);
    if (cut - c.src_in < MIN_CLIP || c.src_out - cut < MIN_CLIP) return;
    get().edit((tl) => {
      const a = tl.clips[hit.index];
      const b = { ...a, id: uid(), src_in: cut };
      a.src_out = cut;
      tl.clips.splice(hit.index + 1, 0, b);
    });
  },
  restoreRejected: (clip) => {
    const p = get().project;
    get().edit((tl) => {
      tl.rejected = tl.rejected.filter((r) => r.id !== clip.id);
      const order = p ? p.media.map((m) => m.id) : [];
      const key = (c: Clip) => order.indexOf(c.media_id) * 1e6 + c.src_in;
      const restored = { ...clip, id: uid(), reason: "Réintégré à la main. " + clip.reason };
      let i = tl.clips.findIndex((c) => key(c) > key(restored));
      if (i < 0) i = tl.clips.length;
      tl.clips.splice(i, 0, restored);
    });
  },
  cutSourceRange: (mediaId, start, end) =>
    get().edit((tl) => {
      const out: Clip[] = [];
      for (const c of tl.clips) {
        if (c.media_id !== mediaId || end <= c.src_in || start >= c.src_out) {
          out.push(c);
          continue;
        }
        if (start - c.src_in >= MIN_CLIP) out.push({ ...c, src_out: start });
        if (c.src_out - end >= MIN_CLIP) out.push({ ...c, id: uid(), src_in: end });
      }
      tl.clips = out;
    }),
  setTimelineProps: (patch) => get().edit((tl) => Object.assign(tl, patch)),
  markSaved: () => set({ dirty: false }),
  selectItem: (it) => set({ item: it, selected: it?.kind === "clip" ? it.id : null }),
  addText: (patch) => {
    const id = rid("t_");
    const start = get().playhead;
    get().edit((tl) => {
      tl.texts = [...(tl.texts || []), { id, start, duration: 2.5, text: "Ton texte", style: "titre", position: "centre", ...patch }];
    });
    get().selectItem({ kind: "text", id });
  },
  addSfx: (kind) => {
    const id = rid("s_");
    const start = get().playhead;
    get().edit((tl) => {
      tl.sfx = [...(tl.sfx || []), { id, start, kind, volume: 0.8 } as SfxItem];
    });
    get().selectItem({ kind: "sfx", id });
  },
  addComment: (text, general = false) => {
    const { timeline, playhead, pendingRegion } = get();
    if (!timeline || !text.trim()) return false;
    const hit = general ? null : clipAt(timeline, Math.min(playhead, Math.max(0, timelineDuration(timeline) - 1e-3)));
    const c = hit ? timeline.clips[hit.index] : null;
    const id = rid("n_");
    const pr = !general ? pendingRegion : null;
    get().edit((tl) => {
      tl.comments = [...(tl.comments || []), {
        id, text: text.trim(), status: "ouvert", reply: "", created_at: Date.now() / 1000,
        media_id: pr ? pr.media_id : c ? c.media_id : null,
        src_t: pr ? pr.src_t : c && hit ? +(c.src_in + (playhead - hit.start) * (c.speed || 1)).toFixed(3) : 0,
        region: pr ? pr.region : null,
      }];
    });
    set({ pendingRegion: null });
    return true;
  },
  addOverlay: (o) => {
    const id = rid("o_");
    get().edit((tl) => {
      tl.overlays = [...(tl.overlays || []), { ...o, id }];
    });
    get().selectItem({ kind: "overlay", id });
  },
  updateItem: (kind, id, patch) =>
    get().edit((tl) => {
      const it = listOf(tl, kind).find((x) => x.id === id);
      if (it) Object.assign(it, patch);
    }),
  deleteItem: (kind, id) => {
    get().edit((tl) => {
      if (kind === "text") tl.texts = tl.texts.filter((x) => x.id !== id);
      else if (kind === "sfx") tl.sfx = tl.sfx.filter((x) => x.id !== id);
      else if (kind === "comment") tl.comments = tl.comments.filter((x) => x.id !== id);
      else tl.overlays = tl.overlays.filter((x) => x.id !== id);
    });
    set({ item: null });
  },
}));
