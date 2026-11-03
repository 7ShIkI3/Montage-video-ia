// Versions du montage, shorts automatiques, modèles de style et images (logo, stickers).
import { useEffect, useRef, useState } from "react";
import { api, ExportPreset, Format, StyleTemplate, Versions, waitJob } from "../api";
import { useStore } from "../store";

async function saveIfDirty() {
  const st = useStore.getState();
  if (st.dirty && st.timeline && st.project) {
    await api.saveTimeline(st.project.id, st.timeline);
    st.markSaved();
  }
}

export function VersionsPanel() {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { setJob, notify, setTimeline } = useStore.getState();
  const [vs, setVs] = useState<Versions | null>(null);
  const [count, setCount] = useState(3);
  const [dur, setDur] = useState(30);
  const [fmt, setFmt] = useState<Format>("9:16");
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  const reload = () => project && api.versions(project.id).then(setVs).catch(() => undefined);
  useEffect(() => { reload(); }, [project?.id, project?.active_version, project?.updated_at]); // eslint-disable-line
  if (!project) return null;

  const activate = async (vid: string) => {
    try {
      await saveIfDirty();
      setTimeline(await api.activateVersion(project.id, vid));
    } catch (e) { notify((e as Error).message); }
  };
  const duplicate = async () => {
    await saveIfDirty();
    const name = prompt("Nom de la nouvelle version", "Essai");
    if (name === null) return;
    await api.duplicateVersion(project.id, name);
    setTimeline(await api.getProject(project.id));
  };
  const rename = async (vid: string, old: string) => {
    const name = prompt("Renommer la version", old);
    if (name) setVs(await api.renameVersion(project.id, vid, name));
  };
  const remove = async (vid: string, name: string) => {
    if (!confirm(`Supprimer la version « ${name} » ?`)) return;
    try {
      await saveIfDirty();
      setTimeline(await api.deleteVersion(project.id, vid));
    } catch (e) { notify((e as Error).message); }
  };
  const shorts = async () => {
    if (!project.media.length) return notify("Importe d'abord une vidéo.");
    try {
      await saveIfDirty();
      const j = await api.makeShorts(project.id, count, dur, fmt);
      const done = await waitJob(j, setJob);
      const made = done.result as { name: string }[];
      notify(`${made.length} short(s) créé(s) : ${made.map((m) => m.name).join(", ")}`);
      reload();
    } catch (e) {
      notify(`Shorts impossibles : ${(e as Error).message}`);
    } finally { setJob(null); }
  };

  return (
    <section className="panel versions">
      <header><h3>Versions</h3><button className="small-btn" onClick={duplicate} title="Copier la version actuelle">＋ Dupliquer</button></header>
      <ul className="version-list">
        {vs?.versions.map((v) => (
          <li key={v.id} className={v.id === vs.active ? "on" : ""}>
            <button className="v-main" onClick={() => activate(v.id)} title={v.note || "Ouvrir cette version"}>
              <span className="v-name">{v.name}</span>
              <span className="muted small">{Math.round(v.duration)} s{v.note ? ` · ${v.note}` : ""}</span>
            </button>
            {v.score != null && <span className={`score-badge ${v.score >= 70 ? "hi" : v.score >= 50 ? "mid" : ""}`}>{v.score}</span>}
            <button className="icon-btn" title="Renommer" onClick={() => rename(v.id, v.name)}>✎</button>
            {vs.versions.length > 1 && <button className="icon-btn" title="Supprimer" onClick={() => remove(v.id, v.name)}>✕</button>}
          </li>
        ))}
      </ul>
      <div className="shorts-box">
        <div className="fx-title">Shorts automatiques</div>
        <p className="muted small">L'IA cherche les passages les plus partageables, les monte en vertical et leur donne un score viral.</p>
        <div className="field-row">
          <label>Nombre<select value={count} onChange={(e) => setCount(+e.target.value)}>{[1, 2, 3, 4, 5, 8].map((n) => <option key={n}>{n}</option>)}</select></label>
          <label>Durée<select value={dur} onChange={(e) => setDur(+e.target.value)}>{[15, 30, 45, 60, 90].map((n) => <option key={n} value={n}>{n} s</option>)}</select></label>
          <label>Format<select value={fmt} onChange={(e) => setFmt(e.target.value as Format)}><option value="9:16">9:16</option><option value="1:1">1:1</option><option value="16:9">16:9</option></select></label>
        </div>
        <button className="primary small" disabled={busy} onClick={shorts}>✂ Générer les shorts</button>
      </div>
    </section>
  );
}

