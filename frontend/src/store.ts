import { create } from "zustand";
import { api, ApiError, CaptionLine, Clip, AudioItem, Comment, EffectsCatalog, Health, ImageItem, ImpactItem, Region, Job, OverlayItem, Project, Segment, SfxItem, TextItem, Timeline, Transcript, waitJob } from "./api";

const uid = () => "c_" + Math.random().toString(36).slice(2, 12);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const MIN_CLIP = 0.1;
const MIN_RANGE = 0.1;

// --- Vitesse et images clés (mêmes calculs que le rendu : backend/app/models.py) ---

/** [vitesse de début, vitesse de fin] quand le plan a une rampe de vitesse. */
export function rampOf(c: Clip): [number, number] | null {
  if (c.speed_end == null) return null;
  const k = (v: number) => Math.max(0.25, Math.min(4, v));
  const s0 = k(c.speed || 1), s1 = k(c.speed_end);
  return Math.abs(s1 - s0) < 1e-3 ? null : [s0, s1];
}

export const clipDur = (c: Clip) => {
  const r = rampOf(c);
  const sd = c.src_out - c.src_in;
  return r ? (2 * sd) / (r[0] + r[1]) : sd / Math.max(0.05, c.speed || 1);
};

/** Instant du plan (timeline) → secondes de rush depuis src_in. */
export function localToSrc(c: Clip, t: number): number {
  const r = rampOf(c);
  if (!r) return t * Math.max(0.05, c.speed || 1);
  return r[0] * t + ((r[1] - r[0]) * t * t) / (2 * clipDur(c));
}

/** Secondes de rush depuis src_in → instant du plan sur la timeline. */
export function srcToLocal(c: Clip, tau: number): number {
  const r = rampOf(c);
  if (!r) return tau / Math.max(0.05, c.speed || 1);
  const a = (r[1] - r[0]) / (2 * clipDur(c));
  return (-r[0] + Math.sqrt(Math.max(0, r[0] * r[0] + 4 * a * tau))) / (2 * a);
}

export function speedAt(c: Clip, t: number): number {
  const r = rampOf(c);
  if (!r) return c.speed || 1;
  return r[0] + (r[1] - r[0]) * Math.max(0, Math.min(1, t / Math.max(1e-6, clipDur(c))));
}

/** Zoom et cadrage animés du plan à l'instant t (null sans images clés). */
export function kfAt(c: Clip, t: number): { zoom: number; x: number; y: number } | null {
  const ks = [...(c.keyframes || [])].sort((a, b) => a.t - b.t);
  if (!ks.length) return null;
  if (t <= ks[0].t) return ks[0];
  for (let i = 0; i + 1 < ks.length; i++) {
    const a = ks[i], b = ks[i + 1];
    if (t < b.t) {
      let u = Math.max(0, Math.min(1, (t - a.t) / Math.max(1e-3, b.t - a.t)));
      if (b.ease === "doux") u = u * u * (3 - 2 * u);
      return { zoom: a.zoom + (b.zoom - a.zoom) * u, x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u };
    }
  }
  return ks[ks.length - 1];
}
const rid = (p: string) => p + Math.random().toString(36).slice(2, 12);

/** Coupe le plan à l'instant local `tc` (comme la touche S) : rampe et images clés continuent après la coupe.
 *  Renvoie [avant, après] ; `après` garde l'identifiant du plan, à remplacer si les deux sont conservés. */
export function cutClip(c: Clip, tc: number): [Clip, Clip] {
  const cut = c.src_in + localToSrc(c, tc);
  const a: Clip = { ...clone(c), src_out: cut, fade_out: 0 };
  const b: Clip = { ...clone(c), src_in: cut, transition: "aucune", fade_in: 0 };
  if (rampOf(c)) {
    const mid = speedAt(c, tc);
    a.speed_end = mid;
    b.speed = mid;
  }
  if (c.keyframes?.length) {
    const here = kfAt(c, tc)!;
    const k = { ...here, t: 0, ease: "doux" as const };
    b.keyframes = [k, ...c.keyframes.filter((x) => x.t > tc).map((x) => ({ ...x, t: +(x.t - tc).toFixed(3) }))];
    a.keyframes = [...c.keyframes.filter((x) => x.t < tc), { ...k, t: +tc.toFixed(3) }];
  }
  return [a, b];
}

