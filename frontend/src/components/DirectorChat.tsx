// Chat avec le réalisateur IA : il lit le dossier des rushes (coups, combos, écrans rouges, phrases horodatées,
// montage actuel) et répond ; quand on lui demande un changement, il l'applique au montage (annulable).
import { useEffect, useRef, useState } from "react";
import { api, ChatMessage } from "../api";
import { CapHint } from "../caps";
import { Icon } from "../icons";
import { isBusy, jobOf, useStore } from "../store";
import { ask } from "./Dialog";
import { renderLite } from "./Research";

const EXAMPLES = ["Quels combos ai-je gagnés ?", "Garde seulement le meilleur combat", "Fais une version de 30 secondes",
  "Passe en vertical", "Qu'est-ce qui manque pour un bon short ?"];

export function DirectorChat() {
  const project = useStore((s) => s.project);
  const caps = useStore((s) => s.caps);
  const busy = useStore((s) => isBusy(s));
  const job = useStore((s) => jobOf(s, "chat"));
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);  // message envoyé, affiché avant la réponse
  const [dossier, setDossier] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const n = project?.chat?.length ?? 0;
  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight }); }, [n, busy]);
  if (!project) return null;
  const messages: ChatMessage[] = project.chat ?? [];
  const running = busy && job?.status !== "termine" && job?.status !== "erreur" && job?.status !== "annulee" && job?.kind === "chat";

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
          // Un changement appliqué par le réalisateur s'annule comme une retouche manuelle (Ctrl+Z).
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

  return (
    <section className="panel chat" data-testid="director-chat">
      <header>
        <h3>Réalisateur</h3>
        <span className="spacer" />
        <button className="link small" onClick={showDossier} title="Ce que le réalisateur sait de tes rushes">{dossier !== null ? "Masquer le dossier" : "Ce qu'il sait"}</button>
        {messages.length > 0 && <button className="icon-btn" title="Effacer la conversation" aria-label="Effacer la conversation" onClick={clear}><Icon name="trash" size={14} /></button>}
      </header>
      {dossier !== null && <pre className="chat-dossier small">{dossier}</pre>}
      <div className="chat-list" ref={list}>
        {!messages.length && (
          <p className="muted small">Pose-lui une question sur tes rushes (il a les coups, les combos, les écrans rouges, les phrases horodatées et le montage) ou demande un changement : il l'applique.</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}`}>
            <div className="chat-bubble">
              {m.role === "realisateur" ? renderLite(m.text) : <p>{m.text}</p>}
              {m.consigne && (
                <span className={`chat-action ${m.applied ? "ok" : ""}`} title={m.consigne}>
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
          <div className="chat-msg realisateur"><div className="chat-bubble typing"><span className="muted small">{job.message || "…"}</span></div></div>
        )}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={2} value={text} placeholder="Parle au réalisateur : une question sur tes rushes, ou un changement à faire…" disabled={busy}
          aria-label="Message au réalisateur" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="primary" disabled={busy || !text.trim()}>{running ? "…" : <><Icon name="message" size={14} />Envoyer</>}</button>
      </form>
      <CapHint reason={caps && !caps.claude ? "Sans clé Claude (Réglages), le réalisateur ne discute pas : il applique les consignes simples (durée, format, sous-titres, musique, zooms, couper un passage)." : null} />
      <div className="chips">
        {EXAMPLES.map((x) => <button key={x} className="chip" disabled={busy} onClick={() => setText(x)}>{x}</button>)}
      </div>
    </section>
  );
}