export function TemplatesPanel() {
  const project = useStore((s) => s.project);
  const { notify, setTimeline } = useStore.getState();
  const [list, setList] = useState<StyleTemplate[]>([]);
  useEffect(() => { api.templates().then(setList).catch(() => undefined); }, []);
  if (!project) return null;
  const save = async () => {
    const name = prompt("Nom du modèle (look, sous-titres, format, musique, logo)", "Ma chaîne");
    if (!name) return;
    await saveIfDirty();
    await api.saveTemplate(project.id, name);
    setList(await api.templates());
    notify(`Modèle « ${name} » enregistré.`);
  };
  const apply = async (t: StyleTemplate) => {
    await saveIfDirty();
    setTimeline(await api.applyTemplate(project.id, t.id));
    notify(`Modèle « ${t.name} » appliqué.`);
  };
  const del = async (t: StyleTemplate) => {
    if (!confirm(`Supprimer le modèle « ${t.name} » ?`)) return;
    await api.deleteTemplate(t.id);
    setList(await api.templates());
  };
  return (
    <section className="panel templates">
      <header><h3>Modèles de style</h3><button className="small-btn" onClick={save}>＋ Enregistrer</button></header>
      {!list.length && <p className="muted small">Enregistre ton habillage (look, sous-titres, format, musique, logo) pour le réutiliser sur tes prochaines vidéos.</p>}
      <ul className="version-list">
        {list.map((t) => (
          <li key={t.id}>
            <button className="v-main" onClick={() => apply(t)} title="Appliquer à ce projet">
              <span className="v-name">{t.name}</span>
              <span className="muted small">{t.style} · {t.format} · {t.look}{t.images.length ? " · logo" : ""}</span>
            </button>
            <button className="icon-btn" title="Supprimer" onClick={() => del(t)}>✕</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ImageButtons() {
  const project = useStore((s) => s.project);
  const { addImage, notify } = useStore.getState();
  const input = useRef<HTMLInputElement>(null);
  const [mark, setMark] = useState(true);
  if (!project) return null;
  const pick = (watermark: boolean) => { setMark(watermark); input.current?.click(); };
  return (
    <div className="field-row">
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        try {
          const { asset } = await api.uploadAsset(project.id, f);
          addImage(asset, mark);
        } catch (err) { notify((err as Error).message); }
      }} />
      <button className="small-btn" onClick={() => pick(true)}>＋ Logo / filigrane</button>
      <button className="small-btn" onClick={() => pick(false)}>＋ Image au curseur</button>
    </div>
  );
}

export function PresetExport({ onDone }: { onDone: (names: string[]) => void }) {
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const { setJob, notify } = useStore.getState();
  const [presets, setPresets] = useState<ExportPreset[]>([]);
  const [preset, setPreset] = useState("tiktok");
  const [vs, setVs] = useState<Versions | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  useEffect(() => { api.presets().then(setPresets).catch(() => undefined); }, []);
  useEffect(() => {
    if (!project) return;
    api.versions(project.id).then((v) => { setVs(v); setSel([v.active]); }).catch(() => undefined);
  }, [project?.id, project?.active_version]); // eslint-disable-line
  if (!project) return null;
  const busy = job?.kind === "export" && job.status !== "termine" && job.status !== "erreur";
  const go = async () => {
    try {
      await saveIfDirty();
      const j = await api.exportPreset(project.id, preset, sel.length ? sel : null);
      const done = await waitJob(j, setJob);
      onDone(done.result as string[]);
      await useStore.getState().refreshProject();
    } catch (e) {
      notify(`Export impossible : ${(e as Error).message}`);
    } finally { setJob(null); }
  };
  const p = presets.find((x) => x.id === preset);
  return (
    <div className="export-alt">
      <h4>Pour un réseau, en un clic</h4>
      <div className="preset-grid">
        {presets.map((x) => (
          <button key={x.id} className={`fx-chip ${preset === x.id ? "on" : ""}`} onClick={() => setPreset(x.id)}>{x.label}</button>
        ))}
      </div>
      {p && <p className="muted small">{p.format ? `Format ${p.format} (recadrage auto), ` : "Format du montage, "}{p.quality}{p.fps ? `, ${p.fps} i/s` : ""}, fichier .{p.container === "prores" ? "mov" : p.container === "mov" ? "mov" : p.container}</p>}
      {vs && vs.versions.length > 1 && (
        <div className="queue">
          <div className="fx-title">File d'export : versions à rendre</div>
          {vs.versions.map((v) => (
            <label key={v.id} className="inline small">
              <input type="checkbox" checked={sel.includes(v.id)}
                onChange={(e) => setSel(e.target.checked ? [...sel, v.id] : sel.filter((x) => x !== v.id))} />
              {v.name}
            </label>
          ))}
        </div>
      )}
      <button className="primary" disabled={busy || !sel.length} onClick={go}>
        {busy ? "Rendu en cours…" : sel.length > 1 ? `Exporter ${sel.length} versions` : "Exporter"}
      </button>
    </div>
  );
}
