// Accueil : on dépose des vidéos (ou on essaie la démo), puis on retrouve ses projets en grille.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtTime, ProjectSummary } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { ask } from "./Dialog";
import { UpdateBanner } from "./Update";
import { SystemBar, useSystemSummary } from "./SystemBar";
import { EmptyHint } from "./ui/EmptyHint";
import "../styles/home.css";

type Sort = "recent" | "nom" | "duree";

/** Au-delà de ce nombre de projets, la recherche et le tri apparaissent. */
const SEARCH_FROM = 8;

export function Home({ onOpen }: { onOpen: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("recent");
  const [menu, setMenu] = useState<string | null>(null);  // carte dont le menu « ⋯ » est ouvert
  const [sysOpen, setSysOpen] = useState(false);          // popover « Poste »
  const input = useRef<HTMLInputElement>(null);
  const sys = useSystemSummary();
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!(e.target as Element).closest?.(".card-menu, .more")) setMenu(null); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(null); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", key); };
  }, [menu]);
  useEffect(() => {
    if (!sysOpen) return;
    const close = (e: MouseEvent) => { if (!(e.target as Element).closest?.(".sys-wrap")) setSysOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setSysOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", key); };
  }, [sysOpen]);

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

  const searchable = (projects?.length ?? 0) >= SEARCH_FROM;

  return (
    <div className={`home ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); quickStart([...e.dataTransfer.files]); }}>
      <header className="appbar">
        <span className="brand"><span className="brand-mark" aria-hidden><Icon name="play" size={12} /></span>Monteur IA</span>
        <span className="spacer" />
        {sys && (
          <div className="sys-wrap">
            <button type="button" className={`pill home-sys-chip ${sys.ready ? "ok" : "warn"}`} aria-expanded={sysOpen} aria-haspopup="dialog"
              title="État du poste : FFmpeg, GPU, Whisper, voix, réalisateur, disque" onClick={() => setSysOpen(!sysOpen)}>
              {sys.ready ? "Poste prêt" : `${sys.issues} point${sys.issues > 1 ? "s" : ""} à vérifier`}
            </button>
            {sysOpen && (
              <div className="popover home-sys" role="dialog" aria-label="État du poste">
                <h4>État du poste</h4>
                <SystemBar />
              </div>
            )}
          </div>
        )}
        <a className="icon-btn" href="#/reglages" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
      </header>

      <UpdateBanner />

      <main className="home-main">
        <section className="home-hero" aria-label="Commencer">
          <span className="home-hero-icon" aria-hidden="true"><Icon name="upload" size={28} strokeWidth={1.6} /></span>
          <h1>Glisse tes vidéos ici</h1>
          <p>L'IA les analyse, coupe les temps morts et propose un premier montage. Tu retouches ensuite à la main.</p>
          <div className="home-hero-actions">
            <button type="button" className="primary home-import" onClick={() => input.current?.click()} disabled={!!busy}>
              {busy ?? <><Icon name="upload" size={16} strokeWidth={2} />Importer des vidéos</>}
            </button>
            <button type="button" className="link" onClick={tryDemo} disabled={!!busy}
              title="Un clip de combat PvP généré par l'app, monté automatiquement">Essayer sans vidéo</button>
            <button type="button" className="link" onClick={newProject} disabled={!!busy}>Nouveau projet vide</button>
          </div>
          <input ref={input} type="file" multiple accept="video/*,audio/*" hidden onChange={(e) => quickStart([...(e.target.files ?? [])])} />
          {error && <p className="warn small" role="alert">{error}</p>}
        </section>

        <section className="home-projects" aria-labelledby="home-projects-title">
          <div className="home-phead">
            <h2 id="home-projects-title">Projets</h2>
            {projects && projects.length > 0 && <span className="count">{projects.length}</span>}
            <span className="spacer" />
            {searchable && (
              <>
                <input type="search" placeholder="Rechercher un projet…" aria-label="Rechercher un projet" value={q} onChange={(e) => setQ(e.target.value)} />
                <select value={sort} aria-label="Trier" onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="recent">Récents d'abord</option>
                  <option value="nom">Par nom</option>
                  <option value="duree">Par durée</option>
                </select>
              </>
            )}
          </div>
          {shown === null ? <p className="muted">Chargement…</p> : !shown.length ? (
            projects?.length
              ? <p className="empty">Aucun projet ne correspond.</p>
              : <EmptyHint icon="film" text="Aucun projet pour l'instant. Importe des vidéos ci-dessus ou crée un projet vide." />
          ) : (
            <div className="grid">
              {shown.map((p) => (
                <article key={p.id} className={`card ${menu === p.id ? "menu-open" : ""}`}>
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
                  <button className="icon-btn more" title="Actions" aria-label={`Actions pour ${p.name}`} aria-expanded={menu === p.id}
                    onClick={() => setMenu(menu === p.id ? null : p.id)}><Icon name="more" size={18} /></button>
                  {menu === p.id && (
                    <div className="card-menu" role="menu">
                      <button role="menuitem" onClick={() => onOpen(p.id)}><Icon name="play" size={14} />Ouvrir</button>
                      <button role="menuitem" onClick={async () => {
                        setMenu(null);
                        const name = await ask.prompt("Nouveau nom du projet", p.name, { title: "Renommer", ok: "Renommer" });
                        if (name && name.trim() && name.trim() !== p.name)
                          await useStore.getState().guard("Renommage impossible", async () => { await api.patchProject(p.id, { name: name.trim() }); await refresh(); });
                      }}><Icon name="pencil" size={14} />Renommer</button>
                      <button role="menuitem" onClick={async () => {
                        setMenu(null);
                        await useStore.getState().guard("Duplication impossible", async () => {
                          setBusy("Copie du projet…");
                          try { await api.duplicateProject(p.id); await refresh(); } finally { setBusy(null); }
                        });
                      }}><Icon name="copy" size={14} />Dupliquer</button>
                      <button role="menuitem" onClick={() => { setMenu(null); useStore.getState().guard("Ouverture impossible", () => api.openFolder(p.id)); }}>
                        <Icon name="folder" size={14} />Ouvrir le dossier
                      </button>
                      <hr />
                      <button role="menuitem" className="danger-text" onClick={async () => {
                        setMenu(null);
                        if (await ask.confirm(`Supprimer « ${p.name} » et tous ses fichiers ?`, { title: "Supprimer le projet", ok: "Supprimer", danger: true }))
                          await useStore.getState().guard("Suppression impossible", async () => { await api.deleteProject(p.id); await refresh(); });
                      }}><Icon name="trash" size={14} />Supprimer</button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="home-foot">
        <p>Dans l'éditeur : <kbd className="kbd">?</kbd> affiche tous les raccourcis, <kbd className="kbd">Ctrl</kbd><kbd className="kbd">K</kbd> la palette de commandes.</p>
      </footer>
    </div>
  );
}
