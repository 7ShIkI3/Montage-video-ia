// Ce que l'installation permet (clé Claude, voix) : on désactive ce qui ne peut pas marcher, avec la raison.
import type { ReactNode } from "react";
import type { Health } from "./api";

/** Raison pour laquelle une fonction qui exige Claude est indisponible (null si elle l'est, ou si l'état est encore inconnu). */
export function needClaude(caps: Health | null): string | null {
  return caps && !caps.claude ? "Nécessite une clé Claude : ajoute-la dans Réglages" : null;
}

/** Raison pour laquelle aucune voix de synthèse n'est disponible dans cette langue. */
export function needVoice(caps: Health | null, lang: string): string | null {
  return caps && !caps.tts?.[lang] ? "Aucune voix installée pour cette langue" : null;
}

/** Explication discrète, avec un lien vers les réglages. */
export function CapHint({ reason }: { reason: string | null }) {
  if (!reason) return null;
  return <p className="cap-hint small">{reason} · <a href="#/reglages">Régler</a></p>;
}

/** Désactive tout son contenu (boutons, listes, champs) tant qu'une raison l'exige, et l'explique. */
export function Gate({ reason, children }: { reason: string | null; children: ReactNode }) {
  if (!reason) return <>{children}</>;
  return (
    <>
      <fieldset className="gated" disabled title={reason}>{children}</fieldset>
      <CapHint reason={reason} />
    </>
  );
}
