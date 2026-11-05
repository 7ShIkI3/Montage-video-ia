// Accueil : navigateur de projets (créer, importer, ouvrir), comme la fenêtre de projets d'un logiciel de montage.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtTime, ProjectSummary } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { ask } from "./Dialog";
import { SystemBar } from "./SystemBar";

type Sort = "recent" | "nom" | "duree";

export function Home({ onOpen }: { onOpen: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("recent");
  const input = useRef<HTMLInputElement>(null);

  const refresh = () => api.listProjects().then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { refresh(); useStore.getState().refreshCaps(); }, []);

  const quickStart = async (files: File[]) => {
    if (!files.length) return;
    try {
      const name = files[0].name.replace(/\.[^.]+$/, "");
      setBusy("Création du projet…");
      const p = await api.createProject(name);
      await api.uploadMedia(p.id, files, (x) => setBusy(`Envoi des médias ${Math.round(x * 100)} %`));
      onOpen(p.id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };
  const newProject = () => useStore.getState().guard("Création du projet impossible", async () => onOpen((await api.createProject("Nouveau projet")).id));
  // Démo sans vidéo : le projet s'ouvre tout de suite, le clip et le montage se font sous les yeux de l'utilisateur.
  const tryDemo = () => useStore.getState().guard("Démo impossible", async () => {
    setBusy("Préparation de la démo…");
    try { onOpen((await api.demo()).project.id); } finally { setBusy(null); }
  });

  const shown = useMemo(() => {
    if (!projects) return null;
    const nq = q.trim().toLowerCase();
    const list = nq ? projects.filter((p) => p.name.toLowerCase().includes(nq)) : [...projects];
    list.sort((a, b) => (sort === "nom" ? a.name.localeCompare(b.name, "fr") : sort === "duree" ? b.duration - a.duration : b.updated_at - a.updated_at));
    return list;
  }, [projects, q, sort]);

  return (
    <div className={`home ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); quickStart([...e.dataTransfer.files]); }}>
      <header className="appbar">
        <span className="brand"><span className="brand-mark" aria-hidden><Icon name="play" size={12} /></span>Monteur IA</span>
        <span className="spacer" />
        <a className="icon-btn" href="#/reglages" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
      </header>

      <main className="browser">
        <aside className="browser-side">
          <h2>Commencer</h2>
          <button className="primary" onClick={() => input.current?.click()} disabled={!!busy}>
            {busy ?? <><Icon name="upload" size={16} strokeWidth={2} />Importer des vidéos</>}
          </button>
          <button onClick={newProject} disabled={!!busy}><Icon name="plus" size={16} />Nouveau projet</button>
          <button onClick={tryDemo} disabled={!!busy} title="Un clip de combat PvP généré par l'app, monté automatiquement"><Icon name="sparkles" size={16} />Essayer sans vidéo</button>
          <p className="hint">Ou glisse des fichiers vidéo n'importe où sur cette page.</p>
          <input ref={input} type="file" multiple accept="video/*,audio/*" hidden onChange={(e) => quickStart([...(e.target.files ?? [])])} />
          {error && <p className="warn small">{error}</p>}
          <h2>État du poste</h2>
          <SystemBar />
          <h2>Raccourcis</h2>
          <p className="hint">Dans l'éditeur : <kbd className="kbd">?</kbd> affiche tous les raccourcis, <kbd className="kbd">Ctrl</kbd><kbd className="kbd">K</kbd> la palette de commandes.</p>
        </aside>

        <section className="browser-main">
          <div className="browser-head">
            <h2>Projets</h2>
            {projects && projects.length > 0 && <span className="count">{projects.length}</span>}
            <span className="spacer" />
            <input type="search" placeholder="Rechercher un projet…" aria-label="Rechercher un projet" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={sort} aria-label="Trier" onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="recent">Récents d'abord</option>
              <option value="nom">Par nom</option>
              <option value="duree">Par durée</option>
            </select>
          </div>
          {shown === null ? <p className="muted">Chargement…</p> : !shown.length ? (
            <p className="empty">{projects?.length ? "Aucun projet ne correspond." : "Aucun projet : importe des vidéos ou crée un projet vide."}</p>
          ) : (
            <div className="grid">
              {shown.map((p) => (
                <article key={p.id} className="card">
                  <button className="card-open" onClick={() => onOpen(p.id)} aria-label={`Ouvrir ${p.name}`} />
                  <div className="card-thumb">
                    {p.thumb ? <img src={api.thumbUrl(p.id, p.thumb)} alt="" onError={(e) => (e.currentTarget.style.display = "none")} /> : <Icon name="film" size={40} strokeWidth={1.4} />}
                    <span className="card-tc">{fmtTime(p.duration)}</span>
                    <span className={`card-status ${p.duration > 0 ? "ok" : ""}`}>{p.duration > 0 ? "Monté" : "Brouillon"}</span>
                  </div>
                  <div className="card-body">
                    <b>{p.name}</b>
                    <span className="muted small">{p.media} média{p.media > 1 ? "s" : ""} · {new Date(p.updated_at * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })}</span>
                  </div>
                  <button className="icon-btn del" title="Supprimer" aria-label={`Supprimer ${p.name}`} onClick={async () => {
                    if (await ask.confirm(`Supprimer « ${p.name} » et tous ses fichiers ?`, { title: "Supprimer le projet", ok: "Supprimer", danger: true }))
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
