// Pellicules des rushes : une image par rush qui contient toutes ses vignettes en grille (voir `media_filmstrip` côté serveur).
// Partagées par la timeline (vignettes sous les plans) et par le module Médias (défilement au survol) : un seul cache, une seule file.

/** Une pellicule : l'image (URL d'objet), le nombre de vignettes, les colonnes de la grille et la taille d'une vignette. */
export interface Strip { url: string; count: number; cols: number; tw: number; th: number }

const stripLoads: Record<string, Promise<Strip | null>> = {};
const stripReady: Record<string, Strip | null> = {};
let stripQueue: Promise<unknown> = Promise.resolve();

/** Une pellicule à la fois : le serveur les fabrique une par une, et des requêtes en attente occuperaient
 *  les connexions du navigateur dont la lecture et la sauvegarde ont besoin. */
export function loadStrip(pid: string, mediaId: string): Promise<Strip | null> {
  const key = `${pid}/${mediaId}`;
  if (!stripLoads[key]) {
    const load = stripQueue.then(async (): Promise<Strip | null> => {
      const r = await fetch(`/api/projects/${pid}/media/${mediaId}/filmstrip`);
      if (r.status === 404) return null;  // rush sans image : rien à montrer, on ne redemande pas
      if (!r.ok) throw new Error(`pellicule ${r.status}`);
      const [tw, th] = (r.headers.get("X-Strip-Tile") ?? "128x72").split("x").map(Number);
      const count = +(r.headers.get("X-Strip-Count") ?? 0);
      const cols = +(r.headers.get("X-Strip-Cols") ?? 16);
      if (!count || !cols || !tw || !th) return null;
      return { url: URL.createObjectURL(await r.blob()), count, cols, tw, th };
    });
    stripQueue = load.catch(() => undefined);
    stripLoads[key] = load
      .then((st) => (stripLoads[key] === wrapped ? (stripReady[key] = st) : st))
      .catch(() => { if (stripLoads[key] === wrapped) delete stripLoads[key]; return null; });  // panne ou rush absent : on réessaiera
    const wrapped = stripLoads[key];
  }
  return stripLoads[key];
}

/** La pellicule si elle est déjà chargée, sinon null. Ne déclenche aucune requête. */
export function peekStrip(pid: string, mediaId: string): Strip | null {
  return stripReady[`${pid}/${mediaId}`] ?? null;
}

/** Oublie les pellicules chargées et libère leurs images (changement de projet, sortie de l'éditeur). */
export function dropStrips(): void {
  for (const key of Object.keys(stripLoads)) delete stripLoads[key];
  for (const key of Object.keys(stripReady)) {
    const st = stripReady[key];
    if (st) URL.revokeObjectURL(st.url);
    delete stripReady[key];
  }
}
