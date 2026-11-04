// Types et appels vers le serveur du Monteur IA.

export type Style = "youtube" | "short" | "vlog" | "podcast" | "pvp";
export type Format = "16:9" | "9:16" | "1:1";
export type CaptionStyle = "karaoke" | "classique" | "aucun";

export interface Media {
  id: string;
  filename: string;
  path: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  has_video: boolean;
  proxy: string | null;
  analyzed: boolean;
}

export interface Clip {
  id: string;
  media_id: string;
  src_in: number;
  src_out: number;
  segment_id: string | null;
  score: number;
  reason: string;
  zoom: number;
  volume: number;
  speed: number;
  transition: string;
  transition_duration: number;
  effects: string[];
  audio_effects: string[];
  focus_x: number | null;
  focus_y: number | null;
  masks: Mask[];
  speed_end?: number | null;
  keyframes?: Keyframe[];
}

export interface Keyframe {
  t: number;
  zoom: number;
  x: number;
  y: number;
  ease: "lineaire" | "doux";
}

export interface AudioItem {
  id: string;
  asset: string;
  start: number;
  src_in: number;
  src_out: number;
  lane: number;
  kind: "voix" | "musique" | "son";
  volume: number;
  fade_in: number;
  fade_out: number;
  label: string;
  dub?: string | null;
  text?: string; // texte lu par la voix de synthèse
  words?: { start: number; end: number; text: string }[];
  check?: { ok: boolean | null; wer: number | null; heard: string; method: "whisper" | "energie" } | null;
}

export interface QualityIssue {
  check: string;
  level: "erreur" | "alerte" | "ok";
  message: string;
  t?: number | null;
  fix?: string | null;
  target?: string | null;
}

export interface QualityReport {
  score: number;
  issues: QualityIssue[];
  measured: Record<string, number | null>;
  rendered: boolean;
  fixed: string[];
  at: number;
}

export interface Review {
  score: number;
  verdict: string;
  remarks: { t?: number | null; problem: string; fix: string }[];
  reviewer: "claude" | "local";
  rounds: number;
  history: number[];
}

export type TextStyle = "titre" | "bandeau" | "legende" | "gros_texte" | "emoji" | "texte_3d";
export type TextPos = "haut" | "centre" | "bas";

export interface TextItem {
  id: string;
  start: number;
  duration: number;
  text: string;
  style: TextStyle;
  position: TextPos;
}

export interface SfxItem {
  id: string;
  start: number;
  kind: string;
  volume: number;
}

export type ImpactKind = "secousse" | "zoom" | "flash" | "flash_rouge" | "etincelles" | "onde";

export interface ImpactItem {
  id: string;
  start: number;
  kind: ImpactKind;
  intensity: number;
  x: number;
  y: number;
  reason: string;
}

export interface OverlayItem {
  id: string;
  media_id: string;
  src_in: number;
  src_out: number;
  start: number;
  mode: "plein" | "incrustation" | "partage";
  reason: string;
  focus_x?: number | null;
  chroma?: "aucun" | "vert" | "bleu";
  corner?: "haut_droite" | "haut_gauche" | "bas_droite" | "bas_gauche";
}

export interface ImageItem {
  id: string;
  asset: string;
  start: number;
  duration: number | null;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  anim: "aucune" | "fondu" | "pop";
}

export interface VersionInfo {
  id: string;
  name: string;
  score: number | null;
  note: string;
  created_at: number;
  duration: number;
}

export interface Versions {
  active: string;
  versions: VersionInfo[];
}

export interface ExportPreset {
  id: string;
  label: string;
  format?: Format;
  quality: string;
  fps?: number;
  container: string;
}

export interface StyleTemplate {
  id: string;
  name: string;
  style: Style;
  format: Format;
  captions: CaptionStyle;
  look: string;
  images: ImageItem[];
}

