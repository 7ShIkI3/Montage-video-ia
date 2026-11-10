// Contenu du panneau de droite : un onglet à la fois, chacun avec un seul conteneur qui défile (`.tab-body`, fourni par la coque).
// Le titre de l'onglet est dans le `tab-head` de la coque : les onglets n'ont plus d'en-tête propre.
import "../../styles/right.css";
import type { RightTab } from "../../store";
import { ResearchPanel } from "../Research";
import { TranscriptPanel } from "../RightPanel";
import { AudioTab } from "../tabs/AudioTab";
import { ExportTab } from "../tabs/ExportTab";
import { HabillageTab } from "../tabs/HabillageTab";
import { MontageTab } from "../tabs/MontageTab";
import { PlanTab } from "../tabs/PlanTab";
import { QualiteTab } from "../tabs/QualiteTab";
import { ChatTab } from "./ChatTab";

export function RightWorkspace({ tab }: { tab: RightTab }) {
  return (
    <div className="right-ws" data-tab={tab}>
      {tab === "consigne" && <ChatTab />}
      {tab === "montage" && <MontageTab />}
      {tab === "plan" && <PlanTab />}
      {tab === "habillage" && <HabillageTab />}
      {tab === "audio" && <AudioTab />}
      {tab === "qualite" && <QualiteTab />}
      {tab === "export" && <ExportTab />}
      {tab === "texte" && <TranscriptPanel />}
      {tab === "recherche" && <ResearchPanel />}
    </div>
  );
}
