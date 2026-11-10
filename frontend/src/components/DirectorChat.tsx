// Chat avec le réalisateur IA : un agent qui lit le dossier des rushes, REGARDE les images quand il en a besoin
// (outil voir_images), cherche sur le web, et modifie le montage avec des outils précis (annulable).
// Sa réponse s'écrit au fil de l'eau ; chaque message garde la liste de ce qu'il a fait.
import { useEffect, useRef, useState } from "react";
import { api, ChatMessage, VisionEstimate } from "../api";
import { CapHint } from "../caps";
import { Icon } from "../icons";
import { isBusy, jobOf, useStore } from "../store";
import { ask } from "./Dialog";
import { renderLite } from "./Research";

const EXAMPLES = ["Quels combats ai-je gagnés ? Regarde les images.", "Refais le montage autour de mes meilleurs kills",
  "Pose un repère à chaque élimination", "Fais une version de 30 secondes", "Passe en vertical"];

/** `visible` : faux quand la vue est masquée (sous-onglet Commentaires) ; le fil se recale en bas au retour. */
export function DirectorChat({ visible = true }: { visible?: boolean }) {
  const project = useStore((s) => s.project);
  const caps = useStore((s) => s.caps);
  const busy = useStore((s) => isBusy(s));
  const job = useStore((s) => jobOf(s, "chat"));
  const visionJob = useStore((s) => jobOf(s, "vision"));
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);  // message envoyé, affiché avant la réponse
  const [dossier, setDossier] = useState<string | null>(null);
  const [vision, setVision] = useState<VisionEstimate | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const n = project?.chat?.length ?? 0;
  const pid = project?.id;
  const mediaCount = project?.media.length ?? 0;
  const active = (j: typeof job) => !!j && (j.status === "en_cours" || j.status === "en_attente");
  const running = active(job);
  const looking = active(visionJob);
  useEffect(() => { if (visible) list.current?.scrollTo({ top: list.current.scrollHeight }); }, [visible, n, busy, job?.preview, job?.message]);
  // Combien d'images une passe de vision enverrait : relu quand les rushes changent ou qu'une passe finit.
  useEffect(() => {
    if (!pid) return;
    api.visionEstimate(pid).then(setVision).catch(() => setVision(null));
  }, [pid, mediaCount, looking]);
  if (!project) return null;
  const messages: ChatMessage[] = project.chat ?? [];

  const send = async (t: string) => {
    const msg = t.trim();
    if (!msg || busy) return;
    const before = useStore.getState().timeline;
    setText("");
    setPending(msg);
    try {
      await useStore.getState().runJob("Réalisateur", () => api.chat(project.id, msg), {
        timeline: true,
        onDone: (done) => {
          const r = done.result as { applied?: boolean; message?: ChatMessage } | null;
          // Un changement fait par le réalisateur s'annule comme une retouche manuelle (Ctrl+Z).
          if (r?.applied && before) useStore.setState((s) => ({ past: [...s.past, before], future: [] }));
        },
      });
    } finally {
      setPending(null);
    }
  };
  const clear = async () => {
    if (!await ask.confirm("Effacer la conversation avec le réalisateur ? Le montage reste tel quel.", { title: "Effacer", ok: "Effacer", danger: true })) return;
    await useStore.getState().guard("Effacement impossible", async () => { await api.clearChat(project.id); await useStore.getState().refreshProject(); });
  };
  const showDossier = async () => {
    if (dossier !== null) return setDossier(null);
    await useStore.getState().guard("Dossier illisible", async () => setDossier(await api.dossier(project.id)));
  };
  const look = async () => {
    if (!vision || !vision.images) return;
    if (!await ask.confirm(
      `Claude va regarder ${vision.images} image${vision.images > 1 ? "s" : ""} de ${vision.pending} rush${vision.pending > 1 ? "es" : ""} (fins de combos, écrans rouges, changements de plan) et noter ce qu'il voit. C'est un appel payant à l'API Claude, fait une seule fois par rush.`,
      { title: "Faire regarder les rushes", ok: "Regarder" })) return;
    await useStore.getState().runJob("Analyse des images", () => api.vision(project.id), {
      onDone: (j) => {
        const r = j.result as { observations?: number } | null;
        useStore.getState().notify(`Rushes regardés : ${r?.observations ?? 0} observations ajoutées au dossier.`, { level: "ok" });
        setDossier(null);
      },
    });
  };
  const seen = vision ? vision.media.filter((m) => m.done).length : 0;

  return (
    <section className="panel chat" data-testid="director-chat">
      <div className="chat-tools">
        <button className="link small" onClick={showDossier} aria-expanded={dossier !== null}>
          <Icon name={dossier !== null ? "eye-off" : "eye"} size={13} />{dossier !== null ? "Masquer le dossier" : "Ce qu'il sait de tes rushes"}
        </button>
        <span className="spacer" />
        {messages.length > 0 && <button className="icon-btn" title="Effacer la conversation" aria-label="Effacer la conversation" onClick={clear}><Icon name="trash" size={14} /></button>}
      </div>
      {vision && vision.claude && vision.media.length > 0 && (
        <div className="chat-vision" data-testid="chat-vision">
          <Icon name={vision.pending ? "eye-off" : "eye"} size={14} />
          <span className="small">
            {looking ? (visionJob?.message || "Claude regarde les rushes…")
              : vision.pending ? `${seen}/${vision.media.length} rush${vision.media.length > 1 ? "es" : ""} regardé${seen > 1 ? "s" : ""} par Claude. Il peut déjà regarder à la demande ; une passe complète lui donne une vue d'ensemble.`
              : "Claude a regardé tous les rushes : ses observations sont dans le dossier."}
          </span>
          {vision.pending > 0 && !looking && (
            <button className="small-btn" disabled={busy} onClick={look}>Regarder ({vision.images} images)</button>
          )}
        </div>
      )}
      {dossier !== null && <pre className="chat-dossier small">{dossier}</pre>}
      <div className="chat-list" ref={list}>
        {!messages.length && (
          <p className="muted small">Pose-lui une question sur tes rushes ou demande un changement. Il a les mesures de l'app (coups, combos, écrans rouges, paroles), il va voir les images quand la réponse en dépend, et il modifie le montage lui-même.</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}`}>
            <div className="chat-bubble">
              {m.role === "realisateur" ? renderLite(m.text) : <p>{m.text}</p>}
              {m.sources && m.sources.length > 0 && (
                <ul className="chat-sources">
                  {m.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer" title={s.url}>{s.title || s.url}</a></li>)}
                </ul>
              )}
              {m.actions && m.actions.length > 0 && (
                <ul className="chat-actions" aria-label="Ce que le réalisateur a fait">
                  {m.actions.map((a, i) => <li key={i}><Icon name={/regardé|lu les/.test(a) ? "eye" : /web/.test(a) ? "globe" : "check"} size={11} />{a}</li>)}
                </ul>
              )}
              {(m.applied || m.consigne) && (
                <span className={`chat-action ${m.applied ? "ok" : ""}`} title={m.consigne ?? "Annulable par Ctrl+Z"}>
                  <Icon name={m.applied ? "check" : "clock"} size={12} />{m.applied ? "Montage modifié" : "Consigne non appliquée"}
                </span>
              )}
            </div>
            <span className="chat-meta muted small">{m.role === "monteur" ? "Toi" : m.by === "claude" ? "Réalisateur · Claude" : "Réalisateur · local"}</span>
          </div>
        ))}
        {pending && !messages.some((m) => m.role === "monteur" && m.text === pending && m.created_at * 1000 > Date.now() - 120000) && (
          <div className="chat-msg monteur"><div className="chat-bubble"><p>{pending}</p></div><span className="chat-meta muted small">Toi</span></div>
        )}
        {running && job && (
          <div className="chat-msg realisateur" data-testid="chat-live">
            <div className="chat-bubble">
              {job.preview ? renderLite(job.preview) : null}
              <span className="chat-step typing muted small">{job.message || "…"}</span>
            </div>
          </div>
        )}
      </div>
      <CapHint reason={caps && !caps.claude ? "Sans clé Claude (Réglages), le réalisateur ne discute pas et ne regarde pas les images : il applique les consignes simples (durée, format, sous-titres, musique, zooms, couper un passage)." : null} />
      <div className="chips chat-examples">
        {EXAMPLES.map((x) => <button key={x} className="chip" disabled={busy} onClick={() => setText(x)}>{x}</button>)}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={2} value={text} placeholder="Parle au réalisateur : une question sur tes rushes, ou un changement à faire…" disabled={busy}
          aria-label="Message au réalisateur" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="primary" disabled={busy || !text.trim()}>{running ? "…" : <><Icon name="message" size={14} />Envoyer</>}</button>
      </form>
    </section>
  );
}
