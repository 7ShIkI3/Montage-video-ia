// Barre d'état : moteurs, position, zoom, espace de travail.
import { fmtTime } from "../../api";
import { Icon } from "../../icons";
import { useStore } from "../../store";

export function StatusBar() {
  const health = useStore((s) => s.caps);
  const playhead = useStore((s) => s.playhead);
  const pps = useStore((s) => s.pxPerSec);
  const range = useStore((s) => s.timeline?.range ?? null);
  const layout = useStore((s) => s.layout);
  const { setLayout, resetLayout } = useStore.getState();
  const device = health ? (health.whisper_device ? ` · ${health.whisper_device === "cuda" ? "GPU" : "CPU"}` : health.cuda ? " · GPU prêt" : "") : "";
  return (
    <footer className="statusbar">
      {health && (
        <a href="#/reglages" className={`status-item ${health.claude ? "" : "warn"}`} title={health.claude ? `Réalisateur : ${health.claude_model}. Ouvrir les réglages.` : "Sans clé Claude : consignes simples, moteur local. Ouvrir les réglages."}>
          <span className={`led ${health.claude ? "ok" : "warn"}`} />{health.claude ? health.claude_model : "Moteur local"}
        </a>
      )}
      {health && <span className="status-item" title={`Transcription (Whisper ${health.whisper_model})`}>Transcription {health.whisper_model}{device}</span>}
      {health && <span className="status-item" title="Encodeur vidéo">{health.encoder}</span>}
      <span className="spacer" />
      {range && <span className="status-item accent" title="Zone d'entrée/sortie">Zone {fmtTime(range.start, true)} → {fmtTime(range.end, true)}</span>}
      <span className="status-item mono" title="Position du curseur">{fmtTime(playhead, true)}</span>
      <span className="status-item" title="Zoom de la timeline">Zoom {Math.round(pps)} px/s</span>
      <span className="status-item panels">
        <button className={layout.leftOpen ? "on" : ""} title="Panneau de gauche" aria-label="Panneau de gauche" aria-pressed={layout.leftOpen} onClick={() => setLayout({ leftOpen: !layout.leftOpen })}><Icon name="panel-left" size={14} /></button>
        <button className={layout.rightOpen ? "on" : ""} title="Panneau de droite" aria-label="Panneau de droite" aria-pressed={layout.rightOpen} onClick={() => setLayout({ rightOpen: !layout.rightOpen })}><Icon name="panel-right" size={14} /></button>
        <button title="Espace de travail par défaut" aria-label="Espace de travail par défaut" onClick={resetLayout}><Icon name="layout" size={14} /></button>
      </span>
    </footer>
  );
}
