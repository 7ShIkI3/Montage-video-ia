// Réalisation en direct : pendant un montage, des directions, des shorts ou la démo, le lecteur montre
// les étapes du réalisateur (faites, en cours, à venir), le message courant, la progression et le chrono.
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { kindLabel, useStore } from "../store";
import { LIVE_KINDS, STAGES, stageIndex } from "../stages";

function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}

export function LiveDirector() {
  const job = useStore((s) => s.job);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const live = !!job && LIVE_KINDS.includes(job.kind);
  const list = useRef<HTMLOListElement>(null);
  const stageNow = job && live ? stageIndex(job.kind, job.stage) : -1;
  // La liste défile seule quand le cadre est bas : l'étape en cours reste toujours visible.
  useEffect(() => {
    const ol = list.current;
    const li = ol?.children[stageNow] as HTMLElement | undefined;
    if (!ol || !li) return;
    if (li.offsetTop < ol.scrollTop) ol.scrollTop = li.offsetTop;
    else if (li.offsetTop + li.offsetHeight > ol.scrollTop + ol.clientHeight) ol.scrollTop = li.offsetTop + li.offsetHeight - ol.clientHeight;
  }, [stageNow, live]);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now() / 1000);
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [live]);
  if (!job || !live) return null;
  const stages = STAGES[job.kind];
  const cur = stageIndex(job.kind, job.stage);
  const waiting = job.status === "en_attente";
  const elapsed = now - (job.started_at ?? job.created_at);
  return (
    <div className="live-director" role="status" aria-live="polite" data-testid="live-director">
      <header>
        <span className="live-dot" aria-hidden />
        <b>Réalisation en direct</b>
        <span className="muted small">{kindLabel(job.kind)} · {mmss(elapsed)}</span>
        <span className="spacer" />
        <button className="small-btn" disabled={job.cancel_requested} onClick={() => useStore.getState().cancelJob(job.id)}>
          {job.cancel_requested ? "Arrêt…" : "Annuler"}
        </button>
      </header>
      <ol className="live-stages" ref={list}>
        {stages.map((s, i) => {
          const state = i < cur ? "done" : i === cur ? "now" : "todo";
          return (
            <li key={s.id} className={state}>
              <span className="live-mark" aria-hidden>
                {state === "done" ? <Icon name="check" size={12} strokeWidth={2.6} /> : state === "now" ? <Icon name="dot" size={12} /> : null}
              </span>
              <span className="live-label">{s.label}</span>
              {state === "now" && job.message && <span className="live-msg">{job.message}</span>}
            </li>
          );
        })}
      </ol>
      <div className="progress">
        <div style={{ width: `${job.progress * 100}%` }} />
        <span>{waiting ? "En attente d'un créneau…" : cur < 0 ? job.message || "Démarrage…" : `${Math.round(job.progress * 100)} %`}</span>
      </div>
    </div>
  );
}
