// Barre du haut de l'éditeur : retour aux projets, nom et état d'enregistrement, « Monter avec l'IA » au centre,
// puis tâches, palette, réglages et export. Au plus un bouton plein ici : `data-step` (posé sur .editor) choisit lequel.
import { useEffect, useState } from "react";
import { api } from "../../api";
import { Icon } from "../../icons";
import { isBusy, useStore } from "../../store";
import { LIVE_KINDS } from "../../stages";
import { Tasks } from "../Tasks";

/** Libellé du bouton pendant une tâche suivie en direct. */
const LIVE_LABEL: Record<string, string> = { montage: "Montage", demo: "Montage", directions: "Directions", shorts: "Shorts" };

/** Anneau de progression (0 à 1). */
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

function AiButton() {
  const job = useStore((s) => s.job);
  const live = !!job && LIVE_KINDS.includes(job.kind) && (job.status === "en_cours" || job.status === "en_attente");
  if (live) {
    const pct = Math.round(job.progress * 100);
    const label = `${LIVE_LABEL[job.kind] ?? "Montage"} ${pct} %`;
    return (
      <button type="button" className="btn-ai live" title="Voir la réalisation en cours" aria-label={label}
        onClick={() => useStore.getState().openLeft(job.kind === "shorts" ? "versions" : "ia")}>
        <Ring value={job.progress} />{label}
      </button>
    );
  }
  return (
    <button type="button" className="btn-ai" title="Ouvrir le montage automatique (aucun appel n'est lancé)" onClick={() => useStore.getState().openLeft("ia")}>
      <Icon name="sparkles" size={16} />Monter avec l'IA
    </button>
  );
}

function ExportButton() {
  const project = useStore((s) => s.project);
  const clips = useStore((s) => (s.timeline ?? s.project?.timeline)?.clips.length ?? 0);
  const busy = useStore((s) => isBusy(s, "export"));
  if (!project) return null;
  const empty = clips === 0;
  return (
    <>
      {empty && <span id="export-why" className="export-why">Aucun plan à exporter</span>}
      <button type="button" className="btn-export" onClick={() => useStore.getState().setExportOpen(true)} disabled={empty}
        aria-describedby={empty ? "export-why" : undefined} title="Exporter la vidéo (onglet Export)">
        <Icon name="download" size={16} strokeWidth={2.2} />{busy ? "Rendu…" : "Exporter"}
      </button>
    </>
  );
}

export function TopBar({ onBack }: { onBack: () => void }) {
  const project = useStore((s) => s.project);
  const dirty = useStore((s) => s.dirty);
  const [name, setName] = useState("");
  useEffect(() => { if (project) setName(project.name); }, [project?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const rename = () => {
    if (!project || name === project.name) return;
    useStore.getState().guard("Renommage impossible", async () => useStore.getState().setProject(await api.patchProject(project.id, { name })));
  };
  const back = async () => {
    try { await useStore.getState().saveNow(); } catch (e) { return useStore.getState().notify(`Sauvegarde impossible : ${(e as Error).message}`); }
    onBack();
  };
  return (
    <header className="topbar">
      <div className="tb-left">
        <button type="button" className="ghost tb-back" onClick={back} title="Retour aux projets" aria-label="Retour aux projets">
          <Icon name="chevron-left" size={18} />Projets
        </button>
        <span className="brand-mark" aria-hidden><Icon name="play" size={11} /></span>
        <input className="project-name" value={name} aria-label="Nom du projet" onChange={(e) => setName(e.target.value)}
          onBlur={rename} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
        <span className={`saved ${dirty ? "dirty" : ""}`}><Icon name={dirty ? "clock" : "check"} size={12} strokeWidth={3} />{dirty ? "Enregistrement…" : "Enregistré"}</span>
      </div>
      <div className="tb-center"><AiButton /></div>
      <div className="tb-right">
        <Tasks />
        <button type="button" className="cmdk-pill" title="Palette de commandes (Ctrl+K)" aria-label="Palette de commandes" onClick={() => useStore.getState().setPalette(true)}>
          <Icon name="search" size={16} />
          <span className="cmdk-text">Rechercher ou donner une consigne</span>
          <kbd className="kbd">Ctrl K</kbd>
        </button>
        <a href="#/reglages" className="icon-btn settings-link" title="Réglages" aria-label="Réglages"><Icon name="settings" size={18} /></a>
        <ExportButton />
      </div>
    </header>
  );
}
