// Étapes nommées des tâches longues (le serveur pose `job.stage`) : la réalisation en direct les affiche
// comme une liste à cocher. Une étape absente côté serveur (ex. transcription sans Whisper) est cochée en passant.
export interface Stage { id: string; label: string }

const S = (id: string, label: string): Stage => ({ id, label });

export const MONTAGE_STAGES: Stage[] = [
  S("analyse", "Analyse des rushes"),
  S("transcription", "Transcription"),
  S("reperage", "Repérage des moments forts"),
  S("brief", "Stratégie et direction créative"),
  S("montage", "Montage"),
  S("habillage", "Habillage"),
  S("calage", "Calage sur la voix et le rythme"),
  S("relecture", "Relecture et contrôle qualité"),
  S("verification", "Vérification rôle par rôle"),
  S("pret", "Montage prêt"),
];

export const STAGES: Record<string, Stage[]> = {
  montage: MONTAGE_STAGES,
  demo: [S("rendu", "Rendu du clip de démo"), S("import", "Import du clip"), ...MONTAGE_STAGES],
  directions: [S("analyse", "Analyse des rushes"), S("transcription", "Transcription"), S("directions", "3 directions"), S("pret", "Prêt")],
  shorts: [S("analyse", "Analyse des rushes"), S("reperage", "Passages les plus partageables"), S("shorts", "Montage des shorts"), S("pret", "Prêt")],
};

/** Types de tâches suivis en direct dans le lecteur. */
export const LIVE_KINDS = Object.keys(STAGES);

/** Position de l'étape courante dans la liste (-1 : pas encore commencé ou étape inconnue). */
export function stageIndex(kind: string, stage: string | null | undefined): number {
  if (!stage) return -1;
  return (STAGES[kind] ?? []).findIndex((s) => s.id === stage);
}
