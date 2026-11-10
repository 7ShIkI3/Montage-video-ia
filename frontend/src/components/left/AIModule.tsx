// Module IA : « Monter avec l'IA ». Style, réglages, options avancées (vidéo de référence, 3 directions) et, collé en bas, le bouton de montage.
import { useRef, useState } from "react";
import { api, Direction, EditSettings, Style } from "../../api";
import { Icon } from "../../icons";
import { useStore } from "../../store";
import { ask } from "../Dialog";
import { UrlImport } from "../Library";
import { Section } from "../ui/Section";
import { ModuleFrame } from "./ModuleFrame";

export const STYLES: { id: Style; name: string; desc: string }[] = [
  { id: "youtube", name: "YouTube", desc: "Rythme soutenu, coupes franches" },
  { id: "short", name: "Short", desc: "Vertical, 15–60 s, accroche d'abord" },
  { id: "vlog", name: "Vlog", desc: "Ambiance et plans d'illustration" },
  { id: "podcast", name: "Podcast", desc: "Garde tout, retire silences et « euh »" },
  { id: "pvp", name: "Edit PvP", desc: "Gaming : tes meilleurs combos, vérifiés sur les images par l'IA, calés sur le rythme" },
];

const DURATIONS = [15, 30, 45, 60, 90, 180, 300, 600];
const CAPTION_LABELS: Record<EditSettings["captions"], string> = { karaoke: "sous-titres animés", classique: "sous-titres classiques", aucun: "sans sous-titres" };

/** 15 s, 1 min, 1 min 30, 3 min… (jamais « 1.5 min »). */
function fmtDur(d: number): string {
  if (d < 60) return `${d} s`;
  const m = Math.floor(d / 60);
  const s = d % 60;
  return s ? `${m} min ${s}` : `${m} min`;
}

