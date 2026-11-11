// Mises à jour : la logique pure (comparaison de versions, version ignorée, libellés, erreurs en clair).
// Aucun appel réseau ni React ici : tout se teste sans navigateur (tests/update.test.js).
import type { Job, UpdateInfo } from "./api";

/** Clé du navigateur qui retient la version que l'utilisateur ne veut plus se voir proposer. */
export const SKIP_KEY = "monteur.update.skip";
/** Les notes sont tronquées ici, comme côté serveur : un texte énorme ne doit pas figer la fenêtre. */
export const NOTES_MAX = 4000;

/** Où en est le parcours « mettre à jour ». */
export type UpdatePhase = "idle" | "downloading" | "ready" | "installing";

export const BUSY_MESSAGE = "Un export ou une autre tâche est en cours. Attends sa fin, puis lance l'installation.";
export const NOT_INSTALLABLE_MESSAGE = "Cette version ne s'installe pas depuis l'application (version de développement, ou installateur non publié).";

/** « v1.2.10 » → [1, 2, 10] ; null si ce n'est pas un numéro de version. Les suffixes (-beta, +build) sont ignorés. */
export function parseVersion(v: string | null | undefined): number[] | null {
  if (typeof v !== "string") return null;
  const core = v.trim().replace(/^v/i, "").split(/[-+]/)[0];
  if (!/^\d+(\.\d+)*$/.test(core)) return null;
  return core.split(".").map(Number);
}

/** Compare deux versions chiffre par chiffre (1.0.10 > 1.0.9, jamais en texte). -1, 0 ou 1 ; 0 si l'une est illisible. */
export function compareVersions(a: string, b: string): number {
  const x = parseVersion(a), y = parseVersion(b);
  if (!x || !y) return 0;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

/** Même version, quelle que soit l'écriture (« v1.2.0 » et « 1.2.0 »). */
export const sameVersion = (a: string | null | undefined, b: string | null | undefined): boolean =>
  !!a && !!b && (parseVersion(a) && parseVersion(b) ? compareVersions(a, b) === 0 : a.trim() === b.trim());

/** Version mémorisée comme ignorée (chaîne vide si rien, ou si le navigateur refuse le stockage). */
export function readSkipped(): string {
  try { return localStorage.getItem(SKIP_KEY) ?? ""; } catch { return ""; }
}
export function writeSkipped(version: string): void {
  try { if (version) localStorage.setItem(SKIP_KEY, version); else localStorage.removeItem(SKIP_KEY); } catch { /* navigation privée : non mémorisé */ }
}

/** Faut-il proposer cette mise à jour ? Oui si le serveur en voit une, plus récente, et que l'utilisateur ne l'a pas ignorée. */
export function announceable(info: UpdateInfo | null, skipped: string): boolean {
  if (!info || !info.available || !info.latest) return false;
  if (parseVersion(info.latest) && parseVersion(info.current) && compareVersions(info.latest, info.current) <= 0) return false;
  return !sameVersion(info.latest, skipped);
}

/** Faut-il montrer le bandeau et la pastille ? Un téléchargement ou une installation en cours, toujours ; sinon une
 *  mise à jour annonçable. « Prête à installer » ne suffit pas : une version ignorée disparaît même téléchargée. */
export function shouldShow(info: UpdateInfo | null, skipped: string, phase: UpdatePhase): boolean {
  return !!info && (phase === "downloading" || phase === "installing" || announceable(info, skipped));
}

/** Octets → « 1,2 Go » / « 85 Mo » / « 40 Ko » (virgule française). Chaîne vide si inconnu. */
export function formatSize(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !isFinite(bytes) || bytes <= 0) return "";
  const fr = (n: number) => n.toFixed(1).replace(".", ",");
  if (bytes >= 1e9) return `${fr(bytes / 1e9)} Go`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} Mo`;
  return `${Math.max(1, Math.round(bytes / 1e3))} Ko`;
}

/** Date ISO → « 3 décembre 2026 » ; vide si illisible. */
export function formatDay(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d.getTime()) ? d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "";
}
/** Date ISO → « 3 décembre 2026 à 14:05 » ; vide si illisible. */
export function formatMoment(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d.getTime()) ? `${formatDay(iso)} à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}` : "";
}

/** Markdown des notes → texte brut lisible : ni **gras**, ni `code`, ni # titres ; les puces « - » deviennent « • ». */
function plainMarkdown(text: string): string {
  return text
    .replace(/\[([^\]\n]+)\]\([^)\n]*\)/g, "$1")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/`/g, "")
    .replace(/^[ \t]*#{1,6}[ \t]+/gm, "")
    .replace(/^([ \t]*)[-*+][ \t]+/gm, "$1• ");
}

