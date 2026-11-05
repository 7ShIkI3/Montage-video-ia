// Onglet Audio & voix : musique de fond, pistes audio, doublage, voix de synthèse, voix off au micro.
import { fmtTime } from "../../api";
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { AddSoundButton, VoiceOverButton } from "../AudioTools";
import { AIVoiceoverPanel, DubPanel } from "../Lot3";
import { MusicSection } from "../RightPanel";

export function AudioTab() {
  const tl = useStore((s) => s.timeline);
  const item = useStore((s) => s.item);
  const { selectItem, setPlayhead, setRightTab } = useStore.getState();
  if (!tl) return null;
  const audio = [...(tl.audio || [])].sort((a, b) => a.lane - b.lane || a.start - b.start);
  return (
    <section className="panel inspector">
      <header><h3>Audio et voix</h3></header>
      <MusicSection />
      <div className="fx-section">
        <div className="fx-title">Pistes audio</div>
        <div className="add-grid">
          <VoiceOverButton />
          <AddSoundButton />
        </div>
        {audio.length > 0 && (
          <ul className="audio-list">
            {audio.map((a) => (
              <li key={a.id} className={item?.kind === "audio" && item.id === a.id ? "sel" : ""}>
                <button className="link" onClick={() => { selectItem({ kind: "audio", id: a.id }); setPlayhead(a.start); setRightTab("plan"); }}>
                  <Icon name={a.kind === "voix" ? "mic" : a.kind === "musique" ? "music" : "volume"} size={13} />
                  <span>{a.label || a.asset}</span>
                  <small className="muted">Audio {a.lane + 1} · {fmtTime(a.start, true)} · {(a.src_out - a.src_in).toFixed(1)} s · {Math.round(a.volume * 100)} %</small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <DubPanel />
      <AIVoiceoverPanel />
    </section>
  );
}
