// Réglages du plan sélectionné, en cinq sous-onglets : Vidéo, Audio, Vitesse, Animation, Ajuster.
// Un en-tête (vignette, nom, note du réalisateur, raison du choix) reste visible au-dessus des réglages.
import { useState } from "react";
import { api, Clip, ColorAdjust, fmtTime } from "../../api";
import { hasColor } from "../../fxPreview";
import { Icon } from "../../icons";
import { clipDur, useStore } from "../../store";
import { MotionEditor } from "../Motion";
import { Segmented } from "../ui/Segmented";
import { ChipList, RangeField, signedPct, toggleId } from "./fields";

type SubTab = "video" | "audio" | "vitesse" | "animation" | "ajuster";

const SUBTABS: { id: SubTab; label: string }[] = [
  { id: "video", label: "Vidéo" }, { id: "audio", label: "Audio" }, { id: "vitesse", label: "Vitesse" },
  { id: "animation", label: "Animation" }, { id: "ajuster", label: "Ajuster" },
];

/** Anneau de la note du réalisateur : 0 à 100, coloré selon le niveau (70 et plus : bon, 50 et plus : moyen). */
function ScoreRing({ value }: { value: number }) {
  const r = 13;
  const len = 2 * Math.PI * r;
  const tone = value >= 70 ? "good" : value >= 50 ? "mid" : "bad";
  return (
    <span className={`score-ring ${tone}`} role="img" aria-label={`Note du réalisateur : ${value} sur 100`}>
      <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden="true">
        <circle className="ring-bg" cx={16} cy={16} r={r} fill="none" strokeWidth={3} />
        <circle className="ring-fg" cx={16} cy={16} r={r} fill="none" strokeWidth={3} strokeLinecap="round"
          strokeDasharray={`${(len * value) / 100} ${len}`} transform="rotate(-90 16 16)" />
      </svg>
      <b>{value}</b>
    </span>
  );
}

export function ClipInspector({ c }: { c: Clip }) {
  const project = useStore((s) => s.project);
  const segments = useStore((s) => s.segments);
  const multi = useStore((s) => s.selectedIds.length);
  const [sub, setSub] = useState<SubTab>("video");
  if (!project) return null;
  const media = project.media.find((m) => m.id === c.media_id);
  const quote = segments.find((s) => s.id === c.segment_id)?.text ?? "";
  const score = Math.round(c.score * 100);

  return (
    <div className="rw clip-inspector">
      {multi > 1 && (
        <p className="multi-note" data-testid="multi-note">
          <b>{multi} plans sélectionnés.</b> Zoom, volume, vitesse, fondus, transition, effets et couleurs s'appliquent aux <b>{multi} plans</b>.
          L'entrée, la sortie et les images clés ne changent que ce plan.
        </p>
      )}
      <div className="ci-sticky">
        <div className="ci-head">
          {media?.proxy
            ? <img className="ci-thumb" src={api.thumbUrl(project.id, c.media_id)} alt="" />
            : <span className="ci-thumb ci-thumb-empty"><Icon name="film" size={18} /></span>}
          <div className="ci-meta">
            <b className="ci-name">{media?.filename ?? "Plan"}</b>
            <span className="muted small mono">{fmtTime(clipDur(c), true)}{c.speed !== 1 ? ` · ×${c.speed}` : ""}</span>
          </div>
        </div>
        <div className="ci-score"><ScoreRing value={score} /><span className="small">Note du réalisateur /100</span></div>
        <p className="ci-why" title={c.reason || undefined}><b>Pourquoi ce plan est gardé :</b> {c.reason || "—"}</p>
        <Segmented<SubTab> value={sub} onChange={setSub} ariaLabel="Réglages du plan" options={SUBTABS} />
      </div>
      <div className="ci-body" role="tabpanel">
        {sub === "video" && <VideoPart c={c} quote={quote} />}
        {sub === "audio" && <AudioPart c={c} />}
        {sub === "vitesse" && <MotionEditor c={c} part="vitesse" />}
        {sub === "animation" && <MotionEditor c={c} part="cles" />}
        {sub === "ajuster" && <ColorSection c={c} />}
      </div>
    </div>
  );
}

