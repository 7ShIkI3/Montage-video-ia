import { useRef, useState } from "react";
import { api, Direction, EditSettings, fmtTime, Style } from "../api";
import { useStore } from "../store";
import { Icon } from "../icons";

const STYLES: { id: Style; name: string; desc: string }[] = [
  { id: "youtube", name: "YouTube", desc: "Rythme soutenu, coupes franches" },
  { id: "short", name: "Short", desc: "Vertical, 15–60 s, accroche d'abord" },
  { id: "vlog", name: "Vlog", desc: "Ambiance et plans d'illustration" },
  { id: "podcast", name: "Podcast", desc: "Garde tout, retire silences et « euh »" },
  { id: "pvp", name: "Edit PvP", desc: "Gaming : combos, coups au rythme, effets 3D" },
];

export function MediaPanel() {
  const project = useStore((s) => s.project);
  const notify = useStore((s) => s.notify);
  const input = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<number | null>(null);
  const [over, setOver] = useState(false);
  if (!project) return null;

  const send = async (files: File[]) => {
    if (!files.length) return;
    setUpload(0);
    try {
      await api.uploadMedia(project.id, files, setUpload);
      await useStore.getState().refreshProject();
    } catch (e) {
      notify(`Import impossible : ${(e as Error).message}`);
    } finally {
      setUpload(null);
    }
  };

  return (
    <section className={`panel media-panel ${over ? "over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); send([...e.dataTransfer.files]); }}>
      <header>
        <h3>Rushes</h3>
        <button className="small-btn" onClick={() => input.current?.click()}><Icon name="plus" size={13} strokeWidth={2.4} />Importer</button>
        <input ref={input} type="file" multiple accept="video/*,audio/*" hidden
          onChange={(e) => { send([...(e.target.files ?? [])]); e.target.value = ""; }} />
      </header>
      {upload !== null && (
        <div className="progress"><div style={{ width: `${upload * 100}%` }} /><span>Envoi {Math.round(upload * 100)} %</span></div>
      )}
      {!project.media.length && upload === null && (
        <div className="dropzone" onClick={() => input.current?.click()}>
          Glisse tes vidéos ici<br /><span className="muted small">MP4, MOV, MKV, WEBM… plusieurs fichiers possibles</span>
        </div>
      )}
      <ul className="media-list">
        {project.media.map((m) => (
          <li key={m.id}>
            <div className="thumb">
              {m.proxy ? <img src={api.thumbUrl(project.id, m.id)} alt="" /> : <Icon name={m.has_video ? "film" : "mic"} size={16} />}
            </div>
            <div className="meta">
              <div className="name" title={m.filename}>{m.filename}</div>
              <div className="muted small">
                {fmtTime(m.duration)} · {m.width ? `${m.width}×${m.height}` : "audio"} {m.analyzed ? "· analysé" : ""}
              </div>
            </div>
            <button className="icon-btn" title="Retirer" onClick={async () => {
              if (!confirm(`Retirer ${m.filename} du projet ?`)) return;
              const st = useStore.getState();
              await st.guard("Retrait impossible", async () => {
                await st.saveNow();
                await api.deleteMedia(project.id, m.id);
                await st.refreshProject({ timeline: true });
              });
            }} aria-label="Retirer"><Icon name="x" size={14} /></button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function AIPanel() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { notify } = useStore.getState();
  const [s, setS] = useState<EditSettings | null>(null);
  if (!project) return null;
  const settings = s ?? project.settings;
  const set = (patch: Partial<EditSettings>) => setS({ ...settings, ...patch });
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");

  const run = async (override?: EditSettings) => {
    const settings_ = override ?? settings;
    if (!project.media.length) return notify("Importe d'abord au moins une vidéo.");
    const dirty = useStore.getState().dirty;
    if (project.timeline.clips.length && !confirm(
      dirty ? "Remonter remplace le montage actuel et tes retouches non enregistrées. Continuer ?"
        : "Remonter remplace le montage actuel. Continuer ?")) return;
    const before = useStore.getState().timeline;
    await useStore.getState().runJob("Montage automatique", async () => {
      await api.patchProject(project.id, { settings: settings_ });
      return api.autoEdit(project.id, settings_);
    }, {
      refresh: false,
      onDone: async () => {
        await useStore.getState().load(project.id);
        // load() vide l'historique : Ctrl+Z doit pouvoir revenir au montage d'avant.
        useStore.setState({ past: before ? [before] : [], future: [] });
        notify("Montage terminé. Retouche-le à la main ou donne une consigne.");
      },
    });
  };

  return (
    <section className="panel ai-panel">
      <header><h3>Montage automatique</h3></header>
      <div className="styles">
        {STYLES.map((st) => (
          <button key={st.id} className={`style-card ${settings.style === st.id ? "on" : ""}`}
            onClick={() => set({ style: st.id, format: st.id === "short" ? "9:16" : settings.format === "9:16" ? "16:9" : settings.format })}>
            <b>{st.name}</b><span>{st.desc}</span>
          </button>
        ))}
      </div>
      <div className="field-row">
        <label>Durée cible
          <select value={settings.target_duration ?? ""} onChange={(e) => set({ target_duration: e.target.value ? +e.target.value : null })}>
            <option value="">Auto</option>
            {[15, 30, 45, 60, 90, 180, 300, 600].map((d) => <option key={d} value={d}>{d < 60 ? `${d} s` : `${d / 60} min`}</option>)}
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
        <label><input type="checkbox" checked={settings.remove_silences} onChange={(e) => set({ remove_silences: e.target.checked })} /> Couper les silences</label>
        <label><input type="checkbox" checked={settings.remove_fillers} onChange={(e) => set({ remove_fillers: e.target.checked })} /> Couper les « euh »</label>
        <label><input type="checkbox" checked={settings.punch_zooms} onChange={(e) => set({ punch_zooms: e.target.checked })} /> Zooms dynamiques</label>
        <label><input type="checkbox" checked={settings.auto_effects ?? true} onChange={(e) => set({ auto_effects: e.target.checked })} /> Habillage auto</label>
        <label><input type="checkbox" checked={settings.music} onChange={(e) => set({ music: e.target.checked })} /> Musique de fond</label>
      </div>
      <label>Consignes <span className="muted small">(facultatif)</span>
        <textarea rows={2} placeholder="Ex. : garde surtout la partie sur le matériel, ton dynamique"
          value={settings.instructions} onChange={(e) => set({ instructions: e.target.value })} />
      </label>
      <ReferencePicker busy={busy} />
      <DirectionsPicker busy={busy} onPick={(d) => { const next = { ...settings, ...d.settings } as EditSettings; setS(next); run(next); }} />
      <button className="primary big" onClick={() => run()} disabled={busy || !project.media.length}>
        {busy ? "Montage en cours…" : <><Icon name="wand" size={18} strokeWidth={2} />{project.timeline.clips.length ? "Remonter" : "Monter automatiquement"}</>}
      </button>
      {busy && job && (
        <div className="progress-row">
          <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || "En attente…"}</span></div>
          <button className="small-btn" onClick={() => useStore.getState().cancelJob(job.id)} disabled={job.cancel_requested}>
            {job.cancel_requested ? "Arrêt…" : "Annuler"}
          </button>
        </div>
      )}
      {project.warnings.map((w, i) => <p key={i} className="warn small">{w}</p>)}
    </section>
  );
}

/** Avant de monter : 3 directions créatives au choix (questions facultatives : plateforme, ton, public). */
function DirectionsPicker({ busy, onPick }: { busy: boolean; onPick: (d: Direction) => void }) {
  const project = useStore((s) => s.project);
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState("auto");
  const [tone, setTone] = useState("auto");
  const [audience, setAudience] = useState("");
  if (!project) return null;
  const dirs = project.directions ?? [];
  const ask = () => useStore.getState().runJob("3 directions", () => api.directions(project.id, { platform, tone, audience }));
  return (
    <div className="directions">
      <button className="link-like small" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={13} /> Proposer 3 directions avant de monter
      </button>
      {open && (
        <>
          <div className="field-row">
            <label>Plateforme
              <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                <option value="auto">Peu importe</option><option value="youtube">YouTube</option>
                <option value="tiktok">TikTok, Reels, Shorts</option><option value="instagram">Instagram</option>
                <option value="linkedin">LinkedIn</option>
              </select>
            </label>
            <label>Ton
              <select value={tone} onChange={(e) => setTone(e.target.value)}>
                <option value="auto">Automatique</option><option value="fun">Drôle, énergique</option>
                <option value="pro">Sérieux, pro</option><option value="emotion">Émouvant</option>
                <option value="pedago">Pédagogique</option>
              </select>
            </label>
          </div>
          <input placeholder="Public (facultatif) : ex. débutants en montage" value={audience} onChange={(e) => setAudience(e.target.value)} />
          <button className="small-btn" disabled={busy || !project.media.length} onClick={ask}>
            {dirs.length ? "Proposer d'autres directions" : "Proposer 3 directions"}
          </button>
          {dirs.map((d) => (
            <button key={d.id} className={`direction-card ${d.recommended ? "rec" : ""}`} disabled={busy} onClick={() => onPick(d)}>
              <b>{d.name}{d.recommended ? " · recommandée" : ""}</b>
              <span>{d.pitch}</span>
              <small>{[d.rhythm, d.tone, d.settings.format, d.settings.target_duration ? `${d.settings.target_duration} s` : "durée libre"].filter(Boolean).join(" · ")}</small>
            </button>
          ))}
          {dirs.length > 0 && <p className="muted small">Clique une direction : le montage se refait dans ce sens.</p>}
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
      <p className="muted small">{ref ? ref.summary : "Dépose un montage que tu aimes : son rythme (durée des plans, fondus) sera copié, pas ses couleurs."}</p>
    </div>
  );
}