export type Sel = { kind: "clip" | "text" | "sfx" | "overlay" | "comment" | "impact" | "image" | "audio" | "marker"; id: string };
export type ItemKind = "text" | "sfx" | "overlay" | "comment" | "impact" | "image" | "audio" | "marker";

/** Position sur la timeline d'un commentaire ancré sur un rush (null si le passage n'est plus monté). */
export function commentTime(tl: Timeline, c: Comment): number | null {
  if (!c.media_id) return null;
  let t = 0;
  for (const clip of tl.clips) {
    if (clip.media_id === c.media_id && c.src_t >= clip.src_in - 1e-3 && c.src_t < clip.src_out + 1e-3)
      return t + srcToLocal(clip, c.src_t - clip.src_in);
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

/** Instants « aimantés » de la timeline : début, fin et frontières de chaque plan. */
export function snapPoints(tl: Timeline): number[] {
  const starts = clipStarts(tl);
  return [0, ...starts, timelineDuration(tl), ...(tl.markers || []).map((m) => m.t)];
}

/** Rapproche `t` du point le plus proche s'il est à moins de `px` pixels (au zoom `pps`). */
export function snapTo(t: number, points: number[], pps: number, px = 8): { t: number; snapped: number | null } {
  let best: number | null = null;
  let dist = px / pps;
  for (const p of points) {
    const d = Math.abs(p - t);
    if (d < dist) { dist = d; best = p; }
  }
  return best == null ? { t, snapped: null } : { t: best, snapped: best };
}

export type LeftTab = "rushes" | "ia" | "versions" | "modeles" | "marque";
export type RightTab = "consigne" | "montage" | "plan" | "habillage" | "audio" | "qualite" | "export" | "texte" | "recherche";
export const RIGHT_TAB_ORDER: RightTab[] = ["consigne", "montage", "plan", "habillage", "audio", "qualite", "export", "texte", "recherche"];

/** Espace de travail : largeur des panneaux, hauteur de la timeline, panneaux repliés. Mémorisé dans le navigateur. */
export interface Layout { left: number; right: number; bottom: number; leftOpen: boolean; rightOpen: boolean }
export const DEFAULT_LAYOUT: Layout = { left: 360, right: 340, bottom: 268, leftOpen: true, rightOpen: true };
export const LAYOUT_LIMITS = { left: [240, 640], right: [260, 640], bottom: [150, 0.7] } as const;  // bottom : fraction de la fenêtre
const LAYOUT_KEY = "monteur.layout";
function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const l = { ...DEFAULT_LAYOUT, ...JSON.parse(raw) } as Layout;
    return clampLayout(l);
  } catch { return DEFAULT_LAYOUT; }
}
export function clampLayout(l: Layout): Layout {
  const k = (v: number, lo: number, hi: number) => Math.round(Math.max(lo, Math.min(hi, v)));
  const maxBottom = typeof window !== "undefined" ? window.innerHeight * LAYOUT_LIMITS.bottom[1] : 800;
  return { ...l, left: k(l.left, LAYOUT_LIMITS.left[0], LAYOUT_LIMITS.left[1]), right: k(l.right, LAYOUT_LIMITS.right[0], LAYOUT_LIMITS.right[1]),
    bottom: k(l.bottom, LAYOUT_LIMITS.bottom[0], Math.max(LAYOUT_LIMITS.bottom[0], maxBottom)) };
}

export function clipAt(tl: Timeline, t: number): { index: number; start: number } | null {
  const starts = clipStarts(tl);
  for (let i = 0; i < tl.clips.length; i++) {
    const d = clipDur(tl.clips[i]);
    if (t >= starts[i] - 1e-6 && t < starts[i] + d - 1e-6) return { index: i, start: starts[i] };
  }
  return null;
}

/** Tâche suivie côté interface : le libellé et la façon de la relancer ne viennent pas du serveur. */
export interface TrackedJob extends Job {
  label?: string;
  /** Relance la même tâche (bouton « Réessayer »). */
  retry?: () => Promise<Job | null>;
}

export interface Toast {
  id: number;
  text: string;
  level: "info" | "ok" | "erreur";
  action?: { label: string; run: () => void };
}