export interface Region {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Mask extends Region {
  kind: "flou" | "pixel" | "noir" | "cadre" | "projecteur";
  follow_dx: number | null;
  start?: number | null; // zone limitée dans le temps (secondes depuis le début du plan)
  end?: number | null;
  label?: string;
  reason?: string;
  push_in?: boolean;
}

export interface Comment {
  id: string;
  media_id: string | null;
  src_t: number;
  region: Region | null;
  text: string;
  status: "ouvert" | "applique" | "incompris";
  reply: string;
  created_at: number;
}

export interface Opt {
  id: string;
  label: string;
  group?: string;
}

export interface EffectsCatalog {
  video_effects: Opt[];
  looks: Opt[];
  transitions: Opt[];
  audio_effects: Opt[];
  sfx: Opt[];
  text_styles: Opt[];
  impacts: Opt[];
  speeds: number[];
}

export interface MusicSettings {
  track: string | null;
  volume: number;
  ducking: boolean;
  start?: number;
}

export interface Timeline {
  clips: Clip[];
  format: Format;
  captions: CaptionStyle;
  music: MusicSettings;
  title: string;
  summary: string;
  director: string;
  rejected: Clip[];
  look: string;
  texts: TextItem[];
  sfx: SfxItem[];
  overlays: OverlayItem[];
  impacts: ImpactItem[];
  images: ImageItem[];
  audio?: AudioItem[];
  caption_language?: string | null;
  lut?: string | null;
  lut_strength?: number;
  background?: string | null;
  dub_language?: string | null;
  quality?: QualityReport | null;
  review?: Review | null;
  voice_focus?: string[];
  reference_match?: { target_shot: number; result_shot: number; rounds: number; history: number[]; unmatched: string[]; summary: string } | null;
  reframe: boolean;
  comments: Comment[];
}

export interface EditSettings {
  style: Style;
  target_duration: number | null;
  format: Format;
  captions: CaptionStyle;
  remove_fillers: boolean;
  remove_silences: boolean;
  punch_zooms: boolean;
  music: boolean;
  auto_effects: boolean;
  instructions: string;
}

export interface Project {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  media: Media[];
  settings: EditSettings;
  timeline: Timeline;
  warnings: string[];
  exports: string[];
  publish_kit: PublishKit | null;
  active_version: string | null;
  directions?: Direction[];
  reference?: { filename: string; summary: string; mean_shot: number; shots: number; duration: number } | null;
}

export interface Direction {
  id: string;
  name: string;
  pitch: string;
  rhythm: string;
  tone: string;
  settings: Partial<EditSettings>;
  recommended: boolean;
}

export interface PublishKit {
  titles: string[];
  summary: string;
  hashtags: string[];
  thumbnail_text: string;
  chapters: { t: number; title: string }[];
  description: string;
  thumbnail: string;
  by: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  updated_at: number;
  media: number;
  duration: number;
  thumb: string | null;
}

export interface Word {
  start: number;
  end: number;
  text: string;
  prob: number;
  filler: boolean;
}

export interface Transcript {
  media_id: string;
  language: string | null;
  words: Word[];
}

export interface CaptionLine {
  start: number;
  end: number;
  words: Word[];
}

export interface Segment {
  id: string;
  media_id: string;
  start: number;
  end: number;
  text: string;
  kind: "speech" | "action";
  score: number;
  duplicate_of: string | null;
  features: Record<string, number>;
}

export interface Job {
  id: string;
  project_id: string;
  kind: string;
  status: "en_attente" | "en_cours" | "termine" | "erreur";
  progress: number;
  message: string;
  error: string | null;
  result: unknown;
}

export interface Health {
  ok: boolean;
  claude: boolean;
  claude_model: string;
  whisper_model: string;
  encoder: string;
  music_library: number;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* corps non JSON */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => req<Health>("/api/health"),
  effects: () => req<EffectsCatalog>("/api/effects"),
  sfxUrl: (kind: string) => `/api/sfx/${kind}`,
  listProjects: () => req<ProjectSummary[]>("/api/projects"),
  createProject: (name: string) => req<Project>("/api/projects", json("POST", { name })),
  getProject: (id: string) => req<Project>(`/api/projects/${id}`),
  patchProject: (id: string, body: { name?: string; settings?: EditSettings }) =>
    req<Project>(`/api/projects/${id}`, json("PATCH", body)),
  deleteProject: (id: string) => req<{ ok: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),
  uploadMedia: (id: string, files: File[], onProgress?: (p: number) => void) =>
    new Promise<Media[]>((resolve, reject) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/projects/${id}/media`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else {
          try {
            reject(new Error(JSON.parse(xhr.responseText).detail));
          } catch {
            reject(new Error(xhr.statusText));
          }
        }
      };
      xhr.onerror = () => reject(new Error("Envoi interrompu"));
      xhr.send(fd);
    }),
  deleteMedia: (id: string, mid: string) => req<Project>(`/api/projects/${id}/media/${mid}`, { method: "DELETE" }),
  autoEdit: (id: string, settings: EditSettings) => req<Job>(`/api/projects/${id}/auto-edit`, json("POST", settings)),
  command: (id: string, instruction: string) =>
    req<Job>(`/api/projects/${id}/command`, json("POST", { instruction })),
  saveTimeline: (id: string, tl: Timeline) => req<Timeline>(`/api/projects/${id}/timeline`, json("PUT", tl)),
  captions: (id: string) => req<CaptionLine[]>(`/api/projects/${id}/captions`),
  transcripts: (id: string) => req<Record<string, Transcript>>(`/api/projects/${id}/transcripts`),
  segments: (id: string) => req<Segment[]>(`/api/projects/${id}/segments`),
  editWord: (id: string, mid: string, idx: number, text: string) =>
    req<Word>(`/api/projects/${id}/transcripts/${mid}/words/${idx}`, json("PATCH", { text })),
  uploadMusic: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<MusicSettings>(`/api/projects/${id}/music`, { method: "POST", body: fd });
  },
  export: (id: string, quality: string, fps: number | null = null, smooth = false) =>
    req<Job>(`/api/projects/${id}/export`, json("POST", { quality, fps, smooth })),
  applyComments: (id: string) => req<Job>(`/api/projects/${id}/comments/apply`, { method: "POST" }),
  publishKit: (id: string) => req<Job>(`/api/projects/${id}/publish-kit`, { method: "POST" }),
  versions: (id: string) => req<Versions>(`/api/projects/${id}/versions`),
  duplicateVersion: (id: string, name?: string) => req<Versions>(`/api/projects/${id}/versions`, json("POST", { name })),
  activateVersion: (id: string, vid: string) => req<Project>(`/api/projects/${id}/versions/${vid}/activate`, { method: "POST" }),
  renameVersion: (id: string, vid: string, name: string) => req<Versions>(`/api/projects/${id}/versions/${vid}`, json("PATCH", { name })),
  deleteVersion: (id: string, vid: string) => req<Project>(`/api/projects/${id}/versions/${vid}`, { method: "DELETE" }),
  makeShorts: (id: string, count: number, duration: number, format: Format) =>
    req<Job>(`/api/projects/${id}/shorts`, json("POST", { count, duration, format })),
  presets: () => req<ExportPreset[]>(`/api/export-presets`),
  exportPreset: (id: string, preset: string, versions: string[] | null) =>
    req<Job>(`/api/projects/${id}/export-preset`, json("POST", { preset, versions })),
  uploadAsset: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ asset: string }>(`/api/projects/${id}/assets`, { method: "POST", body: fd });
  },
  assetUrl: (id: string, name: string) => `/api/projects/${id}/assets/${encodeURIComponent(name)}`,
  uploadAudio: async (id: string, file: Blob, filename: string) => {
    const fd = new FormData();
    fd.append("file", file, filename);
    return req<{ asset: string; duration: number }>(`/api/projects/${id}/audio`, { method: "POST", body: fd });
  },
  audioUrl: (id: string, name: string) => `/api/projects/${id}/audio/${encodeURIComponent(name)}`,
  luts: (id: string) => req<{ id: string; label: string; builtin: boolean }[]>(`/api/projects/${id}/luts`),
  uploadLut: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ id: string; label: string; builtin: boolean }>(`/api/projects/${id}/luts`, { method: "POST", body: fd });
  },
  previewFrame: (id: string, p: { media: string; t: number; lut?: string | null; strength?: number; effect?: string | null; background?: string | null }) => {
    const q = new URLSearchParams({ media: p.media, t: p.t.toFixed(3) });
    if (p.lut) { q.set("lut", p.lut); q.set("strength", String(p.strength ?? 1)); }
    if (p.effect) q.set("effect", p.effect);
    if (p.background) q.set("background", p.background);
    return `/api/projects/${id}/preview-frame?${q}`;
  },
  uploadReference: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ summary: string }>(`/api/projects/${id}/reference`, { method: "POST", body: fd });
  },
  deleteReference: (id: string) => req<{ ok: boolean }>(`/api/projects/${id}/reference`, { method: "DELETE" }),
  directions: (id: string, body: { platform: string; tone: string; audience: string }) =>
    req<Job>(`/api/projects/${id}/directions`, json("POST", body)),
  quality: (id: string, fix: boolean) => req<Job>(`/api/projects/${id}/quality`, json("POST", { fix })),
  review: (id: string) => req<Job>(`/api/projects/${id}/review`, { method: "POST" }),
  voiceFocus: (id: string) => req<Job>(`/api/projects/${id}/voice-focus`, { method: "POST" }),
  soundsheetUrl: (id: string) => `/api/projects/${id}/soundsheet`,
  ttsStatus: () => req<Record<string, string | null>>(`/api/tts`),
  dub: (id: string, language: string | null, original_volume = 0.12) =>
    req<Job>(`/api/projects/${id}/dub`, json("POST", { language, original_volume })),
  voiceover: (id: string, text: string, start: number, language: string) =>
    req<Job>(`/api/projects/${id}/voiceover`, json("POST", { text, start, language })),
  languages: () => req<{ code: string; name: string }[]>(`/api/languages`),
  translate: (id: string, language: string | null) =>
    req<Job>(`/api/projects/${id}/translate`, json("POST", { language })),
  templates: () => req<StyleTemplate[]>(`/api/templates`),
  saveTemplate: (id: string, name: string) => req<StyleTemplate>(`/api/projects/${id}/templates`, json("POST", { name })),
  applyTemplate: (id: string, tid: string) => req<Project>(`/api/projects/${id}/templates/${tid}/apply`, { method: "POST" }),
  deleteTemplate: (tid: string) => req<{ ok: boolean }>(`/api/templates/${tid}`, { method: "DELETE" }),
  xmlUrl: (id: string) => `/api/projects/${id}/export.xml`,
  srtUrl: (id: string) => `/api/projects/${id}/captions.srt`,
  job: (jobId: string) => req<Job>(`/api/jobs/${jobId}`),
  activeJobs: (id: string) => req<Job[]>(`/api/projects/${id}/jobs`),
  proxyUrl: (id: string, mid: string) => `/api/projects/${id}/media/${mid}/proxy`,
  thumbUrl: (id: string, mid: string) => `/api/projects/${id}/media/${mid}/thumb`,
  musicUrl: (id: string, v: string) => `/api/projects/${id}/music/current?v=${encodeURIComponent(v)}`,
  exportUrl: (id: string, name: string) => `/api/projects/${id}/exports/${encodeURIComponent(name)}`,
};

export async function waitJob(job: Job, onUpdate: (j: Job) => void): Promise<Job> {
  let j = job;
  onUpdate(j);
  while (j.status === "en_attente" || j.status === "en_cours") {
    await new Promise((r) => setTimeout(r, 500));
    j = await api.job(j.id);
    onUpdate(j);
  }
  if (j.status === "erreur") throw new Error(j.error || "La tâche a échoué");
  return j;
}

export function fmtTime(t: number, precise = false): string {
  const neg = t < 0;
  t = Math.abs(t);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const ss = precise ? s.toFixed(1).padStart(4, "0") : Math.floor(s).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${m}:${ss}`;
}
