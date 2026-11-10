// Pitch : à la fin d'un montage, le réalisateur présente son travail (titre, résumé, chiffres, plan rôle par rôle,
// avertissements) et propose de lire, de voir le plan ou d'exporter. Réouvrable depuis l'onglet Plan.
import { useEffect } from "react";
import { fmtTime } from "../api";
import { Icon } from "../icons";
import { timelineDuration, useStore } from "../store";

export function Pitch({ onClose }: { onClose: () => void }) {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  if (!project || !tl) return null;
  const st = useStore.getState();
  const steps = tl.plan ?? [];
  const warnings = project.warnings ?? [];
  const director = tl.director === "claude" ? "Claude" : tl.director === "pvp" ? "PvP local" : "Local";
  const play = () => { onClose(); st.setPlayhead(0); st.setPlaying(true); };
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal pitch" role="dialog" aria-modal="true" aria-labelledby="pitch-title" data-testid="pitch">
        <header>
          <div>
            <span className="muted small">Le réalisateur présente</span>
            <h2 id="pitch-title">{tl.title || project.name}</h2>
          </div>
          <button className="icon-btn" aria-label="Fermer" onClick={onClose}><Icon name="x" size={16} /></button>
        </header>
        <div className="pitch-body">
          <p className="pitch-summary">{tl.summary || "Le montage est prêt : regarde-le, retouche-le à la main ou donne une consigne."}</p>
          <ul className="pitch-stats">
            <li><b>{tl.clips.length}</b><span>plan{tl.clips.length > 1 ? "s" : ""}</span></li>
            <li><b>{fmtTime(timelineDuration(tl))}</b><span>durée</span></li>
            {tl.review && <li><b>{tl.review.score.toFixed(1)}/10</b><span>relecteur</span></li>}
            {tl.quality && <li><b>{tl.quality.score}/100</b><span>qualité</span></li>}
            <li><b>{director}</b><span>réalisateur</span></li>
          </ul>
          {warnings.length > 0 && (
            <ul className="pitch-warn">
              {warnings.map((w, i) => <li key={i}><Icon name="alert" size={14} />{w}</li>)}
            </ul>
          )}
          {steps.length > 0 && (
            <ol className="pitch-plan">
              {steps.map((s, i) => (
                <li key={i} className={s.ok === false ? "ko" : s.ok ? "ok" : ""}>
                  <span className="pitch-mark" aria-hidden>
                    {s.ok === false ? <Icon name="alert" size={13} /> : s.ok ? <Icon name="check" size={13} strokeWidth={2.6} /> : <Icon name="dot" size={13} />}
                  </span>
                  <div>
                    <b>{s.title}</b> <span className="muted small">{s.role}{s.by ? ` · ${s.by}` : ""}</span>
                    {s.items.slice(0, 3).map((it, k) => <span key={k} className="pitch-item">{it}</span>)}
                    {s.notes.map((n, k) => <span key={"n" + k} className="pitch-note">{n}</span>)}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
        <footer className="pitch-actions">
          <button className="primary" onClick={play} disabled={!tl.clips.length}><Icon name="play" size={14} />Lire le montage</button>
          <button onClick={() => { onClose(); st.openRight("plan"); }}><Icon name="list-video" size={14} />Voir le plan</button>
          <button onClick={() => { onClose(); st.setExportOpen(true); }}><Icon name="download" size={14} />Exporter</button>
        </footer>
      </div>
    </div>
  );
}