export interface RunJobOpts {
  /** Recharge le projet à la fin (par défaut : oui). */
  refresh?: boolean;
  /** Le résultat remplace la timeline locale (tâches IA). */
  timeline?: boolean;
  onDone?: (j: Job) => void | Promise<void>;
}

/** Libellé français de chaque type de tâche. */
export const KIND_LABELS: Record<string, string> = {
  montage: "Montage automatique", export: "Export", doublage: "Doublage", traduction: "Traduction des sous-titres",
  controle: "Contrôle qualité", relecture: "Relecture", shorts: "Shorts", publication: "Kit de publication",
  voix_ia: "Voix de synthèse", directions: "3 directions", projecteur: "Projecteur voix", commentaires: "Commentaires",
  consigne: "Consigne", telechargement: "Téléchargement", recherche: "Recherche web", demo: "Démo sans vidéo",
};

/** Tâches dont la fin ouvre le pitch du réalisateur. */
const PITCH_KINDS = new Set(["montage", "demo"]);
export const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

const isActive = (j: Job) => j.status === "en_attente" || j.status === "en_cours";

/** Tâche en cours la plus récente (null s'il n'y en a pas). */
export function activeJob(s: { jobs: Record<string, Job> }): Job | null {
  let best: Job | null = null;
  for (const j of Object.values(s.jobs)) if (isActive(j) && (!best || j.created_at > best.created_at)) best = j;
  return best;
}

/** Tâche la plus récente d'un type : celle en cours si elle existe, sinon la dernière terminée. */
export function jobOf(s: { jobs: Record<string, Job> }, kind: string): Job | null {
  let best: Job | null = null;
  for (const j of Object.values(s.jobs)) {
    if (j.kind !== kind) continue;
    if (!best || (isActive(j) && !isActive(best)) || (isActive(j) === isActive(best) && j.created_at > best.created_at)) best = j;
  }
  return best;
}

/** Vrai si une tâche de l'un de ces types tourne (sans type : n'importe laquelle). */
export const isBusy = (s: { jobs: Record<string, Job> }, ...kinds: string[]): boolean =>
  Object.values(s.jobs).some((j) => isActive(j) && (!kinds.length || kinds.includes(j.kind)));

