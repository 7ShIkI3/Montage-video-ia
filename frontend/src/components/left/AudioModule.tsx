// Module Audio : voix off au micro et sons, musique de fond, voix de synthèse, doublage.
import { useRef, useState } from "react";
import { api } from "../../api";
import { Icon } from "../../icons";
import { useStore } from "../../store";
import { AddSoundButton } from "../AudioTools";
import { MusicLibraryButton } from "../Library";
import { AIVoiceoverPanel, DubPanel } from "../Lot3";
import { EmptyHint } from "../ui/EmptyHint";
import { ModuleFrame } from "./ModuleFrame";

type Sub = "voix" | "musique" | "synthese" | "doublage";

/** La voix off s'enregistre depuis le bouton de la barre de la timeline (instance unique) : ce bouton le déclenche. */
function triggerVoiceOver() {
  const btn = document.querySelector<HTMLButtonElement>("[data-voiceover]");
  if (btn) btn.click();
  else useStore.getState().notify("Barre de la timeline indisponible");
}

function MusicPane() {
  const project = useStore((s) => s.project);
  const tl = useStore((s) => s.timeline);
  const file = useRef<HTMLInputElement>(null);
  if (!project || !tl) return null;
  const { setTimelineProps, notify, openRight } = useStore.getState();
  const track = tl.music.track;
  return (
    <div className="lw-stack">
      <p className="muted small">La musique de fond du montage : un morceau de ta bibliothèque, une musique libre de droits ou un fichier à toi.</p>
      <p className="lw-now">Musique actuelle : <b>{track ? track.split(/[\\/]/).pop() : "aucune"}</b></p>
      <MusicLibraryButton label="Bibliothèque, musiques libres, lien…" className="small-btn lw-wide-btn" />
      <button type="button" className="small-btn lw-wide-btn" onClick={() => file.current?.click()}><Icon name="upload" size={13} />Importer un fichier audio</button>
      <input ref={file} type="file" accept="audio/*" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        try {
          setTimelineProps({ music: await api.uploadMusic(project.id, f) });
        } catch (err) { notify((err as Error).message); }
      }} />
      {track && <button type="button" className="small-btn lw-wide-btn" onClick={() => setTimelineProps({ music: { ...tl.music, track: null } })}><Icon name="x" size={13} />Retirer la musique</button>}
      <p className="muted small">Volume et baisse automatique sous la voix : <button type="button" className="link-like" onClick={() => openRight("audio")}>onglet Mixage</button>.</p>
    </div>
  );
}

export function AudioModule() {
  const tl = useStore((s) => s.timeline);
  const [sub, setSub] = useState<Sub>("voix");
  return (
    <ModuleFrame module="audio" active={sub} onSelect={(id) => setSub(id as Sub)}>
      {sub === "voix" && (
        <div className="lw-stack">
          <p className="muted small">Enregistre ta voix pendant que la vidéo défile depuis le curseur, ou pose un son sur une piste audio.</p>
          <button type="button" className="small-btn lw-wide-btn" onClick={triggerVoiceOver}><Icon name="mic" size={14} />Voix off au micro</button>
          <p className="muted small">Démarre ou arrête l'enregistrement ; l'état s'affiche dans la barre de la timeline.</p>
          <div className="lw-stack-btn"><AddSoundButton label="Importer un son" /></div>
          <p className="muted small">Bruitage, musique ou voix enregistrée ailleurs : le son se pose au curseur sur la première piste audio libre.</p>
        </div>
      )}
      {sub === "musique" && <MusicPane />}
      {sub === "synthese" && (tl ? <AIVoiceoverPanel /> : <EmptyHint icon="mic" text="Le montage se charge…" />)}
      {sub === "doublage" && (tl ? <DubPanel /> : <EmptyHint icon="mic" text="Le montage se charge…" />)}
    </ModuleFrame>
  );
}
