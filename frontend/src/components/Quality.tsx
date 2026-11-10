// Contrôle qualité du montage et note du relecteur indépendant.
import { useState } from "react";
import { api, fmtTime } from "../api";
import { clipStarts, isBusy, useStore } from "../store";
import { Icon, IconName } from "../icons";
import { Section } from "./ui/Section";

const CHECK_LABELS: Record<string, string> = {
  accroche: "Accroche", moment_fort: "Moment fort", plan_long: "Rythme", texte: "Texte", lecture: "Lecture",
  flashs: "Flashs", duree: "Durée", bruitage: "Bruitage", synchro: "Synchro son/image", son: "Son", image: "Image", voix_ia: "Voix de synthèse", affirmation: "Affirmation",
};

export function QualityPanel() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const busy = useStore((s) => isBusy(s, "controle", "relecture", "projecteur"));
  if (!project || !tl || !tl.clips.length) return null;
  const q = tl.quality;
  const r = tl.review;
  const spots = tl.clips.flatMap((c, i) => c.masks.filter((m) => m.kind === "projecteur").map((m) => ({ m, i })));
  const starts = clipStarts(tl);
  const run = (kind: "check" | "fix" | "review" | "focus") => {
    const label = kind === "review" ? "Relecture" : kind === "focus" ? "Projecteur voix" : "Contrôle qualité";
    return useStore.getState().runJob(label, () => kind === "review" ? api.review(project.id) : kind === "focus" ? api.voiceFocus(project.id)
      : api.quality(project.id, kind === "fix"), {
      timeline: true,
      onDone: (done) => useStore.getState().notify(done.message || "Contrôle terminé."),
    });
  };
  const tone = (v: number, max: number) => (v / max >= 0.8 ? "good" : v / max >= 0.6 ? "mid" : "bad");
  const issues = (q?.issues ?? []).filter((i) => i.level !== "ok");
  const canFix = issues.some((i) => i.fix);
  return (
    <div className="quality">
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
      {!q && !r && <p className="muted small">Pas encore de contrôle : lance « Contrôler » pour noter ce montage.</p>}
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
        <Section title={`Ce que montre la voix (${spots.length} projecteur${spots.length > 1 ? "s" : ""})`}>
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
        </Section>
      )}
      {q?.fixed && q.fixed.length > 0 && (
        <Section title={`Corrigé automatiquement (${q.fixed.length})`}>
          <ul className="q-fixed">{q.fixed.map((f, k) => <li key={k}>{f}</li>)}</ul>
        </Section>
      )}
      {q?.rendered && q.measured && (
        <p className="muted small">
          Vidéo finie : {q.measured.lufs != null ? `${q.measured.lufs.toFixed(1)} LUFS` : "volume ?"} · crête{" "}
          {q.measured.true_peak != null ? `${q.measured.true_peak.toFixed(1)} dB` : "?"} · {q.measured.flashes_max_per_s ?? 0} flash(s)/s au pire
        </p>
      )}
      <div className="q-actions">
        <div className="q-action">
          <button className="small-btn" disabled={busy} onClick={() => run("check")}>Contrôler</button>
          <span className="muted small">Note le montage et liste les problèmes.</span>
        </div>
        <div className="q-action">
          <button className="small-btn" disabled={busy || !canFix} onClick={() => run("fix")}>Corriger tout seul</button>
          <span className="muted small">{canFix || busy ? "Applique les corrections possibles." : "Rien à corriger pour l'instant."}</span>
        </div>
        <div className="q-action">
          <button className="small-btn" disabled={busy} onClick={() => run("focus")}>Projecteur sur la voix</button>
          <span className="muted small">Cale les plans sur la durée des phrases et pose un projecteur sur ce que la voix désigne.</span>
        </div>
      </div>
      <div className="q-review">
        <button className="small-btn primary" disabled={busy} onClick={() => run("review")}>Faire relire</button>
        {busy && <p className="why-off">Une tâche est en cours.</p>}
        <p className="muted small">
          Le relecteur n'a pas vu la construction du montage. Sous 7/10, le montage est retouché et relu de nouveau (3 tours au plus).{" "}
          <a href={api.soundsheetUrl(project.id)} target="_blank" rel="noreferrer">Feuille son/image</a>
        </p>
      </div>
    </div>
  );
}

const ROLE_ICON: Record<string, IconName> = { stratege: "zap", creatif: "sparkles", storyboard: "film", montage: "scissors", publication: "upload" };

/** Le réalisateur rôle par rôle : ce que chaque étape a décidé, et ce qui ne colle pas. Le titre est porté par la section du panneau Film. */
export function PlanPanel() {
  const tl = useStore((s) => s.timeline);
  const [open, setOpen] = useState<string | null>(null);
  if (!tl?.plan?.length) return null;
  return (
    <div className="plan">
      <ol className="plan-steps">
        {tl.plan.map((st) => (
          <li key={st.role} className={st.ok === false ? "plan-warn" : st.ok ? "plan-good" : ""}>
            <button className="link-like plan-head" onClick={() => setOpen(open === st.role ? null : st.role)}>
              <span><Icon name={ROLE_ICON[st.role] ?? "dot"} size={13} /> <b>{st.title}</b></span>
              <small>{st.by === "claude" ? "Claude" : st.by === "heuristique" ? "moteur local" : "local"}
                {st.ok === false ? ` · ${st.notes.length} à revoir` : st.ok ? " · ok" : ""} <Icon name={open === st.role ? "chevron-down" : "chevron-right"} size={12} /></small>
            </button>
            {open === st.role && (
              <ul>
                {st.items.map((x, k) => <li key={k}>{x}</li>)}
                {st.notes.map((x, k) => <li key={"n" + k} className="plan-note"><Icon name="alert" size={12} /> {x}</li>)}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