const ERROR_WORDS = /impossible|échoué|échec|refusé|erreur/i;
let toastSeq = 0;
/** Tâches dont un appel suit déjà l'avancement (évite de les suivre deux fois après un load). */
const following = new Set<string>();

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
  /** Toutes les tâches du projet (en cours et récentes), par identifiant. */
  jobs: Record<string, TrackedJob>;
  /** Dérivée de `jobs` : la tâche en cours la plus récente (compatibilité). */
  job: Job | null;
  caps: Health | null;
  toasts: Toast[];
  pxPerSec: number;
  /** Largeur visible de la timeline (px), pour « zoom ajusté ». */
  timelineWidth: number;
  /** Vitesse de lecture (J/K/L) : 1, 2 ou 4. */
  rate: number;
  /** Onglets de l'éditeur ; `leftTab` null = choix automatique (rushes si le projet est vide). */
  leftTab: LeftTab | null;
  rightTab: RightTab;
  helpOpen: boolean;
  paletteOpen: boolean;
  /** Pitch du réalisateur (fin de montage) : ouvert, et tâche dont la fin attend le rechargement du projet. */
  pitchOpen: boolean;
  pitchPending: string | null;
  exportOpen: boolean;
  layout: Layout;
  setLayout: (patch: Partial<Layout>) => void;
  resetLayout: () => void;
  catalog: EffectsCatalog | null;
  item: Sel | null;
  picking: boolean;
  pendingRegion: { media_id: string; src_t: number; region: Region } | null;
  setPicking: (p: boolean) => void;
  setPendingRegion: (r: State["pendingRegion"]) => void;

  load: (id: string) => Promise<void>;
  /** Recharge le projet. Garde les retouches locales non enregistrées, sauf si `timeline` (résultat d'une tâche IA). */
  refreshProject: (opts?: { timeline?: boolean }) => Promise<void>;
  /** Enregistre tout de suite les retouches en attente. */
  saveNow: () => Promise<void>;
  refreshDerived: () => Promise<void>;
  setProject: (p: Project) => void;
  /** Ajoute ou met à jour une tâche (garde son libellé et son `retry`) et recalcule `job`. */
  putJob: (j: Job, extra?: { label?: string; retry?: TrackedJob["retry"] }) => void;
  /** Lance une tâche serveur, suit son avancement, recharge le projet et signale l'issue. Null si échec ou annulation. */
  runJob: (label: string, start: () => Promise<Job>, opts?: RunJobOpts) => Promise<Job | null>;
  /** Exécute `fn` ; une erreur devient un toast au lieu d'être avalée. */
  guard: <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;
  /** Signale une erreur (rien pour une annulation ; bouton « Ouvrir Réglages » si Claude manque). */
  fail: (label: string, e: unknown) => void;
  refreshCaps: () => Promise<void>;
  notify: (msg: string | null, opts?: { level?: Toast["level"]; action?: Toast["action"] }) => void;
  dismiss: (id: number) => void;
  /** Demande l'arrêt d'une tâche en cours. */
  cancelJob: (id: string) => Promise<void>;
  setPlayhead: (t: number) => void;
  setPlaying: (p: boolean) => void;
  select: (id: string | null) => void;
  setZoom: (pps: number) => void;
  /** Zoom pour que tout le montage tienne dans la largeur visible. */
  zoomFit: () => void;
  setRate: (r: number) => void;
  setLeftTab: (t: LeftTab | null) => void;
  setRightTab: (t: RightTab) => void;
  setHelp: (open: boolean) => void;
  setPalette: (open: boolean) => void;
  setPitch: (open: boolean) => void;
  setExportOpen: (open: boolean) => void;
  /** Curseur sur le plan précédent (-1) ou suivant (+1), qui devient sélectionné. */
  jumpClip: (dir: -1 | 1) => void;
  /** Copie du plan insérée juste après lui. */
  duplicateClip: (id: string) => void;
  /** Déplace un plan d'un cran vers la gauche ou la droite. */
  nudgeClip: (id: string, dir: -1 | 1) => void;
  /** Rogne l'entrée (« in ») ou la sortie (« out ») du plan au curseur. */
  trimToPlayhead: (id: string, side: "in" | "out") => void;
  /** Pose un repère au curseur (ou sélectionne celui qui s'y trouve déjà). */
  addMarker: () => void;
  /** Lecture en boucle de la zone d'entrée/sortie. */
  loopRange: boolean;
  setLoopRange: (on: boolean) => void;
  /** Entrée (« in ») ou sortie (« out ») de la zone au curseur ; l'autre borne s'ajuste si elle gêne. */
  markRange: (side: "in" | "out") => void;
  /** Zone = le plan sous le curseur (X). */
  rangeFromClip: () => void;
  setRange: (r: { start: number; end: number } | null) => void;
  /** Retire tout ce qui est dans la zone et referme le trou (suppression « ripple »). */
  deleteRange: () => void;

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
  /** Marque la timeline enregistrée, seulement si c'est encore celle qui a été envoyée. */
  markSaved: (sent?: Timeline) => void;
  selectItem: (s: Sel | null) => void;
  addText: (patch?: Partial<TextItem>) => void;
  addSfx: (kind: string) => void;
  addImpact: (kind: string) => void;
  addImage: (asset: string, watermark: boolean) => void;
  addAudio: (asset: string, duration: number, kind: AudioItem["kind"], start?: number, label?: string) => void;
  setTimeline: (p: Project) => void;
  addOverlay: (o: Omit<OverlayItem, "id">) => void;
  updateItem: (kind: ItemKind, id: string, patch: Record<string, unknown>) => void;
  deleteItem: (kind: ItemKind, id: string) => void;
  addComment: (text: string, general?: boolean) => boolean;
}

