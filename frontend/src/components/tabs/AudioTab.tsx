// Onglet Audio & voix : mixage, musique de fond, pistes audio, doublage, voix de synthèse, voix off au micro.
import { fmtTime, MixGroup, Mixer } from "../../api";
import { useStore } from "../../store";
import { Icon } from "../../icons";
import { AddSoundButton, VoiceOverButton } from "../AudioTools";
import { AIVoiceoverPanel, DubPanel } from "../Lot3";
import { MusicSection } from "../RightPanel";

const MIX_GROUPS: { id: MixGroup; label: string; hint: string }[] = [
  { id: "rushes", label: "Son des plans", hint: "Le son des vidéos du montage" },
  { id: "music", label: "Musique", hint: "La musique de fond" },
  { id: "sfx", label: "Bruitages", hint: "Les effets sonores posés sur la timeline" },
  { id: "tracks", label: "Pistes audio", hint: "Voix off, sons importés et doublage" },
];
const NEUTRAL_MIX: Mixer = Object.fromEntries(MIX_GROUPS.map((g) => [g.id, { volume: 1, mute: false }])) as Mixer;

/** Mixeur : un volume (0 à 200 %) et un bouton muet par groupe de sons. Appliqué à l'aperçu et à l'export. */
function MixerSection() {
  const mix = useStore((s) => s.timeline?.mix) ?? NEUTRAL_MIX;
  const { setTimelineProps } = useStore.getState();
  const set = (g: MixGroup, patch: Partial<Mixer[MixGroup]>) => {
    // Lu dans le store au moment du geste, pas au rendu : deux réglages rapprochés ne s'écrasent pas.
    const cur = useStore.getState().timeline?.mix ?? NEUTRAL_MIX;
    setTimelineProps({ mix: { ...NEUTRAL_MIX, ...cur, [g]: { ...NEUTRAL_MIX[g], ...cur[g], ...patch } } });
  };
  return (
    <div className="fx-section mixer">
      <div className="fx-title">Mixage</div>
      {MIX_GROUPS.map(({ id, label, hint }) => {
        const ch = { ...NEUTRAL_MIX[id], ...mix[id] };
        return (
          <div key={id} className={`mixer-row ${ch.mute ? "muted-row" : ""}`} title={hint}>
            <span className="mixer-name">{label}</span>
            <input type="range" min={0} max={2} step={0.05} value={ch.volume} aria-label={`Volume : ${label}`}
              onChange={(e) => set(id, { volume: +e.target.value })} />
            <span className="mixer-val">{Math.round(ch.volume * 100)} %</span>
            <button className={`small-btn mute-btn ${ch.mute ? "on" : ""}`} aria-pressed={ch.mute}
              title={ch.mute ? "Remettre le son" : "Couper ce groupe"} onClick={() => set(id, { mute: !ch.mute })}>
              <Icon name="volume" size={13} />{ch.mute ? "Muet" : "Son"}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function AudioTab() {
  const tl = useStore((s) => s.timeline);
  const item = useStore((s) => s.item);
  const { selectItem, setPlayhead, setRightTab } = useStore.getState();
  if (!tl) return null;
  const audio = [...(tl.audio || [])].sort((a, b) => a.lane - b.lane || a.start - b.start);
  return (
    <section className="panel inspector">
      <header><h3>Audio et voix</h3></header>
      <MixerSection />
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
