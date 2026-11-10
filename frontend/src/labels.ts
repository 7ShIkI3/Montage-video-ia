// Source unique des libellés de navigation : les rails, la palette de commandes, les titres de panneau et les raccourcis la lisent.
import type { IconName } from "./icons";
import type { LeftTab, RightTab } from "./store";

export type LeftModuleId = "medias" | "ia" | "audio" | "texte" | "effets" | "projet";

/** Sous-section d'un module (colonne `.sub`). Dans Projet, chaque sous-section est un `LeftTab` ; ailleurs, un état local du module. */
export interface LeftSub { id: string; label: string; tab?: LeftTab }

export interface LeftModule {
  id: LeftModuleId;
  /** Libellé du bouton de la barre de modules et titre du module. */
  label: string;
  icon: IconName;
  /** Onglet ouvert par un clic sur le module. */
  tab: LeftTab;
  /** Tous les `LeftTab` que le module couvre. */
  tabs: LeftTab[];
  /** Vide pour IA : accordéons dans le corps. */
  subs: LeftSub[];
}

/** Six modules pour huit valeurs de `LeftTab` : Projet couvre `versions`, `modeles` et `marque`. */
export const LEFT_MODULES: LeftModule[] = [
  { id: "medias", label: "Médias", icon: "folder", tab: "rushes", tabs: ["rushes"],
    subs: [{ id: "projet", label: "Projet" }, { id: "importer", label: "Importer" }, { id: "bibliotheque", label: "Bibliothèque" }] },
  { id: "ia", label: "IA", icon: "wand", tab: "ia", tabs: ["ia"], subs: [] },
  { id: "audio", label: "Audio", icon: "music", tab: "audio", tabs: ["audio"],
    subs: [{ id: "voix", label: "Voix off et sons" }, { id: "musique", label: "Musique" }, { id: "synthese", label: "Voix de synthèse" }, { id: "doublage", label: "Doublage" }] },
  { id: "texte", label: "Texte", icon: "type", tab: "texte", tabs: ["texte"],
    subs: [{ id: "titres", label: "Titres" }, { id: "soustitres", label: "Sous-titres" }] },
  { id: "effets", label: "Effets", icon: "zap", tab: "effets", tabs: ["effets"],
    subs: [{ id: "effets", label: "Effets" }, { id: "transitions", label: "Transitions" }, { id: "impacts", label: "Impacts 3D" }, { id: "bruitages", label: "Bruitages" }, { id: "images", label: "Images" }] },
  { id: "projet", label: "Projet", icon: "layers", tab: "versions", tabs: ["versions", "modeles", "marque"],
    subs: [{ id: "versions", label: "Versions", tab: "versions" }, { id: "modeles", label: "Modèles", tab: "modeles" }, { id: "marque", label: "Marque", tab: "marque" }] },
];

/** Module qui porte un onglet gauche (toujours défini : les huit valeurs sont couvertes). */
export function leftModuleOf(tab: LeftTab): LeftModule {
  return LEFT_MODULES.find((m) => m.tabs.includes(tab)) ?? LEFT_MODULES[0];
}

/** Libellé d'un onglet gauche pour la palette : celui du module, précisé par la sous-section dans Projet. */
export function leftTabLabel(tab: LeftTab): string {
  const m = leftModuleOf(tab);
  const sub = m.subs.find((x) => x.tab === tab);
  return sub ? `${m.label} : ${sub.label}` : m.label;
}

export interface RightTabInfo {
  id: RightTab;
  /** Libellé du rail (10,5 px sous l'icône). */
  label: string;
  /** Titre du panneau (`tab-head`). */
  title: string;
  icon: IconName;
  /** Touche d'ouverture, de 1 à 9. */
  key: number;
}

/** Onglets du panneau droit, dans l'ordre des touches 1 à 9 (même ordre que `RIGHT_TAB_ORDER`). */
export const RIGHT_TABS: RightTabInfo[] = [
  { id: "consigne", label: "Chat", title: "Réalisateur", icon: "message", key: 1 },
  { id: "montage", label: "Film", title: "Fiche du film", icon: "clapper", key: 2 },
  { id: "plan", label: "Plan", title: "Plan", icon: "crosshair", key: 3 },
  { id: "habillage", label: "Look", title: "Look et habillage", icon: "sparkles", key: 4 },
  { id: "audio", label: "Mixage", title: "Mixage audio", icon: "volume", key: 5 },
  { id: "qualite", label: "Qualité", title: "Contrôle qualité", icon: "shield-check", key: 6 },
  { id: "export", label: "Export", title: "Export", icon: "download", key: 7 },
  { id: "texte", label: "Script", title: "Transcription", icon: "align-left", key: 8 },
  { id: "recherche", label: "Web", title: "Recherche web", icon: "globe", key: 9 },
];

export function rightTabTitle(id: RightTab): string {
  return RIGHT_TABS.find((t) => t.id === id)?.title ?? "";
}

/** `title` du bouton du rail droit : « Libellé (N) ». */
export function rightTabHint(t: RightTabInfo): string {
  return `${t.label} (${t.key})`;
}
