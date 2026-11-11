// Mises à jour : bandeau de l'accueil, pastille de la barre d'état, fenêtre de détail et section « À propos » des réglages.
// Rien n'est téléchargé ni installé sans un clic. Les notes de version sont du texte brut : jamais du HTML injecté.
import { useEffect, useRef } from "react";
import { Icon } from "../icons";
import { isBusy, useStore } from "../store";
import {
  aboutStatus, effectivePhase, formatDay, formatMoment, formatSize, installBlock, notesText,
  NOT_INSTALLABLE_MESSAGE, safeHttpsUrl, shouldShow, updateLabel,
} from "../update";
import { JobLine } from "./Library";
import "../styles/update.css";

/** Vrai quand il y a quelque chose à montrer : une mise à jour non ignorée, ou un parcours déjà commencé. */
function useAnnounced() {
  const info = useStore((s) => s.update);
  const skipped = useStore((s) => s.updateSkipped);
  const phase = useStore((s) => s.updatePhase);
  const job = useStore((s) => s.updateJob);
  const show = shouldShow(info, skipped, phase);
  return { info, phase, job, show, label: updateLabel(info, phase, job) };
}

/** Bandeau discret sous la barre de l'accueil. Pas de bouton plein : l'action principale est dans la fenêtre. */
export function UpdateBanner() {
  const { info, show, label } = useAnnounced();
  const hidden = useStore((s) => s.updateHidden);
  if (!show || hidden || !info) return null;
  const { openUpdate, hideUpdateBanner } = useStore.getState();
  return (
    <div className="banner update-banner" role="status" data-testid="update-banner">
      <Icon name="download" size={18} />
      <div><b>{label}</b><span className="muted"> · Tu as la {info.current}.</span></div>
      <button type="button" className="small-btn" onClick={() => openUpdate(true)}>Voir les nouveautés</button>
      <button type="button" className="icon-btn" title="Plus tard" aria-label="Masquer ce message" onClick={hideUpdateBanner}><Icon name="x" size={16} /></button>
    </div>
  );
}

/** Pastille de la barre d'état de l'éditeur : « Version 1.2.0 disponible ». Un clic ouvre la fenêtre. */
export function UpdateChip() {
  const { show, label } = useAnnounced();
  if (!show) return null;
  return (
    <button type="button" className="status-item update-chip" data-testid="update-chip" title="Une nouvelle version est disponible. Voir les nouveautés."
      onClick={() => useStore.getState().openUpdate(true)}>
      <span className="led info" />{label}
    </button>
  );
}

/** La fenêtre : notes de version, taille, et les actions. Montée une fois, à la racine de l'application. */
export function UpdateDialog() {
  const open = useStore((s) => s.updateOpen);
  const info = useStore((s) => s.update);
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && info && !d.open) d.showModal();
    else if ((!open || !info) && d.open) d.close();
  }, [open, info]);
  const close = () => useStore.getState().openUpdate(false);
  return (
    <dialog ref={ref} className="dlg update-dlg" data-testid="update-dialog" aria-labelledby="update-title"
      onCancel={(e) => { e.preventDefault(); close(); }} onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
      {open && info && <UpdateBody onClose={close} />}
    </dialog>
  );
}

