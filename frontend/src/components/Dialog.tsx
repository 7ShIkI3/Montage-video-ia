// Dialogues maison (à la place de confirm() et prompt() natifs) : `ask.confirm(...)`, `ask.prompt(...)`.
// Un seul hôte `<DialogHost />` monté à la racine ; les raccourcis de l'éditeur s'arrêtent tant qu'il est ouvert.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Icon } from "../icons";

export interface ConfirmOpts { title?: string; ok?: string; cancel?: string; danger?: boolean }
export interface PromptOpts { title?: string; ok?: string; cancel?: string; placeholder?: string }
type Pending =
  | { kind: "confirm"; message: string; opts: ConfirmOpts; resolve: (v: boolean) => void }
  | { kind: "prompt"; message: string; value: string; opts: PromptOpts; resolve: (v: string | null) => void };

let pending: Pending | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const get = () => pending;

export const ask = {
  /** Vrai si l'utilisateur confirme. */
  confirm: (message: string, opts: ConfirmOpts = {}) => new Promise<boolean>((resolve) => {
    pending?.resolve(pending.kind === "confirm" ? false as never : null as never);
    pending = { kind: "confirm", message, opts, resolve };
    emit();
  }),
  /** La valeur saisie, ou null si annulé. */
  prompt: (message: string, value = "", opts: PromptOpts = {}) => new Promise<string | null>((resolve) => {
    pending?.resolve(pending.kind === "confirm" ? false as never : null as never);
    pending = { kind: "prompt", message, value, opts, resolve };
    emit();
  }),
};

/** Vrai quand un dialogue est ouvert (les raccourcis clavier s'en servent pour se taire). */
export const dialogOpen = () => pending !== null || (typeof document !== "undefined" && !!document.querySelector("dialog.update-dlg[open]"));

export function DialogHost() {
  const p = useSyncExternalStore(subscribe, get, get);
  const ref = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const submit = useRef<HTMLButtonElement>(null);
  const [value, setValue] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (p && !d.open) {
      d.showModal();
      // Le clavier va au champ de saisie, ou au bouton d'action (Entrée confirme, Échap annule).
      if (p.kind === "prompt") { setValue(p.value); setTimeout(() => { input.current?.focus(); input.current?.select(); }, 0); }
      else setTimeout(() => submit.current?.focus(), 0);
    } else if (!p && d.open) {
      d.close();
    }
  }, [p]);
  if (!p) return <dialog ref={ref} className="dlg" />;
  const done = (ok: boolean) => {
    const cur = pending;
    pending = null;
    emit();
    if (!cur) return;
    if (cur.kind === "confirm") cur.resolve(ok);
    else cur.resolve(ok ? value : null);
  };
  const title = p.opts.title ?? (p.kind === "confirm" ? "Confirmer" : "Saisir");
  return (
    <dialog ref={ref} className="dlg" onCancel={(e) => { e.preventDefault(); done(false); }}
      onClick={(e) => { if (e.target === e.currentTarget) done(false); }}>
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); done(true); }}>
        <header><h2>{title}</h2><button type="button" className="icon-btn" aria-label="Fermer" onClick={() => done(false)}><Icon name="x" size={16} /></button></header>
        <p>{p.message}</p>
        {p.kind === "prompt" && (
          <input ref={input} value={value} placeholder={p.opts.placeholder} aria-label={p.message} onChange={(e) => setValue(e.target.value)} />
        )}
        <footer>
          <button type="button" onClick={() => done(false)}>{p.opts.cancel ?? "Annuler"}</button>
          <button ref={submit} type="submit" className={p.kind === "confirm" && p.opts.danger ? "danger" : "primary"}>
            {p.opts.ok ?? (p.kind === "confirm" ? "Confirmer" : "Valider")}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
