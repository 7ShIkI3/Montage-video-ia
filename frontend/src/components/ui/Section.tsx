// Section repliable d'un panneau (inspecteur, modules de gauche) : un <details> au titre en capitales discrètes.
import type { ReactNode } from "react";

export function Section({ title, defaultOpen, children }: { title: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  return (
    <details className="sec" open={defaultOpen}>
      <summary>{title}</summary>
      <div className="sec-body">{children}</div>
    </details>
  );
}