function VideoPart({ c, quote }: { c: Clip; quote: string }) {
  const catalog = useStore((s) => s.catalog);
  const { updateClip, openLeft } = useStore.getState();
  const labelOf = (id: string) => catalog?.video_effects.find((o) => o.id === id)?.label ?? id;
  return (
    <>
      {quote && <p className="quote">« {quote} »</p>}
      <div className="field-row">
        <label>Entrée (s)
          <input type="number" step={0.05} value={c.src_in.toFixed(2)}
            onChange={(e) => useStore.getState().trimClip(c.id, "in", +e.target.value)} />
        </label>
        <label>Sortie (s)
          <input type="number" step={0.05} value={c.src_out.toFixed(2)}
            onChange={(e) => useStore.getState().trimClip(c.id, "out", +e.target.value)} />
        </label>
      </div>
      <RangeField label="Zoom" min={1} max={1.5} step={0.01} value={c.zoom} display={`${Math.round((c.zoom - 1) * 100)} %`}
        onChange={(v) => updateClip(c.id, { zoom: v })} />
      {catalog && (
        <label>Transition d'entrée
          <select value={c.transition} onChange={(e) => updateClip(c.id, { transition: e.target.value })}>
            {catalog.transitions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </label>
      )}
      {catalog && c.transition !== "aucune" && (
        <RangeField label="Durée de la transition" min={0.1} max={1.5} step={0.05} value={c.transition_duration}
          display={`${c.transition_duration.toFixed(2)} s`} onChange={(v) => updateClip(c.id, { transition_duration: v })} />
      )}
      <div className="fx-title">Effets actifs</div>
      {c.effects.length > 0 ? (
        <div className="fx-chips">
          {c.effects.map((id) => (
            <button key={id} type="button" className="fx-chip on" aria-label={`Retirer l'effet ${labelOf(id)}`}
              onClick={() => updateClip(c.id, { effects: c.effects.filter((x) => x !== id) })}>
              {labelOf(id)}<Icon name="x" size={11} strokeWidth={2.4} />
            </button>
          ))}
        </div>
      ) : <p className="muted small">Aucun effet sur ce plan.</p>}
      <button type="button" className="small-btn ci-add" onClick={() => openLeft("effets")}>
        <Icon name="plus" size={13} strokeWidth={2.4} />Ajouter des effets
      </button>
    </>
  );
}

function AudioPart({ c }: { c: Clip }) {
  const catalog = useStore((s) => s.catalog);
  const { updateClip } = useStore.getState();
  const muted = c.audio_effects.includes("muet");
  return (
    <>
      <RangeField label="Volume" min={0} max={2} step={0.05} value={c.volume} display={`${Math.round(c.volume * 100)} %`}
        onChange={(v) => updateClip(c.id, { volume: v })} />
      <label className="inline">
        <input type="checkbox" className="switch" checked={muted}
          onChange={(e) => updateClip(c.id, { audio_effects: e.target.checked ? [...c.audio_effects.filter((x) => x !== "muet"), "muet"] : c.audio_effects.filter((x) => x !== "muet") })} />
        Couper le son de ce plan
      </label>
      <div className="field-row">
        <label>Fondu d'entrée (s)
          <input type="number" min={0} max={5} step={0.1} value={c.fade_in ?? 0}
            onChange={(e) => updateClip(c.id, { fade_in: Math.max(0, Math.min(5, +e.target.value || 0)) })} />
        </label>
        <label>Fondu de sortie (s)
          <input type="number" min={0} max={5} step={0.1} value={c.fade_out ?? 0}
            onChange={(e) => updateClip(c.id, { fade_out: Math.max(0, Math.min(5, +e.target.value || 0)) })} />
        </label>
      </div>
      {catalog && (
        <>
          <div className="fx-title">Effets sur la voix</div>
          <ChipList opts={catalog.audio_effects} value={c.audio_effects}
            onToggle={(id) => updateClip(c.id, { audio_effects: toggleId(c.audio_effects, id) })} />
        </>
      )}
    </>
  );
}

const COLOR_SLIDERS: { key: keyof ColorAdjust; label: string; hint: string }[] = [
  { key: "exposure", label: "Exposition", hint: "Plus sombre ou plus lumineux" },
  { key: "contrast", label: "Contraste", hint: "Écart entre les clairs et les ombres" },
  { key: "saturation", label: "Saturation", hint: "Intensité des couleurs (tout à gauche : noir et blanc)" },
  { key: "temperature", label: "Température", hint: "Du froid (bleuté) au chaud (orangé)" },
];

/** Correction colorimétrique du plan : 4 curseurs de -100 % à +100 %, 0 = image d'origine. */
function ColorSection({ c }: { c: Clip }) {
  const { updateClip } = useStore.getState();
  const cur: ColorAdjust = c.color ?? { exposure: 0, contrast: 0, saturation: 0, temperature: 0 };
  return (
    <>
      <div className="fx-title color-title">
        Correction des couleurs
        <button type="button" className="small-btn" disabled={!hasColor(c.color)} onClick={() => updateClip(c.id, { color: null })}>Réinitialiser</button>
      </div>
      {COLOR_SLIDERS.map(({ key, label, hint }) => (
        <div key={key}>
          <RangeField label={label} min={-1} max={1} step={0.01} value={cur[key]} display={signedPct(cur[key])}
            onChange={(v) => updateClip(c.id, { color: { ...cur, [key]: v } })} />
          <p className="field-hint">{hint}</p>
        </div>
      ))}
    </>
  );
}
