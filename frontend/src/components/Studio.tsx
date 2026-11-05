// Versions du montage, shorts automatiques, modèles de style et images (logo, stickers).
import { useEffect, useRef, useState } from "react";
import { api, ExportPreset, Format, StyleTemplate, Versions } from "../api";
import { isBusy, jobOf, useStore } from "../store";

async function saveIfDirty() {
  await useStore.getState().saveNow();
}

export function VersionsPanel() {
  const project = useStore((s) => s.project);
  const { notify, setTimeline, guard, runJob } = useStore.getState();
  const [vs, setVs] = useState<Versions | null>(null);
  const [count, setCount] = useState(3);
  const [dur, setDur] = useState(30);
  const [fmt, setFmt] = useState<Format>("9:16");
  const busy = useStore((s) => isBusy(s, "shorts"));
  const reload = () => project && api.versions(project.id).then(setVs).catch(() => undefined);
  useEffect(() => { reload(); }, [project?.id, project?.active_version, project?.updated_at]); // eslint-disable-line
  if (!project) return null;

  const activate = (vid: string) => guard("Changement de version impossible", async () => {
    await saveIfDirty();
    setTimeline(await api.activateVersion(project.id, vid));
  });
  const duplicate = () => guard("Duplication impossible", async () => {
    await saveIfDirty();
    const name = prompt("Nom de la nouvelle version", "Essai");
    if (name === null) return;
    await api.duplicateVersion(project.id, name);
    setTimeline(await api.getProject(project.id));
  });
  const rename = (vid: string, old: string) => guard("Renommage impossible", async () => {
    const name = prompt("Renommer la version", old);
    if (name) setVs(await api.renameVersion(project.id, vid, name));
  });
  const remove = async (vid: string, name: string) => {
    if (!confirm(`Supprimer la version « ${name} » ?`)) return;
    await guard("Suppression impossible", async () => {
      await saveIfDirty();
      setTimeline(await api.deleteVersion(project.id, vid));
    });
  };
  const shorts = () => {
    if (!project.media.length) return notify("Importe d'abord une vidéo.");
    return runJob("Shorts", () => api.makeShorts(project.id, count, dur, fmt), {
      refresh: false,
      onDone: (done) => {
        const made = done.result as { name: string }[];
        notify(`${made.length} short(s) créé(s) : ${made.map((m) => m.name).join(", ")}`);
        reload();
      },
    });
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
  const { notify, setTimeline, guard } = useStore.getState();
  const [list, setList] = useState<StyleTemplate[]>([]);
  useEffect(() => {
    const load = () => api.templates().then(setList).catch(() => undefined);
    load();
    window.addEventListener("templates-changed", load);
    return () => window.removeEventListener("templates-changed", load);
  }, []);
  if (!project) return null;
  const save = () => guard("Enregistrement du modèle impossible", async () => {
    const name = prompt("Nom du modèle (look, sous-titres, format, musique, logo)", "Ma chaîne");
    if (!name) return;
    await saveIfDirty();
    await api.saveTemplate(project.id, name);
    setList(await api.templates());
    notify(`Modèle « ${name} » enregistré.`);
  });
  const apply = (t: StyleTemplate) => guard("Application du modèle impossible", async () => {
    await saveIfDirty();
    setTimeline(await api.applyTemplate(project.id, t.id));
    notify(`Modèle « ${t.name} » appliqué.`);
  });
  const del = async (t: StyleTemplate) => {
    if (!confirm(`Supprimer le modèle « ${t.name} » ?`)) return;
    await guard("Suppression impossible", async () => {
      await api.deleteTemplate(t.id);
      setList(await api.templates());
    });
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
              <span className="muted small">{t.style} · {t.format} · {t.look}{t.images.length ? " · logo" : ""}{t.brand ? " · marque" : ""}</span>
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
  const job = useStore((s) => jobOf(s, "export"));
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
  const busy = !!job && (job.status === "en_cours" || job.status === "en_attente");
  const go = () => useStore.getState().runJob("Export", () => api.exportPreset(project.id, preset, sel.length ? sel : null), {
    onDone: (done) => onDone(done.result as string[]),
  });
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

/** Identité de marque : couleurs, police et ton tirés d'un logo importé ou d'un site, appliqués aux textes. */
export function BrandPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const [url, setUrl] = useState("");
  const [asTemplate, setAsTemplate] = useState(true);
  const [busy, setBusy] = useState(false);
  if (!project || !tl) return null;
  const logos = Array.from(new Set((tl.images ?? []).map((g) => g.asset)));
  const b = tl.brand;
  const run = async (body: { asset?: string; url?: string }) => {
    const st = useStore.getState();
    setBusy(true);
    try {
      await saveIfDirty();
      const r = await api.setBrand(project.id, { ...body, template: asTemplate });
      await st.refreshProject({ timeline: true });
      if (r.template) window.dispatchEvent(new Event("templates-changed"));
      st.notify(`Identité « ${r.brand.name} » appliquée${r.template ? `, modèle « ${r.template.name} » enregistré` : ""}.`);
    } catch (e) {
      st.notify(`Identité de marque impossible : ${(e as Error).message}`);
    } finally { setBusy(false); }
  };
  const clear = () => useStore.getState().guard("Retrait impossible", async () => {
    await saveIfDirty();
    await api.clearBrand(project.id);
    await useStore.getState().refreshProject({ timeline: true });
  });
  return (
    <section className="panel brand">
      <header><h3>Identité de marque</h3>{b && <button className="small-btn" onClick={clear}>Retirer</button>}</header>
      {b ? (
        <div className="brand-card">
          <div className="brand-swatches">
            {b.colors.map((c) => <span key={c} style={{ background: c }} title={c} className={c === b.accent ? "acc" : c === b.primary ? "pri" : ""} />)}
          </div>
          <p className="small"><b>{b.name || "Marque"}</b> · principale {b.primary} · accent {b.accent}</p>
          {b.font && <p className="small">Police {b.font}{b.font_installed ? "" : " (non installée : police par défaut au rendu)"}</p>}
          {b.tone && <p className="small">Ton : {b.tone}</p>}
        </div>
      ) : (
        <p className="muted small">Tire les couleurs, la police et le ton d'un logo ou d'un site : sous-titres, gros textes et bandeaux prennent les couleurs de la marque.</p>
      )}
      {logos.length > 0 && (
        <div className="field-row">
          {logos.slice(0, 3).map((a) => (
            <button key={a} className="small-btn" disabled={busy} onClick={() => run({ asset: a })} title={a}>Depuis {a.length > 18 ? a.slice(0, 16) + "…" : a}</button>
          ))}
        </div>
      )}
      <div className="field-row">
        <input placeholder="site-de-la-marque.fr" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button className="small-btn" disabled={busy || !url.trim()} onClick={() => run({ url })}>{busy ? "Lecture…" : "Depuis le site"}</button>
      </div>
      <label className="check small"><input type="checkbox" checked={asTemplate} onChange={(e) => setAsTemplate(e.target.checked)} /> Enregistrer aussi un modèle de style</label>
    </section>
  );
}
