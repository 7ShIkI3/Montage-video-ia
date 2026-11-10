// Module Médias : le projet en grille de cartes, l'import (fichiers, glisser-déposer, lien) et la bibliothèque musicale.
import { useEffect, useRef, useState } from "react";
import { api, fmtTime, Media, Project } from "../../api";
import { Icon } from "../../icons";
import { loadStrip, peekStrip, Strip } from "../../lib/filmstrip";
import { isBusy, MEDIA_DRAG_TYPE, useStore } from "../../store";
import { ask } from "../Dialog";
import { MusicLibraryButton, UrlImport } from "../Library";
import { EmptyHint } from "../ui/EmptyHint";
import { ModuleFrame } from "./ModuleFrame";

type Sub = "projet" | "importer" | "bibliotheque";

/** Le défilement au survol demande une souris et du mouvement autorisé. */
function canScrub(): boolean {
  if (typeof matchMedia !== "function") return false;
  return matchMedia("(hover: hover)").matches && !matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Une carte de rush : vignette 16:9, durée, coche « analysé », actions au survol, pellicule qui défile sous la souris. */
function MediaCard({ project, m }: { project: Project; m: Media }) {
  const open = useStore((s) => s.sourceMedia === m.id);
  const [strip, setStrip] = useState<Strip | null>(() => peekStrip(project.id, m.id));
  const [k, setK] = useState<number | null>(null);  // tuile montrée sous la souris ; null = la vignette normale

  const enter = () => {
    if (!m.has_video || !canScrub()) return;
    // Même cache et même file que la timeline : pas de nouvel appel si la pellicule est déjà là.
    loadStrip(project.id, m.id).then(setStrip);
  };
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!strip || !canScrub()) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(0.9999, (e.clientX - r.left) / Math.max(1, r.width)));
    setK(Math.floor(x * strip.count));
  };
  const remove = async (e: React.MouseEvent) => {
    e.stopPropagation();  // ne pas ouvrir le moniteur source
    if (!await ask.confirm(`Retirer ${m.filename} du projet ?`, { title: "Retirer le média", ok: "Retirer", danger: true })) return;
    const st = useStore.getState();
    await st.guard("Retrait impossible", async () => {
      await st.saveNow();
      await api.deleteMedia(project.id, m.id);
      await st.refreshProject({ timeline: true });
    });
  };

  let scrub: React.CSSProperties | null = null;
  if (strip && k !== null) {
    const rows = Math.ceil(strip.count / strip.cols);
    const col = k % strip.cols;
    const row = Math.floor(k / strip.cols);
    scrub = {
      backgroundImage: `url(${strip.url})`,
      backgroundSize: `${strip.cols * 100}% ${rows * 100}%`,
      backgroundPosition: `${strip.cols > 1 ? (col / (strip.cols - 1)) * 100 : 0}% ${rows > 1 ? (row / (rows - 1)) * 100 : 0}%`,
    };
  }
  return (
    <li className={`media-card ${open ? "open" : ""}`} draggable tabIndex={0} data-media={m.id}
      title="Clic : ouvrir dans le moniteur source · glisser sur la piste vidéo : poser le rush (ou sa portion marquée)"
      onClick={() => useStore.getState().openSource(m.id)}
      onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) useStore.getState().openSource(m.id); }}
      onDragStart={(e) => {
        e.dataTransfer.setData(MEDIA_DRAG_TYPE, m.id);
        e.dataTransfer.effectAllowed = "copy";
        document.body.classList.add("dragging-media");
      }}
      onDragEnd={() => document.body.classList.remove("dragging-media")}>
      <div className="mc-thumb" onPointerEnter={enter} onPointerMove={move} onPointerLeave={() => setK(null)}>
        {m.proxy ? <img src={api.thumbUrl(project.id, m.id)} alt="" draggable={false} /> : <Icon name={m.has_video ? "film" : "mic"} size={22} />}
        {scrub && <div className="mc-scrub" style={scrub} aria-hidden="true" />}
        {m.analyzed && <span className="mc-ok" role="img" aria-label="Analysé" title="Analysé" />}
        <span className="mc-dur">{fmtTime(m.duration)}</span>
        <div className="mc-acts">
          {m.has_video && (
            <>
              <button type="button" className="icon-btn" title="Voir dans le moniteur source" aria-label={`Voir ${m.filename}`}
                onClick={(e) => { e.stopPropagation(); useStore.getState().openSource(m.id); }}><Icon name="eye" size={14} /></button>
              <button type="button" className="icon-btn" title="Ajouter à la fin du montage" aria-label={`Ajouter ${m.filename} à la fin`}
                onClick={(e) => { e.stopPropagation(); useStore.getState().insertClip(m.id, 0, m.duration, "end"); }}><Icon name="plus" size={14} /></button>
            </>
          )}
          <button type="button" className="icon-btn" title="Retirer du projet" aria-label={`Retirer ${m.filename}`} onClick={remove}><Icon name="trash" size={14} /></button>
        </div>
      </div>
      <div className="mc-name" title={m.filename}>{m.filename}</div>
      <div className="mc-sub">{m.width ? `${m.width}×${m.height}` : "audio"}</div>
    </li>
  );
}