/** Notes de version en texte brut : retours à la ligne unifiés, Markdown retiré, tronquées. Jamais du HTML. */
export function notesText(notes: string | null | undefined): string {
  return typeof notes === "string" ? plainMarkdown(notes.replace(/\r\n?/g, "\n")).trim().slice(0, NOTES_MAX) : "";
}

/** Lien de la page de la release, seulement s'il est en https (rien de `javascript:` ni de `file:`). */
export function safeHttpsUrl(url: string | null | undefined): string | null {
  if (typeof url !== "string") return null;
  try { const u = new URL(url); return u.protocol === "https:" ? u.href : null; } catch { return null; }
}

/** Pourquoi on ne peut pas installer maintenant (null = on peut). */
export function installBlock(info: UpdateInfo | null, busy: boolean): string | null {
  if (!info || !info.available) return "Aucune mise à jour à installer.";
  if (!info.installable) return NOT_INSTALLABLE_MESSAGE;
  if (!info.downloaded) return "Télécharge d'abord la mise à jour.";
  if (busy) return BUSY_MESSAGE;
  return null;
}

/** Phase affichée : une mise à jour déjà téléchargée est « prête » même juste après un relancement de l'application. */
export function effectivePhase(info: UpdateInfo | null, phase: UpdatePhase): UpdatePhase {
  return phase === "idle" && info?.available && info.installable && info.downloaded ? "ready" : phase;
}

/** Texte de la pastille et du bandeau. */
export function updateLabel(info: UpdateInfo | null, phase: UpdatePhase, job: Job | null): string {
  if (!info?.latest) return "";
  if (phase === "downloading") return `Téléchargement de la version ${info.latest} : ${Math.round((job?.progress ?? 0) * 100)} %`;
  if (phase === "installing") return "Installation en cours…";
  if (effectivePhase(info, phase) === "ready") return `Version ${info.latest} prête à installer`;
  return `Version ${info.latest} disponible`;
}

/** Ce que dit la section « À propos » du résultat de la vérification. */
export function aboutStatus(info: UpdateInfo | null, checking: boolean): { tone: "busy" | "error" | "update" | "ok" | "idle"; text: string } {
  if (checking) return { tone: "busy", text: "Vérification en cours…" };
  if (!info) return { tone: "idle", text: "" };
  if (info.error) return { tone: "error", text: info.error };
  if (info.available && info.latest) return { tone: "update", text: `La version ${info.latest} est disponible.` };
  if (info.checked_at) return { tone: "ok", text: "Tu as la dernière version." };
  return { tone: "idle", text: "" };
}

/** Message d'erreur sans jargon : le texte français du serveur tel quel, sinon une phrase simple. */
export function friendlyError(e: unknown): string {
  const msg = e instanceof Error ? e.message : typeof e === "string" ? e : "";
  if (!msg || /failed to fetch|networkerror|load failed|network request failed/i.test(msg)) return "L'application ne répond pas. Réessaie dans un instant.";
  const http = /^(\d{3})\s/.exec(msg);   // « 500 Internal Server Error » : pas de detail du serveur
  if (http) return `Le serveur de l'application a rencontré un problème (code ${http[1]}). Réessaie dans un instant.`;
  return msg;
}
