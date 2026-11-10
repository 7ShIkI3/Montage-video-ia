// Onglet Chat : le réalisateur (Discuter) et les commentaires de montage, en deux sous-onglets.
// Les deux vues restent montées : la vue inactive est seulement masquée (`hidden`), si bien que `#comment-box`
// est toujours dans le DOM et que la touche C peut lui donner le focus.
import { useStore } from "../../store";
import { CommentsPanel } from "../RightPanel";
import { DirectorChat } from "../DirectorChat";
import { Segmented } from "../ui/Segmented";

type View = "chat" | "comments";

export function ChatTab() {
  const view = useStore((s) => s.assistantView);
  const setView = useStore((s) => s.setAssistantView);
  const openCount = useStore((s) => (s.timeline?.comments ?? []).filter((c) => c.status === "ouvert").length);
  return (
    <div className="rw chat-tab">
      <Segmented<View> value={view} onChange={setView} ariaLabel="Réalisateur"
        options={[{ id: "chat", label: "Discuter" }, { id: "comments", label: "Commentaires", badge: openCount > 0 ? openCount : undefined }]} />
      <div className="chat-view" hidden={view !== "chat"}><DirectorChat visible={view === "chat"} /></div>
      <div className="chat-view" hidden={view !== "comments"}><CommentsPanel /></div>
    </div>
  );
}
