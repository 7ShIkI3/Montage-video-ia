// Module Projet : versions du montage (et shorts), modèles de style, identité de marque.
// La sous-section est portée par `leftTab` lui-même : la palette de commandes peut donc viser « Modèles » ou « Marque » directement.
import type { LeftTab } from "../../store";
import { useStore } from "../../store";
import { BrandPanel, TemplatesPanel, VersionsPanel } from "../Studio";
import { ModuleFrame } from "./ModuleFrame";

export function ProjectModule({ tab }: { tab: "versions" | "modeles" | "marque" }) {
  return (
    <ModuleFrame module="projet" active={tab} onSelect={(id) => useStore.getState().setLeftTab(id as LeftTab)}>
      {tab === "versions" && <VersionsPanel />}
      {tab === "modeles" && <TemplatesPanel />}
      {tab === "marque" && <BrandPanel />}
    </ModuleFrame>
  );
}
