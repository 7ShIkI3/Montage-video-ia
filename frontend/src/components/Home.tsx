import { useEffect, useRef, useState } from "react";
import { api, fmtTime, ProjectSummary } from "../api";
import { useStore } from "../store";

export function Home({ onOpen }: { onOpen: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const refresh = () => api.listProjects().then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { refresh(); }, []);

  const quickStart = async (files: File[]) => {
    if (!files.length) return;
    try {
      const name = files[0].name.replace(/\.[^.]+$/, "");
      setBusy("Création du projet…");
      const p = await api.createProject(name);
      await api.uploadMedia(p.id, files, (x) => setBusy(`Envoi des rushes ${Math.round(x * 100)} %`));
      onOpen(p.id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };

  return (
    <div className="home" onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); quickStart([...e.dataTransfer.files]); }}>
      <div className="hero">
        <a className="btn hero-settings" href="#/reglages" title="Clé Claude, transcription, voix">⚙ Réglages</a>
        <div className="logo">▶</div>
        <h1>Monteur IA</h1>
        <p>Dépose tes rushes. L'IA les regarde, les écoute, choisit les meilleurs moments et monte la vidéo.
          Tu retouches si tu veux.</p>
        <div className="hero-actions">
          <button className="primary big" onClick={() => input.current?.click()} disabled={!!busy}>
            {busy ?? "Importer des vidéos"}
          </button>
          <button className="big" onClick={() => useStore.getState().guard("Création du projet impossible", async () => onOpen((await api.createProject("Nouveau projet")).id))}>
            Projet vide
          </button>
        </div>
        <input ref={input} type="file" multiple accept="video/*,audio/*" hidden
          onChange={(e) => quickStart([...(e.target.files ?? [])])} />
        {error && <p className="warn">{error}</p>}
      </div>
      <h2>Projets récents</h2>
      {projects === null ? <p className="muted">Chargement…</p> : !projects.length ? (
        <p className="muted">Aucun projet pour l'instant.</p>
      ) : (
        <div className="grid">
          {projects.map((p) => (
            <div key={p.id} className="card">
              <button className="card-open" onClick={() => onOpen(p.id)} aria-label={`Ouvrir ${p.name}`} />
              <div className="card-thumb">
                {p.thumb ? <img src={api.thumbUrl(p.id, p.thumb)} alt="" onError={(e) => (e.currentTarget.style.display = "none")} /> : "🎬"}
              </div>
              <div className="card-body">
                <b>{p.name}</b>
                <span className="muted small">{p.media} rush(es) · montage {fmtTime(p.duration)} · {new Date(p.updated_at * 1000).toLocaleDateString("fr-FR")}</span>
              </div>
              <button className="icon-btn del" title="Supprimer" onClick={async () => {
                if (confirm(`Supprimer « ${p.name} » et tous ses fichiers ?`))
                  await useStore.getState().guard("Suppression impossible", async () => { await api.deleteProject(p.id); await refresh(); });
              }}>✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
