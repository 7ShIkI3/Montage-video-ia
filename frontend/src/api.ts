// Types et appels vers le serveur du Monteur IA.

export type Style = "youtube" | "short" | "vlog" | "podcast";
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
}

export type TextStyle = "titre" | "bandeau" | "legende" | "gros_texte" | "emoji";
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

export interface OverlayItem {
  id: string;
  media_id: string;
  src_in: number;
  src_out: number;
  start: number;
  mode: "plein" | "incrustation";
  reason: string;
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
  speeds: number[];
}

export interface MusicSettings {
  track: string | null;
  volume: number;
  ducking: boolean;
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
  export: (id: string, quality: string) => req<Job>(`/api/projects/${id}/export`, json("POST", { quality })),
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