function UpdateBody({ onClose }: { onClose: () => void }) {
  const info = useStore((s) => s.update)!;
  const stored = useStore((s) => s.updatePhase);
  const job = useStore((s) => s.updateJob);
  const error = useStore((s) => s.updateError);
  const busy = useStore((s) => isBusy(s));
  const phase = effectivePhase(info, stored);
  const st = useStore.getState();
  const size = formatSize(info.size);
  const when = formatDay(info.published_at);
  const notes = notesText(info.notes);
  const page = safeHttpsUrl(info.page_url);
  const block = installBlock(info, busy);
  const working = phase === "downloading" || phase === "installing";
  return (
    <div className="update-body">
      <header>
        <h2 id="update-title">Mise à jour disponible</h2>
        <button type="button" className="icon-btn" aria-label="Fermer" onClick={onClose}><Icon name="x" size={16} /></button>
      </header>
      <p className="update-meta">
        <b>Version {info.latest}</b>
        <span className="muted"> · tu as la {info.current}{when ? ` · publiée le ${when}` : ""}{size ? ` · ${size}` : ""}</span>
      </p>
      {/* Texte brut : React échappe tout, les retours à la ligne viennent de white-space: pre-wrap. */}
      <div className="update-notes" tabIndex={0} role="region" aria-label="Notes de version" data-testid="update-notes">
        {notes || "Pas de notes pour cette version."}
      </div>

      {!info.installable && (
        <p className="update-note" role="note">
          {NOT_INSTALLABLE_MESSAGE}
          {page && <> Tu peux la récupérer sur sa page : <a href={page} target="_blank" rel="noopener noreferrer">ouvrir la page de la version</a>.</>}
        </p>
      )}
      {info.installable && phase === "idle" && (
        <p className="update-note">Rien ne se télécharge avant ton clic{size ? ` (${size})` : ""}. Ensuite, l'application se ferme puis se relance pour installer.</p>
      )}
      {phase === "downloading" && (
        <div role="status" className="update-progress">
          <JobLine job={job} />
          {!job && <p className="update-note">Préparation du téléchargement…</p>}
          <button type="button" className="small-btn" onClick={() => st.cancelUpdateDownload()}>Annuler le téléchargement</button>
        </div>
      )}
      {phase === "ready" && (
        <p className="update-note" role="note">L'application va se fermer puis se relancer pour installer la version {info.latest}. Tes retouches en cours sont enregistrées avant.</p>
      )}
      {phase === "installing" && <p className="update-note" role="status">Fermeture de l'application… Elle se relance toute seule une fois la mise à jour installée.</p>}
      {error && <p className="warn small" role="alert">{error}</p>}

      <footer>
        <button type="button" className="link" disabled={working} onClick={() => st.skipUpdate()}>Ignorer cette version</button>
        <button type="button" onClick={onClose}>Plus tard</button>
        {info.installable && phase === "idle" && (
          <button type="button" className="primary" onClick={() => st.startUpdateDownload()}><Icon name="download" size={16} />Mettre à jour</button>
        )}
        {phase === "ready" && (
          <button type="button" className="primary" disabled={!!block} onClick={() => st.installUpdate()}><Icon name="refresh" size={16} />Installer et relancer</button>
        )}
      </footer>
      {phase === "ready" && block && <p className="why-off">{block}</p>}
    </div>
  );
}

/** Section « À propos » des réglages : version installée, vérification à la demande, vérification au lancement. */
export function AboutSection({ checkOnLaunch, onCheckOnLaunch }: { checkOnLaunch: boolean; onCheckOnLaunch: (v: boolean) => void }) {
  const info = useStore((s) => s.update);
  const checking = useStore((s) => s.updateChecking);
  useEffect(() => { useStore.getState().checkUpdate({ quiet: true }); }, []);   // version installée (réponse gardée par le serveur)
  const status = aboutStatus(info, checking);
  const check = async () => {
    const st = useStore.getState();
    const fresh = await st.checkUpdate({ force: true });
    if (fresh?.available) st.clearSkippedUpdate();   // il l'a redemandé : on la montre, même si elle était ignorée
  };
  const checkedAt = formatMoment(info?.checked_at);
  return (
    <div data-testid="update-about">
      <dl className="about-list">
        <dt>Version installée</dt>
        <dd>{info?.current ?? "…"}</dd>
      </dl>
      <div className="row-btns">
        <button type="button" className="small-btn" disabled={checking} onClick={check}><Icon name="refresh" size={14} />{checking ? "Vérification…" : "Vérifier les mises à jour"}</button>
        {status.text && <span className={`small about-status ${status.tone}`} role="status">{status.tone === "ok" && <Icon name="check" size={14} strokeWidth={3} />}{status.text}</span>}
        {status.tone === "update" && !checking && (
          <button type="button" className="small-btn" onClick={() => useStore.getState().openUpdate(true)}>Voir les nouveautés</button>
        )}
      </div>
      {checkedAt && <p className="muted small" style={{ margin: "0 0 var(--s-2)" }}>Dernière vérification : {checkedAt}.</p>}
      <label className="check">
        <input type="checkbox" className="switch" checked={checkOnLaunch} onChange={(e) => onCheckOnLaunch(e.target.checked)} />
        Vérifier au lancement
      </label>
      <p className="muted small" style={{ margin: 0 }}>
        L'application demande seulement à GitHub quelle est la dernière version publiée ; rien de ton poste n'est envoyé.
        Rien n'est téléchargé ni installé sans ton clic.
      </p>
    </div>
  );
}