/** Contenu du module : tout ce qui sert à lancer un montage automatique. */
export function AIPanel() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { notify } = useStore.getState();
  const [s, setS] = useState<EditSettings | null>(null);
  // Réglages des 3 directions : au niveau du panneau, pour survivre au remontage de la section « Avancé » quand les cartes arrivent.
  const [dir, setDir] = useState({ platform: "auto", tone: "auto", audience: "", picked: null as string | null });
  if (!project) return null;
  const settings = s ?? project.settings;
  const set = (patch: Partial<EditSettings>) => setS({ ...settings, ...patch });
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  const dirs = project.directions ?? [];
  const pickedDir = dirs.find((d) => d.id === dir.picked) ?? null;

  const run = async (override?: EditSettings) => {
    const settings_ = override ?? settings;
    if (!project.media.length) return notify("Importe d'abord au moins une vidéo.");
    const dirty = useStore.getState().dirty;
    if (project.timeline.clips.length && !await ask.confirm(
      dirty ? "Remonter remplace le montage actuel et tes retouches non enregistrées. Continuer ?"
        : "Remonter remplace le montage actuel. Continuer ?", { title: "Remonter", ok: "Remonter" })) return;
    const before = useStore.getState().timeline;
    await useStore.getState().runJob("Montage automatique", async () => {
      await api.patchProject(project.id, { settings: settings_ });
      return api.autoEdit(project.id, settings_);
    }, {
      refresh: false,
      onDone: async () => {
        // load() ouvre aussi le pitch du réalisateur : le montage est déjà là quand la ligne suivante s'exécute.
        await useStore.getState().load(project.id);
        // load() vide l'historique : Ctrl+Z doit pouvoir revenir au montage d'avant.
        useStore.setState({ past: before ? [before] : [], future: [] });
        // La timeline joue son animation d'arrivée une fois le pitch refermé.
        useStore.getState().markBuilt();
      },
    });
  };

  const fmt = settings.style === "short" && settings.format === "9:16" ? "9:16 (imposé par Short)" : settings.format;
  const summary = [
    STYLES.find((x) => x.id === settings.style)?.name ?? settings.style,
    settings.target_duration ? fmtDur(settings.target_duration) : "durée auto",
    fmt,
    CAPTION_LABELS[settings.captions] ?? settings.captions,
  ].join(" · ");
  const durations = settings.target_duration && !DURATIONS.includes(settings.target_duration) ? [...DURATIONS, settings.target_duration].sort((a, b) => a - b) : DURATIONS;
  const hasClips = project.timeline.clips.length > 0;

  return (
    <section className="ai-panel">
      <div className="ai-content">
        <p className="ai-summary" aria-live="polite">{summary}</p>

        <div className="ai-block">
          <div className="fx-title">Style</div>
          <div className="styles">
            {STYLES.map((st) => (
              <button key={st.id} className={`style-card ${settings.style === st.id ? "on" : ""}`} aria-pressed={settings.style === st.id} title={st.desc}
                onClick={() => set({ style: st.id, format: st.id === "short" ? "9:16" : settings.format === "9:16" ? "16:9" : settings.format })}>
                <b>{st.name}</b><span>{st.desc}</span>
              </button>
            ))}
          </div>
        </div>

        <Section title="Réglages">
          <div className="field-row">
            <label>Durée cible
              <select value={settings.target_duration ?? ""} onChange={(e) => set({ target_duration: e.target.value ? +e.target.value : null })}>
                <option value="">Auto</option>
                {durations.map((d) => <option key={d} value={d}>{fmtDur(d)}</option>)}
              </select>
            </label>
            <label>Format
              <select value={settings.format} onChange={(e) => set({ format: e.target.value as EditSettings["format"] })}>
                <option value="16:9">16:9 paysage</option>
                <option value="9:16">9:16 vertical</option>
                <option value="1:1">1:1 carré</option>
              </select>
            </label>
          </div>
          <label>Sous-titres
            <select value={settings.captions} onChange={(e) => set({ captions: e.target.value as EditSettings["captions"] })}>
              <option value="karaoke">Animés mot par mot</option>
              <option value="classique">Classiques</option>
              <option value="aucun">Aucun</option>
            </select>
          </label>
          <div className="toggles">
            <label className="sw-row"><span>Couper les silences</span><input type="checkbox" className="switch" checked={settings.remove_silences} onChange={(e) => set({ remove_silences: e.target.checked })} /></label>
            <label className="sw-row"><span>Couper les « euh »</span><input type="checkbox" className="switch" checked={settings.remove_fillers} onChange={(e) => set({ remove_fillers: e.target.checked })} /></label>
            <label className="sw-row"><span>Zooms dynamiques</span><input type="checkbox" className="switch" checked={settings.punch_zooms} onChange={(e) => set({ punch_zooms: e.target.checked })} /></label>
            <label className="sw-row"><span>Habillage auto</span><input type="checkbox" className="switch" checked={settings.auto_effects ?? true} onChange={(e) => set({ auto_effects: e.target.checked })} /></label>
            <label className="sw-row"><span>Musique de fond</span><input type="checkbox" className="switch" checked={settings.music} onChange={(e) => set({ music: e.target.checked })} /></label>
          </div>
          <label>Précisions pour l'IA <span className="muted small">(facultatif)</span>
            <textarea rows={2} placeholder="Ex. : garde surtout la partie sur le matériel, ton dynamique"
              value={settings.instructions} onChange={(e) => set({ instructions: e.target.value })} />
          </label>
        </Section>

        {/* Les cartes de directions arrivent dans cette section : elle se rouvre d'elle-même (remontage) quand elles apparaissent. */}
        <Section key={dirs.length ? "avec-directions" : "sans-directions"} defaultOpen={dirs.length > 0}
          title={<>Avancé{pickedDir && <span className="sec-note"> · {pickedDir.name}</span>}</>}>
          <ReferencePicker busy={busy} />
          <DirectionsPicker busy={busy} dirs={dirs} state={dir} onChange={(p) => setDir({ ...dir, ...p })}
            onRun={(d) => { const next = { ...settings, ...d.settings } as EditSettings; setS(next); run(next); }} />
        </Section>
      </div>

      <div className="cta-bar">
        {project.warnings.length > 0 && (
          <div className="cta-warns">{project.warnings.map((w, i) => <p key={i} className="warn small">{w}</p>)}</div>
        )}
        {busy && job && (
          <div className="progress-row">
            <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || "En attente…"}</span></div>
            <button className="small-btn" onClick={() => useStore.getState().cancelJob(job.id)} disabled={job.cancel_requested}>
              {job.cancel_requested ? "Arrêt…" : "Annuler"}
            </button>
          </div>
        )}
        <button className="cta-ai" onClick={() => run()} disabled={busy || !project.media.length}>
          {busy ? "Montage en cours…" : <><Icon name="wand" size={18} strokeWidth={2} />{hasClips ? "Remonter" : "Monter automatiquement"}</>}
        </button>
        {!project.media.length && <span className="why-off">Importe d'abord une vidéo (module Médias).</span>}
      </div>
    </section>
  );
}

type DirState = { platform: string; tone: string; audience: string; picked: string | null };

