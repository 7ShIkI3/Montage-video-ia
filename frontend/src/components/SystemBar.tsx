// Bandeau d'état du poste : une pastille par organe (FFmpeg, GPU, Whisper, voix, réalisateur, disque), verte, orange ou rouge.
// Un clic sur une pastille montre le détail et ce qu'il faut faire ; le rapport complet se copie pour le support.
import { useEffect, useState } from "react";
import { api, SystemLevel, SystemReport } from "../api";
import { Icon } from "../icons";
import { useStore } from "../store";

type PillId = "ffmpeg" | "gpu" | "whisper" | "voices" | "claude" | "disk";
type Row = [label: string, value: string];

const RANK: Record<SystemLevel, number> = { ok: 0, warn: 1, error: 2 };
const yesNo = (b: boolean) => (b ? "oui" : "non");

/** Les pastilles et, pour chacune, son état, son conseil et son détail. */
function pills(r: SystemReport): { id: PillId; label: string; level: SystemLevel; fix: string | null; rows: Row[] }[] {
  const fixes = (...f: (string | null)[]) => f.filter(Boolean).join(" ") || null;
  const gpuLevel = RANK[r.gpu.level] >= RANK[r.encoder.level] ? r.gpu.level : r.encoder.level;
  return [
    { id: "ffmpeg", label: "FFmpeg", level: r.ffmpeg.level, fix: r.ffmpeg.fix, rows: [
      ["Version", r.ffmpeg.version ?? "introuvable"], ["Programme", r.ffmpeg.path], ["Sous-titres incrustés (libass)", yesNo(r.ffmpeg.libass)],
    ] },
    { id: "gpu", label: "GPU et encodeur", level: gpuLevel, fix: fixes(r.gpu.fix, r.encoder.fix), rows: [
      ["Carte graphique", r.gpu.name ? `${r.gpu.name}${r.gpu.memory ? ` · ${r.gpu.memory}` : ""}` : "aucune carte NVIDIA"],
      ["Encodeur d'export", `${r.encoder.name} (${r.encoder.hardware ? "carte graphique" : "processeur"})`],
    ] },
    { id: "whisper", label: "Whisper", level: r.whisper.level, fix: r.whisper.fix, rows: [
      ["Modèle", `${r.whisper.model} · ${r.whisper.model_cached ? "sur le disque" : "pas encore téléchargé"}`],
      ["Appareil demandé", r.whisper.device_requested],
      ["Appareil utilisé", r.whisper.device_used ?? "pas encore chargé"],
      ["CUDA / cuBLAS", `${yesNo(r.whisper.cuda)} / ${yesNo(r.whisper.cublas)}`],
      ...(r.whisper.fallback_reason ? [["Repli sur le processeur", r.whisper.fallback_reason] as Row] : []),
    ] },
    { id: "voices", label: "Voix", level: r.voices.level, fix: r.voices.fix, rows: [
      ["espeak-ng", r.voices.espeak ? "trouvé" : "absent"], ["Piper", r.voices.piper ? "disponible" : "absent"],
      ["Voix Piper installées", r.voices.installed.length ? r.voices.installed.join(", ") : "aucune"],
    ] },
    { id: "claude", label: "Réalisateur", level: r.claude.level, fix: r.claude.fix, rows: [
      ["Moteur", r.claude.key_set ? "Claude" : "moteur local"],
      ["Clé Claude", r.claude.key_set ? `présente (${r.claude.key_source === "env" ? "variable d'environnement" : "fichier"})` : "absente"],
      ["Modèle", r.claude.model],
    ] },
    { id: "disk", label: "Disque", level: r.folders.level, fix: r.folders.fix, rows: [
      ["Espace libre", r.folders.free_gb === null ? "inconnu" : `${r.folders.free_gb} Go`],
      ["Données", r.folders.data], ["Projets", `${r.folders.project_count} · ${r.folders.projects}`],
      ["Musiques", r.folders.music], ["Voix", r.folders.voices], ["Tâches en cours", String(r.jobs.active)],
    ] },
  ];
}

export function SystemBar() {
  const [report, setReport] = useState<SystemReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<PillId | null>(null);

  const load = (refresh: boolean) => {
    setBusy(true);
    return api.system(refresh)
      .then((r) => { setReport(r); setError(null); })
      .catch((e: Error) => setError(e.message || "Le serveur ne répond pas."))
      .finally(() => setBusy(false));
  };
  useEffect(() => { load(false); }, []);

  const copy = () => report && navigator.clipboard?.writeText(JSON.stringify(report, null, 2)).then(
    () => useStore.getState().notify("Rapport copié : colle-le dans ton message."),
    () => useStore.getState().notify("Copie impossible : le presse-papier est refusé."));

  const list = report ? pills(report) : [];
  const current = list.find((p) => p.id === open);
  const problems = report?.summary.problems.length ?? 0;
  return (
    <div className="sysbar">
      {error && <p className="warn small">{error}</p>}
      {!report && !error && <p className="muted small">Contrôle du poste…</p>}
      {report && (
        <>
          <div className="sysbar-pills" role="group" aria-label="État du poste">
            {list.map((p) => (
              <button key={p.id} type="button" className={`pill sys-pill ${p.level} ${open === p.id ? "open" : ""}`} aria-expanded={open === p.id}
                title={p.fix ?? "Tout fonctionne"} aria-label={`${p.label} : ${p.level === "ok" ? "en ordre" : p.level === "warn" ? "à vérifier" : "problème"}`} onClick={() => setOpen(open === p.id ? null : p.id)}>{p.label}</button>
            ))}
          </div>
          <p className={`small sysbar-summary ${problems ? "warn" : "muted"}`}>
            {problems ? `${problems} point${problems > 1 ? "s" : ""} à vérifier : touche une pastille pour le détail.` : "Le poste est prêt."}
          </p>
          {current && (
            <div className={`sysbar-detail ${current.level}`}>
              {current.fix && <p className="sysbar-fix"><Icon name={current.level === "ok" ? "check" : "alert"} size={14} />{current.fix}</p>}
              <dl>{current.rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
            </div>
          )}
        </>
      )}
      <div className="sysbar-actions">
        <button type="button" className="small-btn" disabled={busy} onClick={() => load(true)} title="Refaire le contrôle du poste">
          <Icon name="refresh" size={13} />{busy ? "Contrôle…" : "Actualiser"}
        </button>
        <button type="button" className="small-btn" disabled={!report} onClick={copy} title="Copier le rapport complet (JSON) pour le support">
          <Icon name="copy" size={13} />Copier le rapport
        </button>
      </div>
    </div>
  );
}

/** Résumé du poste pour la puce de l'accueil : prêt, ou nombre de points à vérifier. Null tant que le contrôle n'a pas répondu. */
export function useSystemSummary(): { ready: boolean; issues: number } | null {
  const [summary, setSummary] = useState<{ ready: boolean; issues: number } | null>(null);
  useEffect(() => {
    let alive = true;
    api.system(false)
      .then((r) => { if (alive) setSummary({ ready: r.summary.problems.length === 0, issues: r.summary.problems.length }); })
      .catch(() => undefined);  // serveur muet : pas de puce, l'accueil reste utilisable
    return () => { alive = false; };
  }, []);
  return summary;
}
