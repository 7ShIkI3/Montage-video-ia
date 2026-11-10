// Bibliothèque musicale : morceaux déjà là, musiques libres de droits (ccMixter) à télécharger, son d'un lien.
// Les tâches de la bibliothèque n'ont pas de projet : on les suit ici directement.
import { useEffect, useState } from "react";
import { api, Job, LibraryTrack, MusicResult, waitJob } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";
import { ask } from "./Dialog";

/** Lance une tâche sans projet (bibliothèque, modèles) et suit son avancement. Null si échec. */
export async function runStandaloneJob(label: string, start: () => Promise<Job>, onUpdate?: (j: Job) => void): Promise<Job | null> {
  const st = useStore.getState();
  try {
    const done = await waitJob(await start(), (j) => onUpdate?.(j));
    return done;
  } catch (e) {
    st.fail(label, e);
    return null;
  }
}

export function JobLine({ job }: { job: Job | null }) {
  if (!job || (job.status !== "en_attente" && job.status !== "en_cours")) return null;
  return <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || "…"}</span></div>;
}

type Tab = "bibliotheque" | "libres" | "lien";

export function MusicLibraryDialog({ current, onPick, onClose }: { current: string | null; onPick: (path: string) => void; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("bibliotheque");
  const [tracks, setTracks] = useState<LibraryTrack[] | null>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<MusicResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [url, setUrl] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const notify = useStore((s) => s.notify);

  const refresh = () => api.musicLibrary().then(setTracks).catch((e) => notify(`Bibliothèque : ${(e as Error).message}`));
  useEffect(() => { refresh(); }, []);  // eslint-disable-line
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const search = async () => {
    setSearching(true);
    try { setResults(await api.musicSearch(q, 25)); } catch (e) { notify(`Recherche impossible : ${(e as Error).message}`); } finally { setSearching(false); }
  };
  const add = async (item: MusicResult) => {
    const done = await runStandaloneJob("Téléchargement", () => api.musicDownload(item), setJob);
    setJob(null);
    if (done) { notify(`Ajouté à la bibliothèque : ${done.result as string}`, { level: "ok" }); await refresh(); setTab("bibliotheque"); }
  };
  const fromUrl = async () => {
    if (!/^https?:\/\//.test(url.trim())) return notify("Donne un lien complet (https://…).");
    const done = await runStandaloneJob("Téléchargement", () => api.musicFromUrl(url.trim()), setJob);
    setJob(null);
    if (done) { notify(`Ajouté à la bibliothèque : ${done.result as string}`, { level: "ok" }); setUrl(""); await refresh(); setTab("bibliotheque"); }
  };
  const remove = async (t: LibraryTrack) => {
    if (!await ask.confirm(`Supprimer « ${t.name} » de la bibliothèque ?`, { title: "Supprimer le morceau", ok: "Supprimer", danger: true })) return;
    await useStore.getState().guard("Suppression impossible", async () => { await api.musicDelete(t.name); await refresh(); });
  };
  const isCurrent = (t: LibraryTrack) => !!current && (current === t.path || current.split(/[\\/]/).pop() === t.name);
  const busy = !!job && (job.status === "en_attente" || job.status === "en_cours");

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal library" role="dialog" aria-modal="true" aria-labelledby="lib-title">
        <header>
          <h2 id="lib-title">Musique</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Fermer"><Icon name="x" size={16} /></button>
        </header>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "bibliotheque"} className={tab === "bibliotheque" ? "on" : ""} onClick={() => setTab("bibliotheque")}>Ma bibliothèque{tracks ? ` (${tracks.length})` : ""}</button>
          <button role="tab" aria-selected={tab === "libres"} className={tab === "libres" ? "on" : ""} onClick={() => setTab("libres")}>Musiques libres</button>
          <button role="tab" aria-selected={tab === "lien"} className={tab === "lien" ? "on" : ""} onClick={() => setTab("lien")}>Depuis un lien</button>
        </div>
        <div className="library-body">
          <JobLine job={job} />
          {tab === "bibliotheque" && (
            tracks === null ? <p className="muted">Chargement…</p> : !tracks.length ? (
              <p className="muted">Bibliothèque vide. Cherche une musique libre ou ajoute un son depuis un lien.</p>
            ) : (
              <ul className="track-list">
                {tracks.map((t) => (
                  <li key={t.name} className={isCurrent(t) ? "on" : ""}>
                    <Icon name="music" size={14} />
                    <div className="meta">
                      <div className="name">{t.title ? `${t.artist ? t.artist + " · " : ""}${t.title}` : t.name}</div>
                      <div className="muted small">
                        {t.license ? <>{t.license_url ? <a href={t.license_url} target="_blank" rel="noreferrer">{t.license}</a> : t.license}</> : "licence inconnue"}
                        {t.source ? ` · ${t.source}` : ""} · {(t.size / 1e6).toFixed(1)} Mo
                      </div>
                    </div>
                    <button className="small-btn primary" onClick={() => { onPick(t.path); onClose(); }}>{isCurrent(t) ? "En place" : "Utiliser"}</button>
                    <button className="icon-btn" title="Supprimer de la bibliothèque" aria-label="Supprimer" onClick={() => remove(t)}><Icon name="trash" size={14} /></button>
                  </li>
                ))}
              </ul>
            )
          )}
          {tab === "libres" && (
            <>
              <form className="search-row" onSubmit={(e) => { e.preventDefault(); search(); }}>
                <input value={q} placeholder="Ambiance, genre, instrument : calm piano, hip hop, epic…" aria-label="Rechercher une musique libre" onChange={(e) => setQ(e.target.value)} autoFocus />
                <button type="submit" disabled={searching}><Icon name="search" size={14} />{searching ? "Recherche…" : "Chercher"}</button>
              </form>
              <p className="muted small">Morceaux Creative Commons de ccMixter (instrumentaux). La licence est gardée avec le fichier : la plupart demandent de créditer l'artiste dans la description.</p>
              {results && !results.length && <p className="muted">Rien trouvé. Essaie en anglais, avec un seul mot.</p>}
              {results && results.length > 0 && (
                <ul className="track-list">
                  {results.map((r) => (
                    <li key={r.id}>
                      <Icon name="music" size={14} />
                      <div className="meta">
                        <div className="name">{r.artist} · {r.title}</div>
                        <div className="muted small">
                          <a href={r.license_url} target="_blank" rel="noreferrer">{r.license}</a>
                          {r.duration ? ` · ${r.duration}` : ""}{r.bpm ? ` · ${r.bpm} bpm` : ""}{r.size ? ` · ${r.size}` : ""}
                          {r.page && <> · <a href={r.page} target="_blank" rel="noreferrer">page</a></>}
                        </div>
                      </div>
                      <button className="small-btn" disabled={busy} onClick={() => add(r)}><Icon name="download" size={13} />Ajouter</button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          {tab === "lien" && (
            <>
              <form className="search-row" onSubmit={(e) => { e.preventDefault(); fromUrl(); }}>
                <input value={url} placeholder="https://… (YouTube, SoundCloud, fichier audio…)" aria-label="Lien du son" onChange={(e) => setUrl(e.target.value)} autoFocus />
                <button type="submit" disabled={busy}><Icon name="download" size={14} />Ajouter</button>
              </form>
              <p className="muted small">Le son du lien est extrait en MP3 dans la bibliothèque. Vérifie que tu as le droit d'utiliser la musique avant de publier.</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Champ « depuis un lien » réutilisable (rush, vidéo de référence). */
export function UrlImport({ placeholder, busy, onSubmit }: { placeholder: string; busy: boolean; onSubmit: (url: string) => void }) {
  const [url, setUrl] = useState("");
  const notify = useStore((s) => s.notify);
  return (
    <form className="search-row" onSubmit={(e) => {
      e.preventDefault();
      if (!/^https?:\/\//.test(url.trim())) return notify("Donne un lien complet (https://…).");
      onSubmit(url.trim());
      setUrl("");
    }}>
      <input value={url} placeholder={placeholder} aria-label="Lien" onChange={(e) => setUrl(e.target.value)} disabled={busy} />
      <button type="submit" disabled={busy || !url.trim()}><Icon name="link" size={14} />{busy ? "…" : "Importer"}</button>
    </form>
  );
}

/** Bouton qui ouvre la bibliothèque musicale ; choisir un morceau en fait la musique de fond du montage. */
export function MusicLibraryButton({ label = "Bibliothèque…", className = "small-btn" }: { label?: string; className?: string }) {
  const tl = useStore((s) => s.timeline);
  const [open, setOpen] = useState(false);
  if (!tl) return null;
  return (
    <>
      {open && <MusicLibraryDialog current={tl.music.track} onClose={() => setOpen(false)}
        onPick={(path) => useStore.getState().setTimelineProps({ music: { ...tl.music, track: path } })} />}
      <button type="button" className={className} onClick={() => setOpen(true)} title="Bibliothèque, musiques libres de droits, son d'un lien">
        <Icon name="music" size={13} />{label}
      </button>
    </>
  );
}
