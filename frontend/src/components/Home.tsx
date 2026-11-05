import { useEffect, useRef, useState } from "react";
import { api, fmtTime, ProjectSummary } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";

const STEPS = [
  { title: "Analyse", text: "Transcription au mot, silences, moments forts, prises répétées." },
  { title: "Réalisation", text: "Style, durée cible, accroche en premier : chaque choix est expliqué." },
  { title: "Export", text: "16:9, 9:16 ou 1:1, sous-titres et musique, en une passe." },
];

export function Home({ onOpen }: { onOpen: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const caps = useStore((s) => s.caps);
  const input = useRef<HTMLInputElement>(null);

  const refresh = () => api.listProjects().then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { refresh(); useStore.getState().refreshCaps(); }, []);

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

  const status = caps ? (caps.claude ? "Claude actif" : "IA locale") + ` · Whisper ${caps.whisper_model}` + (caps.whisper_device === "cuda" ? " · GPU" : "") : null;

  return (
    <div className="home" onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); quickStart([...e.dataTransfer.files]); }}>
      <header className="appbar">
        <span className="brand"><span className="brand-mark" aria-hidden><Icon name="play" size={12} /></span>Monteur IA</span>
        <span className="spacer" />
        {status && <a href="#/reglages" className={`pill ${caps?.claude ? "ok" : ""}`} title="État du système · ouvrir les réglages">{status}</a>}
        <a className="icon-btn" href="#/reglages" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
      </header>

      <main className="home-main">
        <section className="hero-row">
          <div className={`hero ${over ? "over" : ""}`}>
            <span className="hero-tag">Montage automatique</span>
            <h1>Dépose tes rushes, l'IA monte la vidéo.</h1>
            <p>Elle regarde, écoute, choisit les meilleurs moments, pose les sous-titres et la musique. Tu retouches si tu veux, à la souris ou en français.</p>
            <div className="hero-actions">
              <button className="primary big" onClick={() => input.current?.click()} disabled={!!busy}>
                {busy ?? <><Icon name="upload" size={18} strokeWidth={2} />Importer des vidéos</>}
              </button>
              <button className="big" onClick={() => useStore.getState().guard("Création du projet impossible", async () => onOpen((await api.createProject("Nouveau projet")).id))}>
                Projet vide
              </button>
              <span className="hint">ou glisse-dépose n'importe où sur la page</span>
            </div>
            <input ref={input} type="file" multiple accept="video/*,audio/*" hidden
              onChange={(e) => quickStart([...(e.target.files ?? [])])} />
            {error && <p className="warn">{error}</p>}
          </div>
          <aside className="howto">
            <h2>Comment ça marche</h2>
            {STEPS.map((s, i) => (
              <div key={s.title} className="howto-step">
                <span className="n">{i + 1}</span>
                <div><b>{s.title}</b><p>{s.text}</p></div>
              </div>
            ))}
          </aside>
        </section>

        <section>
          <div className="projects-head">
            <h2>Projets récents</h2>
            {projects && projects.length > 0 && <span className="count">{projects.length} projet{projects.length > 1 ? "s" : ""}</span>}
          </div>
          {projects === null ? <p className="muted">Chargement…</p> : !projects.length ? (
            <p className="muted">Aucun projet pour l'instant : importe des vidéos pour commencer.</p>
          ) : (
            <div className="grid">
              {projects.map((p) => (
                <article key={p.id} className="card">
                  <button className="card-open" onClick={() => onOpen(p.id)} aria-label={`Ouvrir ${p.name}`} />
                  <div className="card-thumb">
                    {p.thumb ? <img src={api.thumbUrl(p.id, p.thumb)} alt="" onError={(e) => (e.currentTarget.style.display = "none")} /> : <Icon name="film" size={40} strokeWidth={1.4} />}
                    <span className="card-tc">{fmtTime(p.duration)}</span>
                    <span className={`card-status ${p.duration > 0 ? "ok" : ""}`}>{p.duration > 0 ? "Monté" : "Brouillon"}</span>
                  </div>
                  <div className="card-body">
                    <b>{p.name}</b>
                    <span className="muted small">{p.media} rush{p.media > 1 ? "es" : ""} · {new Date(p.updated_at * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })}</span>
                  </div>
                  <button className="icon-btn del" title="Supprimer" aria-label={`Supprimer ${p.name}`} onClick={async () => {
                    if (confirm(`Supprimer « ${p.name} » et tous ses fichiers ?`))
                      await useStore.getState().guard("Suppression impossible", async () => { await api.deleteProject(p.id); await refresh(); });
                  }}><Icon name="trash" size={15} /></button>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