const listOf = (tl: Timeline, kind: ItemKind) =>
  (kind === "text" ? tl.texts : kind === "sfx" ? tl.sfx : kind === "comment" ? tl.comments
    : kind === "impact" ? (tl.impacts ??= []) : kind === "image" ? (tl.images ??= [])
    : kind === "audio" ? (tl.audio ??= []) : kind === "marker" ? (tl.markers ??= []) : tl.overlays) as Array<{ id: string }>;

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
  jobs: {},
  job: null,
  caps: null,
  toasts: [],
  pxPerSec: 40,
  timelineWidth: 0,
  rate: 1,
  leftTab: null,
  rightTab: "consigne",
  helpOpen: false,
  paletteOpen: false,
  pitchOpen: false,
  pitchPending: null,
  exportOpen: false,
  loopRange: false,
  layout: loadLayout(),
  setLayout: (patch) => {
    const layout = clampLayout({ ...get().layout, ...patch });
    set({ layout });
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* navigation privée : non mémorisé */ }
  },
  resetLayout: () => get().setLayout(DEFAULT_LAYOUT),
  catalog: null,
  item: null,
  picking: false,
  pendingRegion: null,
  setPicking: (p) => set({ picking: p, playing: p ? false : get().playing }),
  setPendingRegion: (r) => set({ pendingRegion: r, picking: false }),

  load: async (id) => {
    if (!get().catalog) api.effects().then((c) => set({ catalog: c })).catch(() => undefined);
    get().refreshCaps();
    const sameProject = get().project?.id === id;
    const p = await api.getProject(id);
    // Revenir des réglages recharge le même projet : on garde alors l'historique d'annulation.
    set({ project: p, timeline: p.timeline, dirty: false, playhead: 0, selected: null,
      ...(sameProject ? {} : { past: [], future: [], jobs: {}, job: null }) });
    const [active, history] = await Promise.all([
      api.activeJobs(id).catch(() => [] as Job[]),
      api.jobsHistory(id).catch(() => [] as Job[]),
    ]);
    // Les tâches déjà suivies ici gardent leur libellé et leur « Réessayer » ; le reste vient du serveur.
    const jobs: Record<string, TrackedJob> = {};
    for (const j of history) jobs[j.id] = j;
    for (const j of active) jobs[j.id] = j;
    for (const [jid, j] of Object.entries(get().jobs)) if (isActive(j) || !jobs[jid]) jobs[jid] = j;
    set({ jobs, job: activeJob({ jobs }), ...(get().pitchPending ? { pitchOpen: true, pitchPending: null } : {}) });
    await get().refreshDerived();
    // Tâches lancées avant un rechargement de la page : on reprend leur suivi jusqu'au bout.
    for (const j of active) {
      if (following.has(j.id)) continue;
      following.add(j.id);
      waitJob(j, (u) => { if (get().project?.id === id) get().putJob(u); })
        .then(() => { if (get().project?.id === id) return get().refreshProject({ timeline: true }); })
        .catch((e) => get().fail(kindLabel(j.kind), e))
        .finally(() => following.delete(j.id));
    }
  },
  refreshProject: async (opts) => {
    const p = get().project;
    if (!p) return;
    const before = get().timeline;
    const np = await api.getProject(p.id);
    const st = get();
    if (!opts?.timeline && st.timeline && (st.dirty || st.timeline !== before)) {
      set({ project: { ...np, timeline: st.timeline } });  // retouches en cours : on ne les écrase pas
    } else {
      set({ project: np, timeline: np.timeline, dirty: false });
    }
    if (get().pitchPending) set({ pitchOpen: true, pitchPending: null });
    await get().refreshDerived();
  },
  saveNow: async () => {
    const { project, timeline, dirty } = get();
    if (!project || !timeline || !dirty) return;
    await api.saveTimeline(project.id, timeline);
    get().markSaved(timeline);
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
  putJob: (j, extra) => {
    const prev = get().jobs[j.id];
    const jobs = { ...get().jobs, [j.id]: { ...prev, ...j, ...extra } };
    // Un montage suivi ici qui vient de finir : le pitch s'ouvrira dès que le projet sera rechargé.
    const pitch = j.status === "termine" && !!prev && isActive(prev) && PITCH_KINDS.has(j.kind);
    set({ jobs, job: activeJob({ jobs }), ...(pitch ? { pitchPending: j.id } : {}) });
  },
  runJob: async (label, start, opts) => {
    const pid = get().project?.id;
    if (!pid) return null;
    const same = () => get().project?.id === pid;
    const retry = () => get().runJob(label, start, opts);
    try {
      await get().saveNow();
      const first = await start();
      following.add(first.id);
      let done: Job;
      try {
        done = await waitJob(first, (j) => { if (same()) get().putJob(j, { label, retry }); });
      } finally {
        following.delete(first.id);
      }
      if (same()) {
        if (opts?.refresh !== false) await get().refreshProject({ timeline: opts?.timeline });
        await opts?.onDone?.(done);
      }
      return done;
    } catch (e) {
      if ((e as ApiError).code === "cancelled") get().notify(`Annulée : ${label}`);
      else get().fail(label, e);
      return null;
    }
  },
  guard: async (label, fn) => {
    try {
      return await fn();
    } catch (e) {
      get().fail(label, e);
      return undefined;
    }
  },
  fail: (label, e) => {
    const err = e as ApiError;
    if (err?.code === "cancelled") return;
    const text = `${label} : ${err?.message ?? String(e)}`;
    const action = err?.code === "claude_required"
      ? { label: "Ouvrir Réglages", run: () => { location.hash = "#/reglages"; } }
      : undefined;
    get().notify(text, { level: "erreur", action });
  },
  refreshCaps: () => api.health().then((caps) => set({ caps })).catch(() => undefined),
  notify: (msg, opts) => {
    if (msg === null) return set({ toasts: [] });
    const level = opts?.level ?? (ERROR_WORDS.test(msg) ? "erreur" : "info");
    // Un même message répété remplace le précédent au lieu de s'empiler.
    const rest = get().toasts.filter((t) => t.text !== msg).slice(-4);
    set({ toasts: [...rest, { id: ++toastSeq, text: msg, level, action: opts?.action }] });
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  cancelJob: async (id) => {
    await get().guard("Annulation impossible", async () => { get().putJob(await api.cancelJob(id)); });
  },
  setPlayhead: (t) => {
    const tl = get().timeline;
    const max = tl ? timelineDuration(tl) : 0;
    set({ playhead: Math.max(0, Math.min(t, max)) });
  },
  setPlaying: (p) => set({ playing: p }),
  select: (id) => set({ selected: id, item: id ? { kind: "clip", id } : null }),
  setZoom: (pps) => set({ pxPerSec: Math.max(4, Math.min(300, pps)) }),
  zoomFit: () => {
    const { timeline, timelineWidth } = get();
    const total = timeline ? timelineDuration(timeline) : 0;
    if (!total || !timelineWidth) return;
    get().setZoom((timelineWidth - 48) / total);
  },
  setRate: (r) => set({ rate: [1, 2, 4].includes(r) ? r : 1 }),
  setLeftTab: (t) => set({ leftTab: t }),
  setRightTab: (t) => set({ rightTab: t }),
  setHelp: (open) => set({ helpOpen: open, paletteOpen: open ? false : get().paletteOpen }),
  setPalette: (open) => set({ paletteOpen: open, helpOpen: open ? false : get().helpOpen }),
  setPitch: (open) => set({ pitchOpen: open, pitchPending: null }),
  setExportOpen: (open) => {
    // L'export est un onglet du panneau droit : « ouvrir » l'affiche (et déplie le panneau s'il est replié).
    set({ exportOpen: open });
    if (open) { set({ rightTab: "export" }); if (!get().layout.rightOpen) get().setLayout({ rightOpen: true }); }
  },
  jumpClip: (dir) => {
    const { timeline, playhead } = get();
    if (!timeline?.clips.length) return;
    const starts = clipStarts(timeline);
    const eps = 0.02;
    let idx: number;
    if (dir > 0) {
      idx = starts.findIndex((s) => s > playhead + eps);
      if (idx < 0) { get().setPlayhead(timelineDuration(timeline)); return; }
    } else {
      idx = starts.length - 1;
      while (idx > 0 && starts[idx] >= playhead - eps) idx--;
    }
    set({ playhead: starts[idx], selected: timeline.clips[idx].id, item: { kind: "clip", id: timeline.clips[idx].id } });
  },
  duplicateClip: (id) => {
    let nid = "";
    get().edit((tl) => {
      const i = tl.clips.findIndex((x) => x.id === id);
      if (i < 0) return;
      nid = uid();
      tl.clips.splice(i + 1, 0, { ...clone(tl.clips[i]), id: nid, reason: "Copie. " + tl.clips[i].reason });
    });
    if (nid) set({ selected: nid, item: { kind: "clip", id: nid } });
  },
  nudgeClip: (id, dir) => {
    const tl = get().timeline;
    if (!tl) return;
    const i = tl.clips.findIndex((x) => x.id === id);
    if (i < 0 || i + dir < 0 || i + dir >= tl.clips.length) return;
    get().moveClip(i, dir > 0 ? i + 2 : i - 1);
    const starts = clipStarts(get().timeline!);
    set({ playhead: starts[i + dir] });
  },
  trimToPlayhead: (id, side) => {
    const { timeline, playhead } = get();
    if (!timeline) return;
    const i = timeline.clips.findIndex((x) => x.id === id);
    if (i < 0) return;
    const c = timeline.clips[i];
    const start = clipStarts(timeline)[i];
    const loc = playhead - start;
    if (loc <= 0 || loc >= clipDur(c)) return;  // le curseur doit être dans le plan
    const src = c.src_in + localToSrc(c, loc);
    get().trimClip(id, side, src);
    if (side === "in") set({ playhead: start });
  },
  addMarker: () => {
    const { timeline, playhead } = get();
    if (!timeline) return;
    const here = (timeline.markers || []).find((m) => Math.abs(m.t - playhead) < 0.15);
    if (here) { set({ item: { kind: "marker", id: here.id }, selected: null }); return; }
    const id = rid("k_");
    get().edit((tl) => { (tl.markers ??= []).push({ id, t: +playhead.toFixed(3), label: "", color: "corail", chapter: true }); });
    set({ item: { kind: "marker", id }, selected: null });
  },
  setLoopRange: (on) => set({ loopRange: on }),
  setRange: (r) => get().edit((tl) => { tl.range = r; }),
  markRange: (side) => {
    const { timeline, playhead } = get();
    if (!timeline) return;
    const total = timelineDuration(timeline);
    const t = +Math.max(0, Math.min(playhead, total)).toFixed(3);
    const r = timeline.range;
    let start = r?.start ?? 0, end = r?.end ?? total;
    if (side === "in") { start = t; if (end <= start + MIN_RANGE) end = total; }
    else { end = t; if (start >= end - MIN_RANGE) start = 0; }
    if (end - start < MIN_RANGE) return;
    get().setRange({ start, end });
  },
  rangeFromClip: () => {
    const { timeline, playhead } = get();
    if (!timeline) return;
    const hit = clipAt(timeline, Math.min(playhead, Math.max(0, timelineDuration(timeline) - 1e-3)));
    if (!hit) return;
    const end = hit.start + clipDur(timeline.clips[hit.index]);
    get().setRange({ start: +hit.start.toFixed(3), end: +end.toFixed(3) });
  },
  deleteRange: () => {
    const { timeline } = get();
    const r = timeline?.range;
    if (!timeline || !r) return;
    const { start: a, end: b } = r;
    const span = b - a;
    get().edit((tl) => {
      // Plans : on garde ce qui est avant a et après b (coupe comme la touche S, rampes et images clés comprises).
      const starts = clipStarts(tl);
      const out: Clip[] = [];
      tl.clips.forEach((c, i) => {
        const c0 = starts[i], c1 = c0 + clipDur(c);
        if (c1 <= a + 1e-6 || c0 >= b - 1e-6) { out.push(c); return; }
        if (a > c0 + 1e-6) { const [head] = cutClip(c, a - c0); if (clipDur(head) >= MIN_CLIP) out.push(head); }
        if (b < c1 - 1e-6) { const [, tail] = cutClip(c, b - c0); if (clipDur(tail) >= MIN_CLIP) out.push({ ...tail, id: uid() }); }
      });
      tl.clips = out;
      const shift = <T extends { start: number }>(items: T[] | undefined, durOf?: (x: T) => number | null): T[] =>
        (items || []).filter((it) => {
          if (it.start >= b - 1e-6) { it.start = +(it.start - span).toFixed(3); return true; }
          if (it.start >= a - 1e-6) return false;  // commence dans la zone
          const d = durOf?.(it);
          if (d != null && it.start + d > a) {  // à cheval sur l'entrée : on le raccourcit
            const keep = a - it.start;
            if ("duration" in it && (it as { duration: number | null }).duration != null) (it as { duration: number }).duration = +keep.toFixed(3);
            else if ("src_out" in it) (it as unknown as { src_out: number; src_in: number }).src_out = +((it as unknown as { src_in: number }).src_in + keep).toFixed(3);
          }
          return true;
        });
      tl.texts = shift(tl.texts, (x) => x.duration);
      tl.sfx = shift(tl.sfx);
      tl.impacts = shift(tl.impacts);
      tl.images = shift(tl.images, (g) => g.duration);
      tl.audio = shift(tl.audio, (x) => x.src_out - x.src_in);
      tl.overlays = shift(tl.overlays, (o) => o.src_out - o.src_in);
      tl.markers = (tl.markers || []).filter((k) => k.t < a - 1e-6 || k.t >= b - 1e-6).map((k) => (k.t >= b - 1e-6 ? { ...k, t: +(k.t - span).toFixed(3) } : k));
      tl.range = null;
    });
    set({ playhead: a, selected: null, item: null });
  },

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
    const cut = c.src_in + localToSrc(c, playhead - hit.start);
    if (cut - c.src_in < MIN_CLIP || c.src_out - cut < MIN_CLIP) return;
    const tc = playhead - hit.start;
    get().edit((tl) => {
      const [a, b] = cutClip(tl.clips[hit.index], tc);  // rampe et images clés continuent de l'autre côté
      a.fade_out = tl.clips[hit.index].fade_out;  // une coupe garde les fondus du plan d'origine
      b.fade_in = tl.clips[hit.index].fade_in;
      tl.clips.splice(hit.index, 1, a, { ...b, id: uid() });
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
  markSaved: (sent) => { if (!sent || get().timeline === sent) set({ dirty: false }); },
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
  addImage: (asset, watermark) => {
    const id = rid("g_");
    const start = watermark ? 0 : get().playhead;
    get().edit((tl) => {
      tl.images = [...(tl.images || []), {
        id, asset, start, duration: watermark ? null : 3, x: watermark ? 0.9 : 0.5, y: watermark ? 0.1 : 0.5,
        scale: watermark ? 0.14 : 0.3, opacity: watermark ? 0.85 : 1, anim: watermark ? "fondu" : "pop",
      } as ImageItem];
    });
    get().selectItem({ kind: "image", id });
  },
  addAudio: (asset, duration, kind, start, label) => {
    const id = rid("a_");
    const at = start ?? get().playhead;
    get().edit((tl) => {
      const items = (tl.audio ??= []);
      // Première piste libre à cet endroit (4 pistes audio).
      const busy = (lane: number) => items.some((a) => a.lane === lane && at < a.start + (a.src_out - a.src_in) && a.start < at + duration);
      const lane = [0, 1, 2, 3].find((l) => !busy(l)) ?? 0;
      items.push({ id, asset, start: +at.toFixed(3), src_in: 0, src_out: +duration.toFixed(3), lane, kind,
        volume: kind === "voix" ? 1 : 0.7, fade_in: 0.05, fade_out: 0.15, label: label || "" });
    });
    get().selectItem({ kind: "audio", id });
  },
  setTimeline: (p) => {
    set({ project: p, timeline: p.timeline, past: [], future: [], dirty: false, item: null, selected: null, playhead: 0, captions: [] });
    get().refreshDerived();  // sous-titres de la nouvelle timeline
  },
  addImpact: (kind) => {
    const id = rid("i_");
    const start = get().playhead;
    get().edit((tl) => {
      tl.impacts = [...(tl.impacts || []), { id, start, kind, intensity: 1, x: 0.5, y: 0.5, reason: "" } as ImpactItem];
    });
    get().selectItem({ kind: "impact", id });
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
        src_t: pr ? pr.src_t : c && hit ? +(c.src_in + localToSrc(c, playhead - hit.start)).toFixed(3) : 0,
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
      else if (kind === "impact") tl.impacts = (tl.impacts || []).filter((x) => x.id !== id);
      else if (kind === "image") tl.images = (tl.images || []).filter((x) => x.id !== id);
      else if (kind === "audio") tl.audio = (tl.audio || []).filter((x) => x.id !== id);
      else if (kind === "marker") tl.markers = (tl.markers || []).filter((x) => x.id !== id);
      else tl.overlays = tl.overlays.filter((x) => x.id !== id);
    });
    set({ item: null });
  },
}));
