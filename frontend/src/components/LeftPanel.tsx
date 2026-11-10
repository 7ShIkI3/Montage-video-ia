// Panneaux historiques de la colonne de gauche. Le contenu vit désormais dans les modules (components/left/) ;
// ces noms restent exportés pour ne rien casser : `MediaPanel` est le module Médias, `AIPanel` le contenu du module IA.
export { MediaModule as MediaPanel } from "./left/MediaModule";
export { AIPanel } from "./left/AIModule";