/** Avant de monter : 3 directions créatives au choix (questions facultatives : plateforme, ton, public). */
function DirectionsPicker({ busy, dirs, state, onChange, onRun }: {
  busy: boolean; dirs: Direction[]; state: DirState; onChange: (patch: Partial<DirState>) => void; onRun: (d: Direction) => void;
}) {
  const project = useStore((s) => s.project);
  if (!project) return null;
  const propose = () => useStore.getState().runJob("3 directions", () => api.directions(project.id, { platform: state.platform, tone: state.tone, audience: state.audience }));
  const chosen = dirs.find((d) => d.id === state.picked) ?? null;
  return (
    <div className="directions">
      <div className="fx-title">3 directions avant de monter</div>
      <div className="field-row">
        <label>Plateforme
          <select value={state.platform} onChange={(e) => onChange({ platform: e.target.value })}>
            <option value="auto">Peu importe</option><option value="youtube">YouTube</option>
            <option value="tiktok">TikTok, Reels, Shorts</option><option value="instagram">Instagram</option>
            <option value="linkedin">LinkedIn</option>
          </select>
        </label>
        <label>Ton
          <select value={state.tone} onChange={(e) => onChange({ tone: e.target.value })}>
            <option value="auto">Automatique</option><option value="fun">Drôle, énergique</option>
            <option value="pro">Sérieux, pro</option><option value="emotion">Émouvant</option>
            <option value="pedago">Pédagogique</option>
          </select>
        </label>
      </div>
      <input placeholder="Public (facultatif) : ex. débutants en montage" aria-label="Public visé" value={state.audience} onChange={(e) => onChange({ audience: e.target.value })} />
      <button className="small-btn" disabled={busy || !project.media.length} onClick={propose}>
        {dirs.length ? "Proposer d'autres directions" : "Proposer 3 directions"}
      </button>
      {dirs.map((d) => (
        <button key={d.id} className={`direction-card ${d.recommended ? "rec" : ""} ${state.picked === d.id ? "on" : ""}`} aria-pressed={state.picked === d.id}
          disabled={busy} onClick={() => onChange({ picked: d.id })}>
          <b>{d.name}{d.recommended ? " · recommandée" : ""}</b>
          <span>{d.pitch}</span>
          <small>{[d.rhythm, d.tone, d.settings.format, d.settings.target_duration ? `${d.settings.target_duration} s` : "durée libre"].filter(Boolean).join(" · ")}</small>
        </button>
      ))}
      {dirs.length > 0 && (
        <>
          <button className="small-btn primary-soft" disabled={busy || !chosen} onClick={() => chosen && onRun(chosen)}>
            <Icon name="wand" size={14} />Monter avec cette direction
          </button>
          {!chosen && <span className="why-off">Choisis d'abord une direction ci-dessus.</span>}
        </>
      )}
    </div>
  );
}

/** « Monte comme cette vidéo » : le montage copie le rythme d'une vidéo de référence (pas son look). */
function ReferencePicker({ busy }: { busy: boolean }) {
  const project = useStore((s) => s.project);
  const input = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  if (!project) return null;
  const ref = project.reference;
  const { notify, refreshProject } = useStore.getState();
  return (
    <div className="reference-box">
      <div className="field-row">
        <span className="small"><b>Vidéo de référence</b> {ref ? `· ${ref.filename}` : "· aucune"}</span>
        <button className="small-btn" disabled={busy || sending} onClick={() => input.current?.click()}>
          {sending ? "Analyse…" : ref ? "Changer" : "Choisir…"}
        </button>
        {ref && <button className="small-btn" disabled={busy} onClick={() => useStore.getState().guard("Retrait impossible", async () => { await api.deleteReference(project.id); await refreshProject(); })}>Retirer</button>}
      </div>
      <input ref={input} type="file" accept="video/*" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setSending(true);
        try {
          await useStore.getState().saveNow();
          await api.uploadReference(project.id, f);
          await refreshProject();
          notify("Référence analysée : le prochain montage suivra son rythme.");
        } catch (err) { notify(`Référence refusée : ${(err as Error).message}`); } finally { setSending(false); }
      }} />
      <UrlImport placeholder="Ou le lien d'une vidéo dont copier le rythme" busy={busy || sending}
        onSubmit={(url) => useStore.getState().runJob("Référence", () => api.referenceUrl(project.id, url), {
          onDone: () => notify("Référence analysée : le prochain montage suivra son rythme."),
        })} />
      <p className="muted small">{ref ? ref.summary : "Dépose un montage que tu aimes : son rythme (durée des plans, fondus) sera copié, pas ses couleurs."}</p>
    </div>
  );
}

export function AIModule() {
  return (
    <ModuleFrame module="ia" title="Monter avec l'IA" flush>
      <AIPanel />
    </ModuleFrame>
  );
}
