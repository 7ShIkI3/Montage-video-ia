// Contrôle qualité du montage et note du relecteur indépendant.
import { useState } from "react";
import { api, fmtTime, waitJob } from "../api";
import { clipStarts, useStore } from "../store";

const CHECK_LABELS: Record<string, string> = {
  accroche: "Accroche", moment_fort: "Moment fort", plan_long: "Rythme", texte: "Texte", lecture: "Lecture",
  flashs: "Flashs", duree: "Durée", bruitage: "Bruitage", synchro: "Synchro son/image", son: "Son", image: "Image", voix_ia: "Voix IA", affirmation: "Affirmation",
};

export function QualityPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const job = useStore((s) => s.job);
  const [open, setOpen] = useState(true);
  if (!project || !tl || !tl.clips.length) return null;
  const q = tl.quality;
  const r = tl.review;
  const busy = !!job && ["controle", "relecture", "projecteur"].includes(job.kind) && job.status !== "termine" && job.status !== "erreur";
  const spots = tl.clips.flatMap((c, i) => c.masks.filter((m) => m.kind === "projecteur").map((m) => ({ m, i })));
  const starts = clipStarts(tl);
  const run = async (kind: "check" | "fix" | "review" | "focus") => {
    const st = useStore.getState();
    try {
      await st.saveNow();
      const j = kind === "review" ? await api.review(project.id) : kind === "focus" ? await api.voiceFocus(project.id)
        : await api.quality(project.id, kind === "fix");
      const done = await waitJob(j, st.setJob);
      await st.refreshProject({ timeline: true });
      st.notify(done.message || "Contrôle terminé.");
    } catch (e) {
      st.notify(`Contrôle impossible : ${(e as Error).message}`);
    } finally { st.setJob(null); }
  };
  const tone = (v: number, max: number) => (v / max >= 0.8 ? "good" : v / max >= 0.6 ? "mid" : "bad");
  const issues = (q?.issues ?? []).filter((i) => i.level !== "ok");
  return (
    <div className="fx-section quality">
      <button className="fx-title link-like" onClick={() => setOpen(!open)}>Contrôle qualité et relecture {open ? "▾" : "▸"}</button>
      <div className="q-scores">
        <div className={`q-score ${q ? tone(q.score, 100) : ""}`}>
          <b>{q ? q.score : "–"}</b><span>/100</span>
          <small>Contrôle{q?.rendered ? " (vidéo finie)" : " (plan)"}</small>
        </div>
        <div className={`q-score ${r ? tone(r.score, 10) : ""}`}>
          <b>{r ? r.score.toFixed(1) : "–"}</b><span>/10</span>
          <small>Relecteur {r ? (r.reviewer === "claude" ? "Claude" : "local") : ""}{r && r.rounds > 1 ? ` · ${r.history.join(" → ")}` : ""}</small>
        </div>
      </div>
      {open && (
        <>
          {r?.verdict && <p className="small">{r.verdict}</p>}
          {issues.length > 0 && (
            <ul className="q-list">
              {issues.map((i, k) => (
                <li key={k} className={`q-${i.level}`}>
                  <button className="link-like" disabled={i.t == null} onClick={() => i.t != null && useStore.getState().setPlayhead(i.t)}>
                    {i.t != null ? fmtTime(i.t) : "—"}
                  </button>
                  <span><b>{CHECK_LABELS[i.check] ?? i.check}</b> · {i.message}{i.fix ? " (corrigeable)" : ""}</span>
                </li>
              ))}
            </ul>
          )}
          {!issues.length && q && <p className="muted small">Aucun problème relevé.</p>}
          {r && r.reviewer === "claude" && r.remarks.length > 0 && (
            <ul className="q-list">
              {r.remarks.map((m, k) => (
                <li key={k} className="q-remark">
                  <button className="link-like" disabled={m.t == null} onClick={() => m.t != null && useStore.getState().setPlayhead(m.t)}>
                    {m.t != null ? fmtTime(m.t) : "—"}
                  </button>
                  <span>{m.problem}{m.fix ? ` → ${m.fix}` : ""}</span>
                </li>
              ))}
            </ul>
          )}
          {tl.reference_match && (
            <p className="small">
              <b>Rythme de la référence</b> : {tl.reference_match.target_shot.toFixed(1)} s par plan · obtenu{" "}
              {tl.reference_match.result_shot.toFixed(1)} s en {tl.reference_match.rounds} tour(s)
              {tl.reference_match.unmatched.length ? ` · pas égalé : ${tl.reference_match.unmatched.join(" ; ")}` : " · égalé"}
            </p>
          )}
          {(spots.length > 0 || (tl.voice_focus?.length ?? 0) > 0) && (
            <details className="small"><summary>Ce que montre la voix ({spots.length} projecteur{spots.length > 1 ? "s" : ""})</summary>
              <ul className="q-list">
                {spots.map(({ m, i }, k) => (
                  <li key={k} className="q-remark">
                    <button className="link-like" onClick={() => useStore.getState().setPlayhead(starts[i] + (m.start ?? 0))}>
                      {fmtTime(starts[i] + (m.start ?? 0))}
                    </button>
                    <span>{m.label ? <b>{m.label} · </b> : null}{m.reason}</span>
                  </li>
                ))}
                {(tl.voice_focus ?? []).filter((n) => !n.startsWith("projecteur")).map((n, k) => <li key={"n" + k} className="q-note"><span>{n}</span></li>)}
              </ul>
            </details>
          )}
          {q?.fixed && q.fixed.length > 0 && (
            <details className="small"><summary>Corrigé automatiquement ({q.fixed.length})</summary>
              <ul>{q.fixed.map((f, k) => <li key={k}>{f}</li>)}</ul>
            </details>
          )}
          {q?.rendered && q.measured && (
            <p className="muted small">
              Vidéo finie : {q.measured.lufs != null ? `${q.measured.lufs.toFixed(1)} LUFS` : "volume ?"} · crête{" "}
              {q.measured.true_peak != null ? `${q.measured.true_peak.toFixed(1)} dB` : "?"} · {q.measured.flashes_max_per_s ?? 0} flash(s)/s au pire
            </p>
          )}
          <div className="field-row">
            <button className="small-btn" disabled={busy} onClick={() => run("check")}>Contrôler</button>
            <button className="small-btn" disabled={busy || !issues.some((i) => i.fix)} onClick={() => run("fix")}>Corriger tout seul</button>
            <button className="small-btn" disabled={busy} onClick={() => run("focus")}
              title="Cale les plans sur la durée des phrases et pose un projecteur sur ce que la voix désigne">Projecteur sur la voix</button>
            <button className="small-btn primary" disabled={busy} onClick={() => run("review")}>Faire relire</button>
          </div>
          <p className="muted small">
            Le relecteur n'a pas vu la construction du montage. Sous 7/10, l'IA retouche et refait relire (3 tours au plus).{" "}
            <a href={api.soundsheetUrl(project.id)} target="_blank" rel="noreferrer">Feuille son/image</a>
          </p>
        </>
      )}
    </div>
  );
}
