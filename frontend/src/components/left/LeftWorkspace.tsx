// Panneau de gauche : six modules (Médias, IA, Audio, Texte, Effets, Projet) derrière huit valeurs de `LeftTab`.
// La barre de modules est dans la coque (shell/LeftRail) ; ici, le contenu du module choisi.
import type { LeftTab } from "../../store";
import { AIModule } from "./AIModule";
import { AudioModule } from "./AudioModule";
import { EffectsModule } from "./EffectsModule";
import { MediaModule } from "./MediaModule";
import { ProjectModule } from "./ProjectModule";
import { TextModule } from "./TextModule";
import "../../styles/left.css";

export function LeftWorkspace({ tab }: { tab: LeftTab }) {
  return (
    <div className="lw" data-tab={tab}>
      {tab === "rushes" && <MediaModule />}
      {tab === "ia" && <AIModule />}
      {tab === "audio" && <AudioModule />}
      {tab === "texte" && <TextModule />}
      {tab === "effets" && <EffectsModule />}
      {(tab === "versions" || tab === "modeles" || tab === "marque") && <ProjectModule tab={tab} />}
    </div>
  );
}