function MediaGrid({ project, onImport }: { project: Project; onImport: () => void }) {
  if (!project.media.length) {
    return <EmptyHint icon="film" text="Aucune vidéo dans ce projet. Importe tes rushes pour commencer." action={{ label: "Importer des vidéos", onClick: onImport }} />;
  }
  return <ul className="media-grid">{project.media.map((m) => <MediaCard key={m.id} project={project} m={m} />)}</ul>;
}

export function MediaModule() {
  const project = useStore((s) => s.project);
  const downloading = useStore((s) => isBusy(s, "telechargement"));
  const tl = useStore((s) => s.timeline);
  const [picked, setPicked] = useState<Sub | null>(null);
  const [upload, setUpload] = useState<number | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // Un glisser de rush dont la carte disparaît (rechargement du projet, changement de module) n'envoie pas toujours `dragend` :
  // on retire la classe de la piste vidéo à la main.
  useEffect(() => {
    const clear = () => document.body.classList.remove("dragging-media");
    window.addEventListener("dragend", clear, true);
    window.addEventListener("drop", clear, true);
    return () => { window.removeEventListener("dragend", clear, true); window.removeEventListener("drop", clear, true); clear(); };
  }, []);

  if (!project) return null;
  // Projet vide : on ouvre sur « Importer » tant que l'utilisateur n'a rien choisi ; sinon sur « Projet ».
  const sub: Sub = picked ?? (project.media.length ? "projet" : "importer");

  const send = async (files: File[]) => {
    if (!files.length) return;
    const { notify } = useStore.getState();
    setUpload(0);
    try {
      await api.uploadMedia(project.id, files, setUpload);
      await useStore.getState().refreshProject();
      setPicked("projet");
    } catch (e) {
      notify(`Import impossible : ${(e as Error).message}`);
    } finally {
      setUpload(null);
    }
  };

  return (
    <ModuleFrame module="medias" active={sub} onSelect={(id) => setPicked(id as Sub)}>
      <div className={`lw-media ${over ? "over" : ""}`}
        onDragOver={(e) => { if (!e.dataTransfer.types.includes("Files")) return; e.preventDefault(); setOver(true); }}  // un rush du chutier qu'on glisse n'est pas un import
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false); }}
        onDrop={(e) => { if (!e.dataTransfer.files.length) return; e.preventDefault(); setOver(false); send([...e.dataTransfer.files]); }}>
        <input ref={input} type="file" multiple accept="video/*,audio/*" hidden
          onChange={(e) => { send([...(e.target.files ?? [])]); e.target.value = ""; }} />
        {upload !== null && (
          <div className="progress" role="progressbar" aria-valuenow={Math.round(upload * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${upload * 100}%` }} /><span>Envoi {Math.round(upload * 100)} %</span>
          </div>
        )}
        {sub === "projet" && <MediaGrid project={project} onImport={() => setPicked("importer")} />}
        {sub === "importer" && (
          <div className="lw-import">
            <button type="button" className="lw-import-btn" disabled={upload !== null} onClick={() => input.current?.click()}>
              <Icon name="upload" size={16} strokeWidth={2} />Importer des vidéos
            </button>
            <div className="lw-drop" role="button" tabIndex={0} onClick={() => input.current?.click()}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}>
              <Icon name="upload" size={22} />
              <span>Glisse tes vidéos ici</span>
              <span className="muted small">MP4, MOV, MKV, WEBM… plusieurs fichiers possibles</span>
            </div>
            <div className="fx-title">Depuis un lien</div>
            <UrlImport placeholder="Lien d'une vidéo (YouTube, Vimeo, fichier…)" busy={downloading}
              onSubmit={(url) => useStore.getState().runJob("Téléchargement", () => api.importUrl(project.id, url), {
                onDone: (j) => {
                  useStore.getState().notify(`Importé : ${(j.result as { filename?: string })?.filename ?? "vidéo"}`, { level: "ok" });
                  setPicked("projet");
                },
              })} />
          </div>
        )}
        {sub === "bibliotheque" && (
          <div className="lw-import">
            <p className="muted small">Tes musiques, les musiques libres de droits et le son d'un lien. Le morceau choisi devient la musique de fond du montage.</p>
            {tl ? (
              <>
                <p className="lw-now">Musique actuelle : <b>{tl.music.track ? tl.music.track.split(/[\\/]/).pop() : "aucune"}</b></p>
                <MusicLibraryButton label="Ouvrir la bibliothèque musicale" className="small-btn lw-wide-btn" />
              </>
            ) : <p className="muted small">Le montage se charge…</p>}
          </div>
        )}
      </div>
    </ModuleFrame>
  );
}
