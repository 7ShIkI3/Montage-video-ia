// Bouton « Tâches » de la barre du haut : toutes les tâches du projet, en cours puis récentes, avec annulation.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { kindLabel, TrackedJob, useStore } from "../store";

const STATUS_LABEL: Record<TrackedJob["status"], string> = {
  en_attente: "En attente", en_cours: "En cours", termine: "Terminée", erreur: "Erreur", annulee: "Annulée",
};
const STATUS_CLASS: Record<TrackedJob["status"], string> = {
  en_attente: "muted", en_cours: "run", termine: "ok", erreur: "danger", annulee: "muted",
};

const isActive = (j: TrackedJob) => j.status === "en_attente" || j.status === "en_cours";

/** Durée en mm:ss. */
function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}

/** Petit anneau de progression (0 à 1). */
function Ring({ value }: { value: number }) {
  const r = 7, c = 2 * Math.PI * r;
  return (
    <svg className="ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
      <circle cx="9" cy="9" r={r} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <circle cx="9" cy="9" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(1, value)))} transform="rotate(-90 9 9)" />
    </svg>
  );
}

function TaskRow({ job, now }: { job: TrackedJob; now: number }) {
  const [open, setOpen] = useState(false);
  const active = isActive(job);
  const start = job.started_at ?? job.created_at;
  const elapsed = (active ? now : job.finished_at ?? job.updated_at) - start;
  const files = typeof job.result === "string" ? [job.result]
    : Array.isArray(job.result) && job.result.every((x) => typeof x === "string") ? (job.result as string[]) : [];
  return (
    <li className={`task task-${job.status}`}>
      <div className="task-head">
        <b>{job.label ?? kindLabel(job.kind)}</b>
        <span className={`task-status ${STATUS_CLASS[job.status]}`}>{job.cancel_requested && active ? "Arrêt en cours…" : STATUS_LABEL[job.status]}</span>
        <span className="muted small task-time">{mmss(elapsed)}</span>
        {active && (
          <button className="small-btn" disabled={job.cancel_requested} title="Arrêter cette tâche"
            onClick={() => useStore.getState().cancelJob(job.id)}>✕ Annuler</button>
        )}
      </div>
      {active && <div className="progress"><div style={{ width: `${job.progress * 100}%` }} /><span>{job.message || "En attente…"}</span></div>}
      {!active && job.status === "erreur" && job.error && <p className="warn small">{job.error}</p>}
      {!active && job.status === "termine" && job.message && <p className="muted small">{job.message}</p>}
      <div className="task-actions">
        {job.kind === "export" && job.status === "termine" && files.map((f) => (
          <a key={f} className="small-btn btn" href={api.exportUrl(job.project_id, f)} download>Télécharger</a>
        ))}
        {job.status === "erreur" && job.retry && (
          <button className="small-btn" onClick={() => job.retry!()}>↻ Réessayer</button>
        )}
      </div>
      {job.steps?.length > 0 && (
        <>
          <button className="link small" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} Détails ({job.steps.length})</button>
          {open && (
            <ul className="task-steps small">
              {job.steps.map((st, i) => (
                <li key={i}>
                  <span className="muted">{mmss(st.t > 1e8 ? st.t - start : st.t)}</span> {st.message}
                  <span className="muted"> · {Math.round(st.progress * 100)} %</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

export function Tasks() {
  const jobs = useStore((s) => s.jobs);
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const wrap = useRef<HTMLDivElement>(null);

  const all = Object.values(jobs);
  const active = all.filter(isActive).sort((a, b) => b.created_at - a.created_at);
  const recent = all.filter((j) => !isActive(j)).sort((a, b) => (b.finished_at ?? b.updated_at) - (a.finished_at ?? a.updated_at)).slice(0, 10);
  const latest = active[0];

  // Le chrono avance chaque seconde tant qu'on le regarde ou qu'une tâche tourne.
  useEffect(() => {
    if (!open && !active.length) return;
    setNow(Date.now() / 1000);
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [open, active.length]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <div className="tasks-wrap" ref={wrap}>
      <button className={`tasks-btn ${active.length ? "busy" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}
        title="Tâches en cours et récentes">
        {latest ? <Ring value={latest.progress} /> : <span aria-hidden>⏳</span>} Tâches ({active.length})
      </button>
      {open && (
        <div className="popover tasks-drawer">
          <h4>Tâches</h4>
          {!active.length && !recent.length && <p className="muted small">Aucune tâche pour ce projet.</p>}
          <ul className="task-list">
            {active.map((j) => <TaskRow key={j.id} job={j} now={now} />)}
            {recent.map((j) => <TaskRow key={j.id} job={j} now={now} />)}
          </ul>
        </div>
      )}
    </div>
  );
}
