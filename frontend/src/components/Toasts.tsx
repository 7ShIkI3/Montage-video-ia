// Messages empilés en bas à droite : info/ok partent seuls après 5 s, une erreur reste jusqu'au clic.
import { useEffect } from "react";
import { Toast, useStore } from "../store";
import { Icon } from "../icons";

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useStore((s) => s.dismiss);
  useEffect(() => {
    if (toast.level === "erreur") return;
    const t = window.setTimeout(() => dismiss(toast.id), 5000);
    return () => window.clearTimeout(t);
  }, [toast.id, toast.level, dismiss]);
  return (
    <div className={`toast ${toast.level}`} role={toast.level === "erreur" ? "alert" : "status"}>
      <span className="toast-text">{toast.text}</span>
      {toast.action && (
        <button className="small-btn" onClick={() => { toast.action!.run(); dismiss(toast.id); }}>{toast.action.label}</button>
      )}
      <button className="icon-btn toast-x" title="Fermer" aria-label="Fermer" onClick={() => dismiss(toast.id)}><Icon name="x" size={14} /></button>
    </div>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  if (!toasts.length) return null;
  return <div className="toasts">{toasts.map((t) => <ToastItem key={t.id} toast={t} />)}</div>;
}
