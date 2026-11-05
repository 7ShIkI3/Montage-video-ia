// Recherche web pour la vidéo : une question, une réponse sourcée, gardée dans le projet et donnée au réalisateur.
import { useState } from "react";
import { api, ResearchNote } from "../api";
import { Icon } from "../icons";
import { isBusy, jobOf, useStore } from "../store";

/** Rendu minimal du Markdown des réponses : paragraphes, puces, **gras**. */
export function renderLite(text: string) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <b key={i}>{part.slice(2, -2)}</b> : <span key={i}>{part}</span>);
  const blocks: JSX.Element[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) { blocks.push(<ul key={blocks.length}>{bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}</ul>); bullets = []; }
  };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (/^[-*•]\s+/.test(line)) { bullets.push(line.replace(/^[-*•]\s+/, "")); continue; }
    flush();
    blocks.push(<p key={blocks.length}>{inline(line.replace(/^#+\s*/, ""))}</p>);
  }
  flush();
  return blocks;
}

export function ResearchPanel() {
  const project = useStore((s) => s.project);
  const caps = useStore((s) => s.caps);
  const busy = useStore((s) => isBusy(s, "recherche"));
  const job = useStore((s) => jobOf(s, "recherche"));
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  if (!project) return null;
  const notes = [...(project.research || [])].reverse();
  const { notify, runJob, addText } = useStore.getState();

  const ask = () => {
    const question = q.trim();
    if (!question) return;
    runJob("Recherche", () => api.research(project.id, question), {
      onDone: (j) => { setQ(""); setOpen((j.result as ResearchNote)?.id ?? null); },
    });
  };
  const remove = (n: ResearchNote) => useStore.getState().guard("Suppression impossible", async () => {
    await api.deleteResearch(project.id, n.id);
    await useStore.getState().refreshProject();
  });
  const copy = (t: string) => navigator.clipboard?.writeText(t).then(() => notify("Copié !"), () => undefined);
  const insert = (n: ResearchNote) => {
    const first = n.answer.split(/(?<=[.!?])\s+/)[0]?.slice(0, 120) || n.question;
    addText({ text: first, style: "bandeau", position: "bas", duration: 4 });
    notify("Texte posé au curseur : modifie-le dans l'inspecteur.");
  };

  return (
    <section className="panel research">
      <header><h3>Recherche</h3>{notes.length > 0 && <span className="muted small">{notes.length} note{notes.length > 1 ? "s" : ""}</span>}</header>
      <p className="muted small">Un fait, un chiffre, une règle, un contexte : la réponse est gardée ici et le réalisateur s'appuie dessus au prochain montage ou à la prochaine consigne.</p>
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <input value={q} placeholder="Ex. : date de sortie de Minecraft 1.21, règles du PvP…" aria-label="Question" disabled={busy} onChange={(e) => setQ(e.target.value)} />
        <button type="submit" className="primary" disabled={busy || !q.trim()}><Icon name="globe" size={14} />{busy ? "…" : "Chercher"}</button>
      </form>
      {busy && job && <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || "Recherche…"}</span></div>}
      {caps && !caps.claude && <p className="muted small">Sans clé Claude : résultats DuckDuckGo bruts. Avec une clé (Réglages), la réponse est rédigée et sourcée.</p>}
      <ul className="research-list">
        {notes.map((n) => (
          <li key={n.id} className={open === n.id ? "open" : ""}>
            <button className="research-q" onClick={() => setOpen(open === n.id ? null : n.id)} aria-expanded={open === n.id}>
              <Icon name={open === n.id ? "chevron-down" : "chevron-right"} size={13} />
              <span>{n.question}</span>
              <small className="muted">{n.by === "claude" ? "Claude" : "DuckDuckGo"}</small>
            </button>
            {open === n.id && (
              <div className="research-body">
                <div className="research-answer">{renderLite(n.answer)}</div>
                {n.sources.length > 0 && (
                  <ul className="research-sources">
                    {n.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer" title={s.url}>{s.title || s.url}</a></li>)}
                  </ul>
                )}
                <div className="row-btns">
                  <button className="small-btn" onClick={() => copy(n.answer)}><Icon name="copy" size={13} />Copier</button>
                  <button className="small-btn" onClick={() => insert(n)} title="Pose la première phrase en bandeau au curseur"><Icon name="plus" size={13} />En texte</button>
                  <button className="small-btn" onClick={() => remove(n)}><Icon name="trash" size={13} />Supprimer</button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
